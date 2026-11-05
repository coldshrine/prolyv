"""Jinja2 rendering with the context every page needs (user, csrf, flash, settings)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.templating import Jinja2Templates

from prolyv import __version__
from prolyv.web.deps import csrf_token_for
from prolyv.web.flash import pop_flash
from prolyv.web.useragent import is_in_app_browser

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
templates = Jinja2Templates(env=jinja_env)


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    app = request.app
    settings, signer = app.state.settings, app.state.signer
    ctx = {
        "settings": settings,
        "version": __version__,
        "current_user": getattr(request.state, "user", None),
        "csrf_token": csrf_token_for(request),
        "flash": pop_flash(request, settings, signer),
        "in_app_browser": is_in_app_browser(request.headers.get("user-agent")),
        "path": request.url.path,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
