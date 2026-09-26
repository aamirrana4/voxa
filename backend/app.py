"""
Voxa backend — secure proxy between the Voxa website and the ai33.pro API,
plus user accounts, per-user credit balances, usage metering, and an admin panel.

SECURITY:
- The ai33 API key is read ONLY from the AI33_API_KEY environment variable.
  It is never written to disk, never logged, and never sent to the browser.
- Passwords are hashed with werkzeug; sessions are signed cookies.
- Every generation endpoint requires login and a sufficient credit balance.
- /api/credits (the owner's Ai33 pool) and /api/admin/* require an admin user.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from functools import wraps

import requests
from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from models import Job, Ledger, SessionLocal, User, init_db
import pricing

AI33_BASE = "https://api.ai33.pro"
API_KEY = os.environ.get("AI33_API_KEY", "").strip()

ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAIL", "").split(",")
    if e.strip()
}
try:
    SIGNUP_BONUS = float(os.environ.get("SIGNUP_BONUS", "1000") or 1000)
except ValueError:
    SIGNUP_BONUS = 1000.0

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

app = Flask(__name__)
# 350MB cap covers the largest supported uploads (voice changer / dubbing).
app.config["MAX_CONTENT_LENGTH"] = 350 * 1024 * 1024

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SECRET_KEY not set — generated a random per-boot key. "
          "Sessions will not survive restarts. Set SECRET_KEY env var in production.")
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"

init_db()

# ------------------------------------------------------------ helpers ----
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _db():
    return SessionLocal()


def current_user():
    """Return {'id','email','is_admin'} for the logged-in user, else None."""
    uid = session.get("uid")
    if not uid:
        return None
    s = _db()
    try:
        u = s.query(User).filter(User.id == uid).first()
        if not u:
            return None
        return {"id": u.id, "email": u.email, "is_admin": bool(u.is_admin)}
    finally:
        s.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return jsonify({"success": False, "error": "login_required"}), 401
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return jsonify({"success": False, "error": "login_required"}), 401
        if not u["is_admin"]:
            return jsonify({"success": False, "error": "forbidden"}), 403
        return fn(*a, **kw)
    return wrapper


# Tiny in-memory rate limiter for auth endpoints (per IP).
_rate_buckets: dict[str, list[float]] = {}


def _rate_ok(key: str, limit: int = 30, window: int = 300) -> bool:
    now = time.time()
    hits = [t for t in _rate_buckets.get(key, []) if now - t < window]
    if len(hits) >= limit:
        return False
    hits.append(now)
    _rate_buckets[key] = hits
    return True


def _balance_of(user_id: int):
    s = _db()
    try:
        u = s.query(User).filter(User.id == user_id).first()
        return float(u.credit_balance) if u else None
    finally:
        s.close()


def _user_json(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "is_admin": bool(u.is_admin),
        "credits": float(u.credit_balance),
    }


# ---------------------------------------------------------------- auth ----
@app.post("/api/auth/signup")
def signup():
    if not _rate_ok("signup:" + (request.remote_addr or "?"), 20, 600):
        return jsonify({"success": False, "error": "Too many attempts, try later."}), 429
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if not EMAIL_RE.match(email):
        return jsonify({"success": False, "error": "Please enter a valid email address."}), 400
    if len(password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters."}), 400
    s = _db()
    try:
        if s.query(User).filter(User.email == email).first():
            return jsonify({"success": False, "error": "This email is already registered. Try logging in."}), 400
        u = User(
            email=email,
            password_hash=generate_password_hash(password),
            is_admin=email in ADMIN_EMAILS,
            credit_balance=0.0,
        )
        s.add(u)
        s.flush()
        if SIGNUP_BONUS > 0:
            u.credit_balance = float(SIGNUP_BONUS)
            s.add(Ledger(user_id=u.id, delta=float(SIGNUP_BONUS),
                         balance_after=float(SIGNUP_BONUS),
                         reason="signup_bonus", meta="{}"))
        s.commit()
        session["uid"] = u.id
        return jsonify({"success": True, "user": _user_json(u)})
    finally:
        s.close()


@app.post("/api/auth/login")
def login():
    if not _rate_ok("login:" + (request.remote_addr or "?"), 30, 300):
        return jsonify({"success": False, "error": "Too many attempts, try later."}), 429
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    s = _db()
    try:
        u = s.query(User).filter(User.email == email).first()
        if not u or not check_password_hash(u.password_hash, password):
            return jsonify({"success": False, "error": "Wrong email or password."}), 401
        if email in ADMIN_EMAILS and not u.is_admin:
            u.is_admin = True
            s.commit()
        session["uid"] = u.id
        return jsonify({"success": True, "user": _user_json(u)})
    finally:
        s.close()


@app.post("/api/auth/logout")
def logout():
    session.pop("uid", None)
    return jsonify({"success": True})


@app.get("/api/auth/me")
def me():
    u = current_user()
    if not u:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "email": u["email"],
        "is_admin": u["is_admin"],
        "credits": _balance_of(u["id"]) or 0.0,
    })


# ------------------------------------------------------- upstream proxy --
def _need_key():
    if not API_KEY:
        return {"success": False,
                "error": "AI33_API_KEY is not set on the server."}, 500
    return None


def _proxy_json(method, path, payload=None, params=None, timeout=120):
    """Forward a JSON request to ai33. Returns (data_dict, status_code)."""
    err = _need_key()
    if err:
        return err
    try:
        resp = requests.request(
            method, AI33_BASE + path,
            headers={"xi-api-key": API_KEY},
            json=payload, params=params, timeout=timeout,
        )
    except requests.RequestException as exc:
        return {"success": False, "error": f"Upstream request failed: {exc}"}, 502
    try:
        data = resp.json()
    except ValueError:
        data = {"success": False, "error": "Upstream returned non-JSON response"}
    if not isinstance(data, dict):
        data = {"success": False, "error": "Unexpected upstream response", "data": data}
    return data, resp.status_code


def _proxy_formdata(path, timeout=300):
    """Forward a browser multipart/form-data request to ai33. Returns (dict, status)."""
    err = _need_key()
    if err:
        return err
    form = {k: v for k, v in request.form.items()}
    files = {}
    for key, storage in request.files.items():
        files[key] = (
            storage.filename or key,
            storage.stream,
            storage.mimetype or "application/octet-stream",
        )
    try:
        resp = requests.post(
            AI33_BASE + path,
            headers={"xi-api-key": API_KEY},
            data=form, files=files or None, timeout=timeout,
        )
    except requests.RequestException as exc:
        return {"success": False, "error": f"Upstream request failed: {exc}"}, 502
    try:
        data = resp.json()
    except ValueError:
        data = {"success": False, "error": "Upstream returned non-JSON response"}
    if not isinstance(data, dict):
        data = {"success": False, "error": "Unexpected upstream response"}
    return data, resp.status_code


def _upstream_ok(data: dict, status: int) -> bool:
    return (200 <= status < 300
            and isinstance(data, dict)
            and data.get("success", True) is not False)


# ------------------------------------------------------------ metering --
def _upload_bytes() -> int:
    total = 0
    for storage in request.files.values():
        try:
            stream = storage.stream
            pos = stream.tell()
            stream.seek(0, os.SEEK_END)
            total += stream.tell()
            stream.seek(pos)
        except Exception:
            if storage.content_length:
                total += storage.content_length
    return total


_image_models_cache = {"ts": 0.0, "credits": {}}


def _image_price_per(model_id: str) -> float:
    """Live per-image price from ai33's model list; fallback when unavailable."""
    now = time.time()
    if now - _image_models_cache["ts"] > 3600:
        data, status = _proxy_json("GET", "/v1i/models")
        if 200 <= status < 300:
            models = data.get("data") or data.get("models") or []
            creds = {}
            for m in models:
                if isinstance(m, dict) and m.get("model_id"):
                    try:
                        creds[m["model_id"]] = float(m.get("presented_credits") or 0)
                    except (TypeError, ValueError):
                        pass
            if creds:
                _image_models_cache["credits"] = creds
                _image_models_cache["ts"] = now
    try:
        return float(_image_models_cache["credits"].get(model_id)
                     or pricing.IMAGE_FALLBACK_PER_IMAGE)
    except (TypeError, ValueError):
        return pricing.IMAGE_FALLBACK_PER_IMAGE


def _estimate_cost(kind: str) -> float:
    if kind == "tts":
        return pricing.tts_cost(request.form.get("text", ""),
                                request.form.get("voice_id", ""))
    if kind == "dialogue":
        try:
            speakers = json.loads(request.form.get("speakers", "[]"))
        except (TypeError, ValueError):
            speakers = []
        return pricing.dialogue_cost(request.form.get("text", ""), speakers)
    if kind == "voice-clone":
        return pricing.CLONE_CREATE_FLAT
    if kind == "speech-to-text":
        return pricing.audio_mb_cost(_upload_bytes(), pricing.STT_PER_MB)
    if kind == "dubbing":
        return pricing.audio_mb_cost(_upload_bytes(), pricing.DUBBING_PER_MB)
    if kind == "voice-changer":
        return pricing.audio_mb_cost(_upload_bytes(), pricing.VOICE_CHANGER_PER_MB)
    if kind == "voice-isolate":
        return pricing.audio_mb_cost(_upload_bytes(), pricing.VOICE_ISOLATE_PER_MB)
    if kind == "sound-effect":
        return pricing.SFX_FLAT
    if kind == "music":
        try:
            n = int((request.get_json(silent=True) or {}).get("n", 1))
        except (TypeError, ValueError):
            n = 1
        return pricing.MUSIC_FLAT * max(1, min(n, 3))
    if kind == "image":
        try:
            count = int(request.form.get("generations_count", 1))
        except (TypeError, ValueError):
            count = 1
        return _image_price_per(request.form.get("model_id", "")) * max(1, min(count, 4))
    return 0.0


def _charge(user_id: int, kind: str, cost: float, task_id=None):
    """Deduct cost inside one transaction. Returns new balance, or None if the
    balance check failed at commit time (concurrent spend race)."""
    s = _db()
    try:
        u = s.query(User).filter(User.id == user_id).first()
        if not u or float(u.credit_balance) < cost:
            return None
        u.credit_balance = round(float(u.credit_balance) - cost, 2)
        s.add(Ledger(user_id=u.id, delta=-round(cost, 2),
                     balance_after=u.credit_balance, reason=kind,
                     meta=json.dumps({"task_id": task_id})))
        if task_id:
            s.add(Job(user_id=u.id, task_id=str(task_id), kind=kind,
                      cost=round(cost, 2), status="doing"))
        s.commit()
        return u.credit_balance
    except Exception:
        s.rollback()
        return None
    finally:
        s.close()


def metered(kind: str):
    """Route decorator: require login + balance, deduct on upstream success."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if not user:
                return jsonify({"success": False, "error": "login_required"}), 401
            try:
                cost = float(_estimate_cost(kind) or 0)
            except Exception:
                cost = 0.0
            balance = _balance_of(user["id"])
            if balance is None:
                return jsonify({"success": False, "error": "login_required"}), 401
            if balance < cost:
                return jsonify({"success": False, "error": "insufficient_credits",
                                "needed": cost, "balance": balance}), 402
            data, status = fn(*a, **kw)  # fn returns (data_dict, status_code)
            if _upstream_ok(data, status) and cost > 0:
                task_id = data.get("task_id") if isinstance(data, dict) else None
                _charge(user["id"], kind, cost, task_id)
            return jsonify(data), status
        return wrapper
    return deco


def _owned_job_or_error(user, task_id):
    """Return (job_dict, None) if the user may access the task, else (None, (resp, code))."""
    s = _db()
    try:
        job = s.query(Job).filter(Job.task_id == task_id).first()
        if not job:
            return None, (jsonify({"success": False, "error": "task_not_found"}), 404)
        if job.user_id != user["id"] and not user["is_admin"]:
            return None, (jsonify({"success": False, "error": "forbidden"}), 403)
        return {"id": job.id, "user_id": job.user_id, "task_id": job.task_id,
                "kind": job.kind}, None
    finally:
        s.close()


def _mark_job_status(task_id, data):
    status = (data or {}).get("status")
    if status not in ("doing", "done", "error"):
        return
    s = _db()
    try:
        job = s.query(Job).filter(Job.task_id == task_id).first()
        if job and job.status != status:
            job.status = status
            s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


# --------------------------------------------------------------- routes --
@app.get("/api/credits")
@admin_required
def credits():
    """Owner's Ai33 pool balance — admins only."""
    data, status = _proxy_json("GET", "/v1/credits")
    return jsonify(data), status


@app.get("/api/health")
@login_required
def health():
    data, status = _proxy_json("GET", "/v1/health-check")
    return jsonify(data), status


@app.get("/api/voices")
@login_required
def voices():
    params = {
        k: request.args[k]
        for k in ("provider", "search", "q", "page", "page_size", "limit", "filters")
        if k in request.args
    }
    data, status = _proxy_json("GET", "/v3/voices", params=params)
    return jsonify(data), status


@app.get("/api/task/<task_id>")
@login_required
def task_status(task_id):
    user = current_user()
    _, err = _owned_job_or_error(user, task_id)
    if err:
        return err
    data, status = _proxy_json("GET", f"/v1/task/{task_id}")
    _mark_job_status(task_id, data)
    return jsonify(data), status


@app.get("/api/tasks")
@login_required
def task_list():
    """Per-user history from our own ledger (never leaks other users' tasks)."""
    user = current_user()
    kind = request.args.get("type", "")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = min(50, max(1, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    s = _db()
    try:
        q = s.query(Job).filter(Job.user_id == user["id"])
        if kind:
            q = q.filter(Job.kind == kind)
        total = q.count()
        jobs = (q.order_by(Job.id.desc())
                 .offset((page - 1) * limit).limit(limit).all())
        items = [{
            "task_id": j.task_id,
            "id": j.task_id,
            "type": j.kind,
            "status": j.status,
            "cost": j.cost,
            "created_at": j.created_at.isoformat() if j.created_at else "",
        } for j in jobs]
    finally:
        s.close()
    return jsonify({"success": True, "data": items,
                    "pagination": {"page": page, "limit": limit, "total": total}})


@app.post("/api/task/delete")
@login_required
def task_delete():
    user = current_user()
    body = request.get_json(silent=True) or {}
    task_ids = body.get("task_ids") or ([body.get("task_id")] if body.get("task_id") else [])
    for tid in task_ids:
        _, err = _owned_job_or_error(user, str(tid))
        if err:
            return err
    data, status = _proxy_json("POST", "/v1/task/delete", payload=body)
    return jsonify(data), status


# ------------------------------------------------------- text-to-speech --
@app.post("/api/tts")
@login_required
@metered("tts")
def tts():
    return _proxy_formdata("/v3/text-to-speech")


@app.post("/api/dialogue")
@login_required
@metered("dialogue")
def dialogue():
    return _proxy_formdata("/v3/text-to-speech/dialogue")


# ---------------------------------------------------------- voice clone --
@app.post("/api/clone")
@login_required
@metered("voice-clone")
def clone():
    storage = request.files.get("audio_file")
    if storage and storage.content_length and storage.content_length > 10 * 1024 * 1024:
        return {"success": False, "error": "Audio file must be under 10MB."}, 413
    return _proxy_formdata("/v3/text-to-speech/voice-clone")


@app.delete("/api/clone/<voice_clone_id>")
@login_required
def clone_delete(voice_clone_id):
    data, status = _proxy_json("DELETE", f"/v3/text-to-speech/voice-clone/{voice_clone_id}")
    return jsonify(data), status


# ----------------------------------------------------------- audio tools --
@app.post("/api/stt")
@login_required
@metered("speech-to-text")
def stt():
    return _proxy_formdata("/v1/task/speech-to-text", timeout=600)


@app.post("/api/dubbing")
@login_required
@metered("dubbing")
def dubbing():
    return _proxy_formdata("/v1/task/dubbing", timeout=900)


@app.post("/api/voice-changer")
@login_required
@metered("voice-changer")
def voice_changer():
    return _proxy_formdata("/v1/task/voice-changer", timeout=900)


@app.post("/api/voice-isolate")
@login_required
@metered("voice-isolate")
def voice_isolate():
    return _proxy_formdata("/v1/task/voice-isolate", timeout=900)


@app.post("/api/sfx")
@login_required
@metered("sound-effect")
def sfx():
    return _proxy_json("POST", "/v1/task/sound-effect",
                       payload=request.get_json(silent=True) or {})


@app.post("/api/music")
@login_required
@metered("music")
def music():
    return _proxy_json("POST", "/v1m/task/music-generation",
                       payload=request.get_json(silent=True) or {}, timeout=300)


# --------------------------------------------------------------- image --
@app.get("/api/image/models")
@login_required
def image_models():
    data, status = _proxy_json("GET", "/v1i/models")
    return jsonify(data), status


@app.post("/api/image/price")
@login_required
def image_price():
    data, status = _proxy_json("POST", "/v1i/task/price",
                               payload=request.get_json(silent=True) or {})
    return jsonify(data), status


@app.post("/api/image")
@login_required
@metered("image")
def image_generate():
    return _proxy_formdata("/v1i/task/generate-image", timeout=600)


# --------------------------------------------------------------- admin --
@app.get("/api/admin/overview")
@admin_required
def admin_overview():
    pool = None
    data, status = _proxy_json("GET", "/v1/credits")
    if 200 <= status < 300 and isinstance(data.get("credits"), (int, float)):
        pool = data["credits"]
    s = _db()
    try:
        total_users = s.query(User).count()
        issued = sum(r[0] or 0 for r in
                     s.query(Ledger.delta).filter(Ledger.delta > 0).all())
        consumed = -sum(r[0] or 0 for r in
                        s.query(Ledger.delta).filter(Ledger.delta < 0).all())
    finally:
        s.close()
    return jsonify({"success": True, "pool_credits": pool,
                    "total_users": total_users,
                    "credits_issued": issued, "credits_consumed": consumed})


@app.get("/api/admin/users")
@admin_required
def admin_users():
    q = (request.args.get("q", "") or "").strip().lower()
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    s = _db()
    try:
        query = s.query(User)
        if q:
            query = query.filter(User.email.contains(q))
        users = query.order_by(User.id.desc()).limit(limit).all()
        out = []
        for u in users:
            spent = sum(r[0] or 0 for r in
                        s.query(Ledger.delta).filter(
                            Ledger.user_id == u.id, Ledger.delta < 0).all())
            out.append({
                "id": u.id, "email": u.email, "is_admin": bool(u.is_admin),
                "credits": float(u.credit_balance),
                "total_spent": -spent,
                "created_at": u.created_at.isoformat() if u.created_at else "",
            })
    finally:
        s.close()
    return jsonify({"success": True, "users": out})


@app.post("/api/admin/topup")
@admin_required
def admin_topup():
    admin = current_user()
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()
    user_id = body.get("user_id")
    plan = str(body.get("plan", "custom"))[:64]
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid amount."}), 400
    if amount <= 0 or amount > 10_000_000:
        return jsonify({"success": False, "error": "Amount must be between 1 and 10,000,000."}), 400
    s = _db()
    try:
        u = None
        if user_id:
            u = s.query(User).filter(User.id == int(user_id)).first()
        elif email:
            u = s.query(User).filter(User.email == email).first()
        if not u:
            return jsonify({"success": False, "error": "User not found."}), 404
        u.credit_balance = round(float(u.credit_balance) + amount, 2)
        s.add(Ledger(user_id=u.id, delta=round(amount, 2),
                     balance_after=u.credit_balance, reason="admin_topup",
                     meta=json.dumps({"plan": plan, "by": admin["email"]})))
        s.commit()
        return jsonify({"success": True, "email": u.email,
                        "new_balance": u.credit_balance})
    finally:
        s.close()


@app.get("/api/admin/ledger")
@admin_required
def admin_ledger():
    try:
        user_id = int(request.args.get("user_id", 0))
    except (TypeError, ValueError):
        user_id = 0
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    s = _db()
    try:
        q = s.query(Ledger)
        if user_id:
            q = q.filter(Ledger.user_id == user_id)
        rows = q.order_by(Ledger.id.desc()).limit(limit).all()
        out = [{
            "id": r.id, "user_id": r.user_id, "delta": r.delta,
            "balance_after": r.balance_after, "reason": r.reason,
            "meta": r.meta,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        } for r in rows]
    finally:
        s.close()
    return jsonify({"success": True, "ledger": out})


# ------------------------------------------------------------ frontend --
@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    # Never serve hidden files or the backend directory.
    if filename.startswith(".") or ".." in filename:
        return jsonify({"error": "not found"}), 404
    return send_from_directory(FRONTEND_DIR, filename)


@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "key_configured": bool(API_KEY)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
