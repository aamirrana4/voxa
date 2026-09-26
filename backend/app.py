"""
Voxa backend — secure proxy between the Voxa website and the ai33.pro API.

SECURITY: the ai33 API key is read ONLY from the AI33_API_KEY environment
variable. It is never written to disk, never logged, and never sent to the
browser. All /api/* routes below run server-side.
"""
from __future__ import annotations

import os

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

AI33_BASE = "https://api.ai33.pro"
API_KEY = os.environ.get("AI33_API_KEY", "").strip()

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

app = Flask(__name__)
# 350MB cap covers the largest supported uploads (voice changer / dubbing).
app.config["MAX_CONTENT_LENGTH"] = 350 * 1024 * 1024


def _need_key():
    if not API_KEY:
        return jsonify({
            "success": False,
            "error": "AI33_API_KEY is not set on the server. "
                     "Set it as an environment variable and restart.",
        }), 500
    return None


def _proxy_json(method, path, payload=None, params=None, timeout=120):
    """Forward a JSON request to ai33 and return its JSON response."""
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
        return jsonify({"success": False, "error": f"Upstream request failed: {exc}"}), 502
    try:
        data = resp.json()
    except ValueError:
        data = {"success": False, "error": "Upstream returned non-JSON response"}
    return jsonify(data), resp.status_code


def _proxy_formdata(path, timeout=300):
    """Forward a browser multipart/form-data request to ai33 as multipart."""
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
        return jsonify({"success": False, "error": f"Upstream request failed: {exc}"}), 502
    try:
        data = resp.json()
    except ValueError:
        data = {"success": False, "error": "Upstream returned non-JSON response"}
    return jsonify(data), resp.status_code


# ---------------------------------------------------------------- core ----
@app.get("/api/credits")
def credits():
    return _proxy_json("GET", "/v1/credits")


@app.get("/api/health")
def health():
    return _proxy_json("GET", "/v1/health-check")


@app.get("/api/voices")
def voices():
    params = {
        k: request.args[k]
        for k in ("provider", "search", "q", "page", "page_size", "limit", "filters")
        if k in request.args
    }
    return _proxy_json("GET", "/v3/voices", params=params)


# ------------------------------------------------------------- tasks ----
@app.get("/api/task/<task_id>")
def task_status(task_id):
    return _proxy_json("GET", f"/v1/task/{task_id}")


@app.get("/api/tasks")
def task_list():
    params = {
        k: request.args[k]
        for k in ("page", "limit", "type")
        if k in request.args
    }
    return _proxy_json("GET", "/v1/tasks", params=params)


@app.post("/api/task/delete")
def task_delete():
    return _proxy_json("POST", "/v1/task/delete", payload=request.get_json(silent=True) or {})


# ------------------------------------------------------- text-to-speech --
@app.post("/api/tts")
def tts():
    return _proxy_formdata("/v3/text-to-speech")


@app.post("/api/dialogue")
def dialogue():
    return _proxy_formdata("/v3/text-to-speech/dialogue")


# ---------------------------------------------------------- voice clone --
@app.post("/api/clone")
def clone():
    storage = request.files.get("audio_file")
    if storage and storage.content_length and storage.content_length > 10 * 1024 * 1024:
        return jsonify({"success": False, "error": "Audio file must be under 10MB."}), 413
    return _proxy_formdata("/v3/text-to-speech/voice-clone")


@app.delete("/api/clone/<voice_clone_id>")
def clone_delete(voice_clone_id):
    return _proxy_json("DELETE", f"/v3/text-to-speech/voice-clone/{voice_clone_id}")


# ----------------------------------------------------------- audio tools --
@app.post("/api/stt")
def stt():
    return _proxy_formdata("/v1/task/speech-to-text", timeout=600)


@app.post("/api/dubbing")
def dubbing():
    return _proxy_formdata("/v1/task/dubbing", timeout=900)


@app.post("/api/voice-changer")
def voice_changer():
    return _proxy_formdata("/v1/task/voice-changer", timeout=900)


@app.post("/api/voice-isolate")
def voice_isolate():
    return _proxy_formdata("/v1/task/voice-isolate", timeout=900)


@app.post("/api/sfx")
def sfx():
    return _proxy_json("POST", "/v1/task/sound-effect",
                       payload=request.get_json(silent=True) or {})


@app.post("/api/music")
def music():
    return _proxy_json("POST", "/v1m/task/music-generation",
                       payload=request.get_json(silent=True) or {}, timeout=300)


# --------------------------------------------------------------- image --
@app.get("/api/image/models")
def image_models():
    return _proxy_json("GET", "/v1i/models")


@app.post("/api/image/price")
def image_price():
    return _proxy_json("POST", "/v1i/task/price",
                       payload=request.get_json(silent=True) or {})


@app.post("/api/image")
def image_generate():
    return _proxy_formdata("/v1i/task/generate-image", timeout=600)


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
