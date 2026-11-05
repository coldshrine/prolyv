"""Local-only sign-in without a provider. Enabled by DEV_LOGIN=1 and refused whenever BASE_URL is https."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from prolyv.auth.accounts import get_user_by_handle
from prolyv.auth.sessions import create_session
from prolyv.security import SESSION_COOKIE
from prolyv.web.cookies import set_cookie
from prolyv.web.deps import DbDep, SettingsDep, SignerDep
from prolyv.web.flash import set_flash

router = APIRouter()


@router.get("/dev/login/{handle}")
def dev_login(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, handle: str):
    if not settings.dev_login_enabled:
        raise HTTPException(status_code=404)
    user = get_user_by_handle(db, handle.lower())
    if user is None:
        raise HTTPException(status_code=404)
    _, token = create_session(db, user, "dev-login", settings.session_days)
    response = RedirectResponse("/", status_code=303)
    set_cookie(response, settings, SESSION_COOKIE, token, settings.session_days * 86400)
    set_flash(response, settings, signer, "ok", f"Signed in as @{user.handle} (dev login).")
    return response
