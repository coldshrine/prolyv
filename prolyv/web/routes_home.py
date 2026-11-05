"""Home with the feed (public sessions, discussions, blog posts), redirects for old placeholders, health."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select

from prolyv.models import Tea, TeaStatus
from prolyv.social import followee_ids
from prolyv.web.deps import CurrentUser, DbDep
from prolyv.web.templating import render

router = APIRouter()

FEED_TABS = [("all", "All"), ("sessions", "Sessions"), ("discussions", "Discussions"), ("blog", "Blog")]


def build_feed(db, tab: str = "all", limit: int = 30, following: list | None = None) -> list[dict]:  # noqa: ANN001
    """Newest public things across the site. Each item: {kind, at, obj}. `following` limits it to those authors."""
    from prolyv.models import Post, TeaSession, Topic
    from prolyv.web.routes_blog import published_posts
    from prolyv.web.routes_diary import public_sessions
    from prolyv.web.routes_forum import topic_query

    items: list[dict] = []
    if tab in ("all", "sessions", "following"):
        stmt = public_sessions().order_by(TeaSession.brewed_at.desc()).limit(limit)
        if following is not None:
            stmt = stmt.where(TeaSession.user_id.in_(following))
        items += [{"kind": "session", "at": s.brewed_at, "obj": s} for s in db.execute(stmt).scalars().all()]
    if tab in ("all", "discussions", "following"):
        stmt = topic_query().where(Topic.hidden.is_(False)).order_by(Topic.created_at.desc()).limit(limit)
        if following is not None:
            stmt = stmt.where(Topic.user_id.in_(following))
        items += [{"kind": "topic", "at": t.created_at, "obj": t} for t in db.execute(stmt).scalars().all()]
    if tab in ("all", "blog"):
        stmt = published_posts().order_by(Post.published_at.desc()).limit(limit)
        items += [{"kind": "post", "at": p.published_at, "obj": p} for p in db.execute(stmt).scalars().all()]
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:limit]


@router.get("/")
def home(request: Request, db: DbDep, user: CurrentUser, tab: str = "all"):
    tabs = [*FEED_TABS, ("following", "Following")] if user else FEED_TABS
    tab = tab if tab in dict(tabs) else "all"
    following = followee_ids(db, user) if tab == "following" and user else None
    tea_count = db.scalar(select(func.count()).select_from(Tea).where(Tea.status == TeaStatus.published)) or 0
    return render(request, "home.html", {"feed": build_feed(db, tab, following=following), "tab": tab, "tabs": tabs, "tea_count": tea_count})


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
