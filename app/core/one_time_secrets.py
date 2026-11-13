"""Secrets shown to a user exactly once (a new API key, a generated temporary
password, an invite link), kept on the server between the POST that creates
them and the page that shows them.

The browser's session cookie is signed but readable, so it only carries a
random token; the value itself is stored encrypted (Fernet, key derived from
SECRET_KEY) in `one_time_secrets`, belongs to one user, expires after 15
minutes and is deleted when read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, session
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

TTL_SECONDS = 15 * 60


class OneTimeSecret(db.Model):
    __tablename__ = "one_time_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fernet() -> Fernet:
    secret = str(current_app.config.get("SECRET_KEY") or "")
    key = hashlib.sha256(b"flowleads-one-time-secrets:" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def stash(kind: str, payload: dict, user_id: int, *, ttl_seconds: int = TTL_SECONDS) -> str:
    """Store `payload` for `user_id`; returns the token to keep in the session.
    Expired rows are removed on the way. Commits."""
    now = _now()
    OneTimeSecret.query.filter(OneTimeSecret.expires_at < now).delete(synchronize_session=False)
    token = secrets.token_urlsafe(32)
    db.session.add(
        OneTimeSecret(
            token_hash=_hash(token),
            user_id=user_id,
            kind=kind,
            payload_encrypted=_fernet().encrypt(json.dumps(payload).encode("utf-8")).decode("ascii"),
            expires_at=now + timedelta(seconds=ttl_seconds),
            created_at=now,
        )
    )
    db.session.commit()
    return token


def _row(kind: str, token: str | None, user_id: int) -> OneTimeSecret | None:
    if not token:
        return None
    row = OneTimeSecret.query.filter_by(token_hash=_hash(token), kind=kind, user_id=user_id).first()
    if row is None:
        return None
    if _aware(row.expires_at) < _now():
        db.session.delete(row)
        db.session.commit()
        return None
    return row


def _decode(row: OneTimeSecret) -> dict | None:
    try:
        return json.loads(_fernet().decrypt(row.payload_encrypted.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError):
        return None


def peek(kind: str, token: str | None, user_id: int) -> dict | None:
    row = _row(kind, token, user_id)
    return _decode(row) if row else None


def take(kind: str, token: str | None, user_id: int) -> dict | None:
    """The payload, deleted as it is read (shown once)."""
    row = _row(kind, token, user_id)
    if row is None:
        return None
    payload = _decode(row)
    db.session.delete(row)
    db.session.commit()
    return payload


# --- session helpers: a list of tokens per kind -----------------------------------------


def _session_key(kind: str) -> str:
    return f"one_time_{kind}"


def remember(kind: str, payload: dict, user_id: int) -> None:
    tokens = list(session.get(_session_key(kind), []))[-4:]
    tokens.append(stash(kind, payload, user_id))
    session[_session_key(kind)] = tokens
    session.modified = True


def pending(kind: str, user_id: int, match=None) -> list[tuple[str, dict]]:
    """[(token, payload)] still valid for the user, optionally filtered."""
    result = []
    kept = []
    for token in session.get(_session_key(kind), []):
        payload = peek(kind, token, user_id)
        if payload is None:
            continue
        kept.append(token)
        if match is None or match(payload):
            result.append((token, payload))
    session[_session_key(kind)] = kept
    session.modified = True
    return result


def consume(kind: str, user_id: int, match=None) -> dict | None:
    for token, _payload in pending(kind, user_id, match):
        payload = take(kind, token, user_id)
        session[_session_key(kind)] = [t for t in session.get(_session_key(kind), []) if t != token]
        session.modified = True
        return payload
    return None
