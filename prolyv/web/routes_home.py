"""Home with the feed (public sessions, discussions, blog posts), redirects for old placeholders, health."""

from __future__ import annotations

import uuid
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select

from prolyv.i18n import N_, _
from prolyv.models import EXPLAINER_SLUG, ForumSection, Tea, TeaStatus
from prolyv.search import search_all, search_teas
from prolyv.social import followee_ids, in_context
from prolyv.web.deps import CurrentUser, DbDep
from prolyv.web.templating import render

router = APIRouter()

CATALOG = (Tea.status == TeaStatus.published, Tea.slug.not_like(EXPLAINER_SLUG))
FEED_PAGE = 30
FEED_MAX_PAGES = 30
FEED_TABS = [("all", N_("All")), ("sessions", N_("Sessions")), ("discussions", N_("Discuss")), ("blog", N_("Blog"))]  # shown through feed_tab()


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


def hot_topics(db, limit: int = 4) -> list:  # noqa: ANN001
    """The topics with most replies among those active in the last 30 days. Empty until people actually reply."""
    from prolyv.models import Topic, utcnow
    from prolyv.web.routes_forum import topic_query

    since = utcnow() - timedelta(days=30)
    return db.execute(
        topic_query().where(Topic.hidden.is_(False), Topic.reply_count > 0, Topic.last_reply_at >= since)
        .order_by(Topic.reply_count.desc(), Topic.last_reply_at.desc()).limit(limit)
    ).scalars().all()


def latest_public_session(db):  # noqa: ANN001, ANN201
    from prolyv.models import TeaSession
    from prolyv.web.routes_diary import public_sessions

    return db.execute(public_sessions().order_by(TeaSession.brewed_at.desc()).limit(1)).scalars().first()


def feed_tab(label: str) -> str:
    """A tab of the feed in the visitor's language: in Russian «Обсуждения» and «Подписки» here, not the verbs."""
    return in_context("feed tab", label)


@router.get("/")
def home(request: Request, db: DbDep, user: CurrentUser, tab: str = "all", page: int = 1):
    tabs = [*FEED_TABS, ("following", N_("Following"))] if user else FEED_TABS
    tab = tab if tab in dict(tabs) else "all"
    tabs = [(key, feed_tab(label)) for key, label in tabs]
    following = followee_ids(db, user) if tab == "following" and user else None
    tea_count = db.scalar(select(func.count()).select_from(Tea).where(*CATALOG)) or 0
    page = min(max(1, page), FEED_MAX_PAGES)
    items = build_feed(db, tab, limit=page * FEED_PAGE + 1, following=following)
    next_url = f"/?{urlencode({**({'tab': tab} if tab != 'all' else {}), 'page': page + 1})}" if len(items) > page * FEED_PAGE and page < FEED_MAX_PAGES else None
    context = {"feed": items[(page - 1) * FEED_PAGE:page * FEED_PAGE], "tab": tab, "tabs": tabs, "tea_count": tea_count, "next_url": next_url,
               "hot_topics": hot_topics(db), "sections": db.execute(select(ForumSection).order_by(ForumSection.sort)).scalars().all(),
               "latest_session": None if user else latest_public_session(db)}
    return render(request, "home.html", context)


SEARCH_TABS = [("all", N_("All")), ("teas", N_("Teas")), ("discussions", N_("Discussions")), ("blog", N_("Blog")), ("sessions", N_("Sessions"))]  # shown with _()
SEARCH_KINDS = {"all": ("tea", "topic", "reply", "post", "session"), "teas": ("tea",), "discussions": ("topic", "reply"),
                "blog": ("post",), "sessions": ("session",)}


SEARCH_PAGE = 30
SEARCH_PREVIEW = 5
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

    def find(kinds: tuple[str, ...], limit: int) -> list[tuple[str, uuid.UUID]]:
        if kinds == ("tea",):
            return [("tea", ref) for ref in search_teas(db, q, limit)]
        return search_all(db, q, kinds, limit=limit)

    def load(hits: list[tuple[str, uuid.UUID]]) -> list[dict]:
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
        return [{"kind": kind, "obj": found[(kind, ref)]} for kind, ref in hits if (kind, ref) in found]

    groups, results, next_url = [], [], None
    if q and type == "all":
        # a few of each kind, so common words don't bury discussions and posts under teas
        for key, label in SEARCH_TABS[1:]:
            matches = find(SEARCH_KINDS[key], SEARCH_PREVIEW + 1)
            items = load(matches[:SEARCH_PREVIEW])
            if items:
                groups.append({"key": key, "label": _(label), "items": items, "more": len(matches) > SEARCH_PREVIEW})
    elif q:
        matches = find(SEARCH_KINDS[type], page * SEARCH_PAGE + 1)
        results = load(matches[(page - 1) * SEARCH_PAGE:page * SEARCH_PAGE])
        if len(matches) > page * SEARCH_PAGE and page < SEARCH_MAX_PAGES:
            next_url = f"/search?{urlencode({'q': q, 'type': type, 'page': page + 1})}"
    context = {"q": q, "type": type, "tabs": [(key, _(label)) for key, label in SEARCH_TABS], "results": results, "groups": groups, "next_url": next_url}
    return render(request, "search.html", context)


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
