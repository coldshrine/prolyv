"""Jinja2 rendering with the context every page needs (user, csrf, flash, settings, language)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import Request
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.templating import Jinja2Templates

from prolyv import __version__
from prolyv.brand import BRAND_HANDLE, SITE_NAME
from prolyv.i18n import LANG_NAMES, LANGS, format_date, get_lang, js_text, set_lang, template_gettext, template_ngettext
from prolyv.web.cup import cup_styles
from prolyv.web.tea_labels import place
from prolyv.web.text import linkify, source_label, time_ago
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
jinja_env.globals["cup_styles"] = cup_styles
jinja_env.globals["site_name"] = SITE_NAME
jinja_env.globals["brand_handles"] = (BRAND_HANDLE, "prolyv")
jinja_env.globals["_"] = template_gettext
jinja_env.globals["_n"] = template_ngettext
jinja_env.globals["lang_names"] = LANG_NAMES
jinja_env.globals["ui_langs"] = LANGS
jinja_env.filters["date"] = format_date
jinja_env.filters["linkify"] = linkify
jinja_env.filters["ago"] = time_ago
jinja_env.filters["source_label"] = source_label
jinja_env.filters["place"] = place  # "China · Fujian" with each known place in the visitor's language
templates = Jinja2Templates(env=jinja_env)


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    app = request.app
    settings, signer = app.state.settings, app.state.signer
    ui_lang = set_lang(request.scope.get("state", {}).get("lang") or get_lang())
    ctx = {
        "settings": settings,
        "version": __version__,
        "current_user": getattr(request.state, "user", None),
        "is_admin": getattr(request.state, "is_admin", False),
        "unread": getattr(request.state, "unread", 0),
        "csrf_token": csrf_token_for(request),
        "flash": pop_flash(request, settings, signer),
        "in_app_browser": is_in_app_browser(request.headers.get("user-agent")),
        "path": request.url.path,
        "here": request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        "year": date.today().year,
        "ui_lang": ui_lang,
        "js_text": js_text(),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
