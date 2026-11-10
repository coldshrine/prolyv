"""About, rules, privacy, terms, and the files browsers and crawlers ask for."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from prolyv.brand import SITE_NAME, SITE_SHORT_NAME
from prolyv.web.deps import SettingsDep
from prolyv.web.templating import render

router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent / "static"


@router.get("/about")
def about(request: Request):
    return render(request, "pages/about.html")


@router.get("/rules")
def rules(request: Request):
    return render(request, "pages/rules.html")


@router.get("/privacy")
def privacy(request: Request):
    return render(request, "pages/privacy.html")


@router.get("/terms")
def terms(request: Request):
    return render(request, "pages/terms.html")


@router.get("/robots.txt")
def robots(settings: SettingsDep):
    lines = ["User-agent: *", "Disallow: /admin", "Disallow: /auth/", "Disallow: /settings", "Disallow: /diary",
             "Disallow: /notifications", "Disallow: /dev/", f"Sitemap: {settings.base_url}/sitemap.xml"]
    return PlainTextResponse("\n".join(lines) + "\n")


@router.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/apple-touch-icon.png")
def apple_touch_icon():
    return FileResponse(STATIC / "icons" / "apple-touch-icon.png", media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/manifest.json")
def manifest():
    return JSONResponse({
        "name": SITE_NAME, "short_name": SITE_SHORT_NAME, "description": "Tea diary, tea base, forum and blog.", "start_url": "/",
        "display": "standalone", "background_color": "#07081A", "theme_color": "#07081A",
        "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, media_type="application/manifest+json")
