"""FastAPI dependencies: settings, database session, current user, CSRF."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prolyv.auth.accounts import is_admin_user
from prolyv.auth.sessions import load_session, touch_session
from prolyv.config import Settings
from prolyv.db import session_scope
from prolyv.models import Notification, User
from prolyv.security import SESSION_COOKIE, Signer
from prolyv.web.cookies import queue_cookie


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_signer(request: Request) -> Signer:
    return request.app.state.signer


def get_db(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.session_factory)


SettingsDep = Annotated[Settings, Depends(get_settings)]
SignerDep = Annotated[Signer, Depends(get_signer)]
DbDep = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep) -> User | None:
    """Loads the login session from the cookie (once per request) and slides its expiry when active."""
    token = request.cookies.get(SESSION_COOKIE)
    sess = load_session(db, token)
    request.state.session = sess
    request.state.user = sess.user if sess is not None else None
    request.state.csrf_token = signer.csrf_token(sess.token_hash) if sess is not None else ""
    request.state.is_admin = sess is not None and is_admin_user(db, settings, sess.user)
    if sess is None:
        return None
    request.state.unread = db.scalar(
        select(func.count()).select_from(Notification).where(Notification.user_id == sess.user_id, Notification.read_at.is_(None))
    ) or 0
    if touch_session(db, sess, settings.session_days):
        queue_cookie(request, settings, SESSION_COOKIE, token, settings.session_days * 86400)
    return sess.user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


def require_user(request: Request, user: CurrentUser) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="login_required")
    return user


RequiredUser = Annotated[User, Depends(require_user)]


def on_this_machine(request: Request) -> bool:
    """BASE_URL defaults to localhost, so a forgotten setting must not switch on local-only helpers on a real domain."""
    host = request.url.hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def csrf_token_for(request: Request) -> str:
    return getattr(request.state, "csrf_token", "")


async def require_csrf(request: Request, user: RequiredUser, signer: SignerDep) -> None:
    """All state-changing forms carry csrf_token bound to the login session."""
    form = await request.form()
    sess = request.state.session
    if not signer.verify_csrf(sess.token_hash, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="csrf_failed")


def require_admin(request: Request, user: RequiredUser) -> User:
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail="admin_only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def post_quota(request: Request, user: RequiredUser) -> None:
    """Limits new topics, replies, comments and reports per user."""
    if not request.app.state.post_limiter.allow(str(user.id)):
        raise HTTPException(status_code=429, detail="too_many_posts")
