"""SQLAlchemy models for Voxa accounts + credit ledger.

DATABASE_URL env var selects the database. Local dev defaults to a SQLite file
next to this module. Production MUST set DATABASE_URL to a persistent Postgres
(e.g. Neon free tier) — Render's free web-service filesystem is ephemeral and
SQLite data would be wiped on every redeploy.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    credit_balance = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class Ledger(Base):
    """Append-only credit ledger. balance_after is the source of truth per row."""
    __tablename__ = "ledger"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    delta = Column(Float, nullable=False)  # negative = spent, positive = added
    balance_after = Column(Float, nullable=False)
    reason = Column(String(64), nullable=False)  # tts, dialogue, admin_topup, signup_bonus, ...
    meta = Column(Text, default="{}")  # JSON: task_id, plan, note
    created_at = Column(DateTime, default=_utcnow, index=True)


class Job(Base):
    """Maps an ai33 task_id to the user who created it (per-user history)."""
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(128), nullable=False, index=True)
    kind = Column(String(32), nullable=False)  # tts, dialogue, speech-to-text, ...
    cost = Column(Float, nullable=False, default=0.0)
    status = Column(String(16), default="doing")
    created_at = Column(DateTime, default=_utcnow, index=True)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        here = os.path.dirname(os.path.abspath(__file__))
        return "sqlite:///" + os.path.join(here, "voxa.db")
    if url.startswith("postgres://"):  # Render/Heroku-style URLs
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    if url.startswith("postgresql") and "sslmode" not in url:
        return {"sslmode": "require"}  # Neon/Render Postgres need TLS
    return {}


DATABASE_URL = _database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True,
                       connect_args=_connect_args(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
