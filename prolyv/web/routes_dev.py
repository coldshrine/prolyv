"""Local-only sign-in without a provider. Enabled by DEV_LOGIN=1 and refused whenever BASE_URL is https."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from prolyv.auth.accounts import get_user_by_handle
from prolyv.auth.sessions import create_session
from prolyv.i18n import _
from prolyv.models import Identity, Provider, User
from prolyv.security import SESSION_COOKIE
from prolyv.web.cookies import set_cookie
from prolyv.web.deps import DbDep, SettingsDep, SignerDep, on_this_machine
from prolyv.web.flash import set_flash

router = APIRouter()

DEMO_HANDLE = "demo_user"


@router.get("/dev/login")
def dev_login_demo(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, next: str = ""):
    """'Continue as demo user' on the login page (localhost with DEV_LOGIN=1 only)."""
    if not settings.dev_login_enabled or not on_this_machine(request):
        raise HTTPException(status_code=404)
    if get_user_by_handle(db, DEMO_HANDLE) is None:
        user = User(handle=DEMO_HANDLE, display_name="Demo User", bio="A local demo account.")
        db.add(user)
        db.add(Identity(user=user, provider=Provider.telegram, provider_user_id="dev-demo", raw_profile={"first_name": "Demo"}))
        db.flush()
    return dev_login(request, db, settings, signer, DEMO_HANDLE, next)


@router.get("/dev/login/{handle}")
def dev_login(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, handle: str, next: str = ""):
    if not settings.dev_login_enabled or not on_this_machine(request):
        raise HTTPException(status_code=404)
    user = get_user_by_handle(db, handle.lower())
    if user is None:
        raise HTTPException(status_code=404)
    _session, token = create_session(db, user, "dev-login", settings.session_days)
    response = RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)
    set_cookie(response, settings, SESSION_COOKIE, token, settings.session_days * 86400)
    set_flash(response, settings, signer, "ok", _("Signed in as @{handle} (dev login).", handle=user.handle))
    return response
