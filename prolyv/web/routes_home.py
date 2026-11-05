"""Home and placeholders for the sections the next milestones fill in."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from prolyv.web.deps import CurrentUser
from prolyv.web.templating import render

router = APIRouter()

SECTIONS = {
    "feed": ("Feed", "Public tea sessions from everyone on Prolyv will show up here."),
    "discuss": ("Discussions", "Topics and replies about the teas you drink."),
    "blog": ("Blog", "Full brewing breakdowns behind the short videos."),
    "diary": ("My diary", "Your sessions, private by default, public when you choose."),
    "new": ("Log a tea", "Tea, grams, water, temperature, steeps, rating, notes — in 30 seconds."),
}


@router.get("/")
def home(request: Request, user: CurrentUser):
    return render(request, "home.html")


@router.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


@router.get("/{section}")
def coming_soon(request: Request, section: str, user: CurrentUser):
    if section not in SECTIONS:
        raise HTTPException(status_code=404)
    title, text = SECTIONS[section]
    return render(request, "coming_soon.html", {"title": title, "text": text, "section": section})
