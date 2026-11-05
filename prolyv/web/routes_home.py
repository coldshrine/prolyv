"""Home with the feed (public sessions, discussions, blog posts), redirects for old placeholders, health."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select

from prolyv.models import Tea, TeaStatus
from prolyv.search import search_all
from prolyv.social import followee_ids
from prolyv.web.deps import CurrentUser, DbDep
from prolyv.web.templating import render

router = APIRouter()

FEED_PAGE = 30
FEED_MAX_PAGES = 30
FEED_TABS = [("all", "All"), ("sessions", "Sessions"), ("discussions", "Discussions"), ("blog", "Blog")]


def build_feed(db, tab: str = "all", limit: int = FEED_PAGE, following: list | None = None) -> list[dict]:  # noqa: ANN001
    """Newest public things across the site. Each item: {kind, at, obj}. `following` limits it to those authors.

    Asks each source for the `limit` newest and merges them. Ties have a fixed order, so pages never repeat or skip."""
    from prolyv.models import Post, TeaSession, Topic
    from prolyv.web.routes_blog import published_posts
    from prolyv.web.routes_diary import public_sessions
    from prolyv.web.routes_forum import topic_query

    items: list[dict] = []
    if tab in ("all", "sessions", "following"):
        stmt = public_sessions().order_by(TeaSession.brewed_at.desc(), TeaSession.id).limit(limit)
        if following is not None:
            stmt = stmt.where(TeaSession.user_id.in_(following))
        items += [{"kind": "session", "at": s.brewed_at, "obj": s} for s in db.execute(stmt).scalars().all()]
    if tab in ("all", "discussions", "following"):
        stmt = topic_query().where(Topic.hidden.is_(False)).order_by(Topic.created_at.desc(), Topic.id).limit(limit)
        if following is not None:
            stmt = stmt.where(Topic.user_id.in_(following))
        items += [{"kind": "topic", "at": t.created_at, "obj": t} for t in db.execute(stmt).scalars().all()]
    if tab in ("all", "blog"):
        stmt = published_posts().order_by(Post.published_at.desc(), Post.id).limit(limit)
        items += [{"kind": "post", "at": p.published_at, "obj": p} for p in db.execute(stmt).scalars().all()]
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:limit]


@router.get("/")
def home(request: Request, db: DbDep, user: CurrentUser, tab: str = "all", page: int = 1):
    tabs = [*FEED_TABS, ("following", "Following")] if user else FEED_TABS
    tab = tab if tab in dict(tabs) else "all"
    following = followee_ids(db, user) if tab == "following" and user else None
    tea_count = db.scalar(select(func.count()).select_from(Tea).where(Tea.status == TeaStatus.published)) or 0
    page = min(max(1, page), FEED_MAX_PAGES)
    items = build_feed(db, tab, limit=page * FEED_PAGE + 1, following=following)
    next_url = f"/?{urlencode({**({'tab': tab} if tab != 'all' else {}), 'page': page + 1})}" if len(items) > page * FEED_PAGE and page < FEED_MAX_PAGES else None
    context = {"feed": items[(page - 1) * FEED_PAGE:page * FEED_PAGE], "tab": tab, "tabs": tabs, "tea_count": tea_count, "next_url": next_url}
    return render(request, "home.html", context)


SEARCH_TABS = [("all", "All"), ("teas", "Teas"), ("discussions", "Discussions"), ("blog", "Blog"), ("sessions", "Sessions")]
SEARCH_KINDS = {"all": ("tea", "topic", "reply", "post", "session"), "teas": ("tea",), "discussions": ("topic", "reply"),
                "blog": ("post",), "sessions": ("session",)}


SEARCH_PAGE = 30
SEARCH_MAX_PAGES = 20


@router.get("/search")
def search_page(request: Request, db: DbDep, q: str = "", type: str = "all", page: int = 1):
    from prolyv.models import Post, TeaSession, Topic
    from prolyv.web.routes_blog import published_posts
    from prolyv.web.routes_diary import public_sessions
    from prolyv.web.routes_forum import topic_query
    from prolyv.web.routes_teas import CARD_OPTIONS

    q = q.strip()[:80]
    type = type if type in SEARCH_KINDS else "all"
    page = min(max(1, page), SEARCH_MAX_PAGES)
    matches = search_all(db, q, SEARCH_KINDS[type], limit=page * SEARCH_PAGE + 1) if q else []
    hits = matches[(page - 1) * SEARCH_PAGE:page * SEARCH_PAGE]
    more = len(matches) > page * SEARCH_PAGE and page < SEARCH_MAX_PAGES
    wanted = {kind: [ref for k, ref in hits if k == kind] for kind in ("tea", "topic", "post", "session")}
    found: dict[tuple, object] = {}
    for obj in db.execute(select(Tea).options(*CARD_OPTIONS).where(Tea.id.in_(wanted["tea"]), Tea.status == TeaStatus.published)).scalars():
        found[("tea", obj.id)] = obj
    for obj in db.execute(topic_query().where(Topic.id.in_(wanted["topic"]), Topic.hidden.is_(False))).scalars():
        found[("topic", obj.id)] = obj
    for obj in db.execute(published_posts().where(Post.id.in_(wanted["post"]))).scalars():
        found[("post", obj.id)] = obj
    for obj in db.execute(public_sessions().where(TeaSession.id.in_(wanted["session"]))).scalars():
        found[("session", obj.id)] = obj
    results = [{"kind": kind, "obj": found[(kind, ref)]} for kind, ref in hits if (kind, ref) in found]
    next_url = f"/search?{urlencode({'q': q, **({'type': type} if type != 'all' else {}), 'page': page + 1})}" if more else None
    return render(request, "search.html", {"q": q, "type": type, "tabs": SEARCH_TABS, "results": results, "next_url": next_url})


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
