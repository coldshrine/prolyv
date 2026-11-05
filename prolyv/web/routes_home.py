"""Home with the feed (public sessions, discussions, blog posts), redirects for old placeholders, health."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from prolyv.models import Tea, TeaStatus
from prolyv.web.deps import CurrentUser, DbDep
from prolyv.web.templating import render

router = APIRouter()

FEED_TABS = [("all", "All"), ("sessions", "Sessions"), ("discussions", "Discussions"), ("blog", "Blog")]


def build_feed(db, tab: str = "all", limit: int = 30) -> list[dict]:  # noqa: ANN001
    """Newest public things across the site. Each item: {kind, at, obj}."""
    from prolyv.web.routes_diary import public_sessions
    from prolyv.web.routes_forum import latest_topics

    items: list[dict] = []
    if tab in ("all", "sessions"):
        from prolyv.models import TeaSession

        sessions = db.execute(public_sessions().order_by(TeaSession.brewed_at.desc()).limit(limit)).scalars().all()
        items += [{"kind": "session", "at": s.brewed_at, "obj": s} for s in sessions]
    if tab in ("all", "discussions"):
        items += [{"kind": "topic", "at": t.created_at, "obj": t} for t in latest_topics(db, limit)]
    if tab in ("all", "blog"):
        try:
            from prolyv.web.routes_blog import latest_posts
        except ImportError:  # pragma: no cover - blog lands in milestone 5
            latest_posts = None
        if latest_posts is not None:
            items += [{"kind": "post", "at": p.published_at, "obj": p} for p in latest_posts(db, limit)]
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:limit]


@router.get("/")
def home(request: Request, db: DbDep, user: CurrentUser, tab: str = "all"):
    tab = tab if tab in dict(FEED_TABS) else "all"
    tea_count = db.scalar(select(__import__("sqlalchemy").func.count()).select_from(Tea).where(Tea.status == TeaStatus.published)) or 0
    return render(request, "home.html", {"feed": build_feed(db, tab), "tab": tab, "tabs": FEED_TABS, "tea_count": tea_count})


@router.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


REDIRECTS = {"feed": "/", "discuss": "/discussions", "new": "/sessions/new"}


@router.get("/{section}")
def legacy_redirect(section: str):
    target = REDIRECTS.get(section)
    if target is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(target, status_code=302)
