"""Login sessions: random token in the cookie, SHA-256 hash in the database, sliding 30-day expiry."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from prolyv.models import AuthSession, User, utcnow
from prolyv.security import hash_token, new_session_token

REFRESH_AFTER = timedelta(hours=1)  # how often an active session's expiry is pushed forward


def create_session(db: Session, user: User, user_agent: str | None, days: int) -> tuple[AuthSession, str]:
    token = new_session_token()
    now = utcnow()
    sess = AuthSession(
        user=user,
        token_hash=hash_token(token),
        expires_at=now + timedelta(days=days),
        created_at=now,
        last_seen_at=now,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(sess)
    db.flush()
    return sess, token


def load_session(db: Session, token: str | None) -> AuthSession | None:
    """The session for a cookie token, or None when missing, unknown, expired or the user is deleted."""
    if not token:
        return None
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token))).scalar_one_or_none()
    if sess is None:
        return None
    now = utcnow()
    if sess.expires_at <= now or sess.user.deleted_at is not None:
        db.delete(sess)
        db.flush()
        return None
    return sess


def touch_session(db: Session, sess: AuthSession, days: int) -> bool:
    """Slide the expiry forward on use. Returns True when the cookie should be re-issued."""
    now = utcnow()
    if now - sess.last_seen_at < REFRESH_AFTER:
        return False
    sess.last_seen_at = now
    sess.expires_at = now + timedelta(days=days)
    db.flush()
    return True


def revoke_session(db: Session, sess: AuthSession) -> None:
    db.delete(sess)
    db.flush()
