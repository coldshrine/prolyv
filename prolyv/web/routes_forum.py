"""Forum: sections, topics with attached sessions, replies with helpful votes, FTS5 search."""

from __future__ import annotations

import math
import uuid
from datetime import timedelta
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select, true
from sqlalchemy.orm import selectinload

from prolyv.auth.accounts import get_user_by_handle
from prolyv.i18n import N_, _
from prolyv.models import (
    ForumSection, HelpfulVote, Notification, Post, Reply, ReplyVote, TeaSession, Topic, TopicFollow, User, Visibility, utcnow,
)
from prolyv.search import index_reply, index_topic, remove_reply, remove_topic, search_topics
from prolyv.social import MAX_FANOUT, notify, notify_followers
from prolyv.web.cup import _mix, palette_for
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render
from prolyv.web.text import mentions

router = APIRouter()
PAGE_SIZE = 20
REPLY_PAGE = 10
# labels in English, shown with _()
TOPIC_SORTS = {"active": N_("Active"), "newest": N_("Newest"), "unanswered": N_("Unanswered"), "helpful": N_("Most helpful")}
REPLY_SORTS = {"helpful": N_("Most helpful"), "newest": N_("Newest")}
SECTION_CUPS = {"oolong": "oolong", "pu-erh": "shu", "black-white": "black", "green-yellow": "green", "teaware": "white",
                "beginners": "yellow", "where-to-buy": "sheng"}
TOO_FAST = N_("Slow down a little. You're posting very quickly: wait a few minutes and try again. Your text is still here.")  # shown with _()


def all_sections(db) -> list[ForumSection]:  # noqa: ANN001
    return db.execute(select(ForumSection).order_by(ForumSection.sort)).scalars().all()


def topic_query():
    return select(Topic).options(
        selectinload(Topic.user), selectinload(Topic.section),
        selectinload(Topic.attached_session).selectinload(TeaSession.steeps),
        selectinload(Topic.attached_session).selectinload(TeaSession.user),
        selectinload(Topic.attached_session).selectinload(TeaSession.tea),
        selectinload(Topic.attached_session).selectinload(TeaSession.photos),
        selectinload(Topic.attached_session).selectinload(TeaSession.comments),
    )


def my_public_sessions(db, user: User | None, limit: int = 20) -> list[TeaSession]:  # noqa: ANN001
    if user is None:
        return []
    return db.execute(
        select(TeaSession).where(TeaSession.user_id == user.id, TeaSession.visibility == Visibility.public)
        .order_by(TeaSession.brewed_at.desc()).limit(limit)
    ).scalars().all()


def resolve_attachment(db, user: User, raw: str) -> TeaSession | None:  # noqa: ANN001
    """Only the author's own public sessions may be attached (a private one would leak)."""
    if not raw:
        return None
    try:
        sess = db.get(TeaSession, uuid.UUID(raw))
    except ValueError:
        return None
    if sess is None or sess.user_id != user.id or not sess.is_public:
        return None
    return sess


def whole_number(raw: str, default: int = 1) -> int:
    """A page number from the address bar: anything odd means the default, never an error page."""
    try:
        return int(raw)
    except ValueError:
        return default


def helpful_this_month(db, limit: int = 3) -> list[tuple[User, int]]:  # noqa: ANN001
    """People whose visible replies got the most Helpful votes in the last 30 days."""
    since = utcnow() - timedelta(days=30)
    votes = func.count(ReplyVote.id)
    return [(person, count) for person, count in db.execute(
        select(User, votes).join(Reply, Reply.user_id == User.id).join(ReplyVote, ReplyVote.reply_id == Reply.id).join(Topic, Reply.topic_id == Topic.id)
        .where(ReplyVote.created_at >= since, Reply.hidden.is_(False), Topic.hidden.is_(False), User.deleted_at.is_(None), User.banned_at.is_(None))
        .group_by(User.id).order_by(votes.desc(), User.handle).limit(limit)
    ).all()]


def by_topic_sort(stmt, sort: str):  # noqa: ANN001, ANN201
    activity = func.coalesce(Topic.last_reply_at, Topic.created_at)
    if sort in ("newest", "unanswered"):
        return stmt.order_by(Topic.created_at.desc(), Topic.id)
    if sort == "helpful":
        replies = select(Reply.topic_id, func.sum(Reply.helpful_count).label("n")).where(Reply.hidden.is_(False)).group_by(Reply.topic_id).subquery()
        opening = select(HelpfulVote.ref_id, func.count(HelpfulVote.id).label("n")).where(HelpfulVote.kind == "topic").group_by(HelpfulVote.ref_id).subquery()
        score = func.coalesce(replies.c.n, 0) + func.coalesce(opening.c.n, 0)
        stmt = stmt.outerjoin(replies, replies.c.topic_id == Topic.id).outerjoin(opening, opening.c.ref_id == Topic.id)
        return stmt.order_by(score.desc(), activity.desc(), Topic.id)
    return stmt.order_by(Topic.pinned.desc(), activity.desc(), Topic.id)


def list_topics(request: Request, db, user, section: ForumSection | None, q: str, sort: str, raw_page: str):  # noqa: ANN001
    q = q.strip()[:80]
    page = max(1, whole_number(raw_page))
    sort = sort if sort in TOPIC_SORTS or (sort == "match" and q) else ("match" if q else "active")
    stmt = topic_query()
    if not getattr(request.state, "is_admin", False):
        stmt = stmt.where(Topic.hidden.is_(False))
    if section is not None:
        stmt = stmt.where(Topic.section_id == section.id)
    in_scope = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    ranked: list[uuid.UUID] = []
    if q:
        ranked = search_topics(db, q)
        stmt = stmt.where(Topic.id.in_(ranked)) if ranked else stmt.where(False)  # noqa: E712
    if sort == "unanswered":
        stmt = stmt.where(Topic.reply_count == 0)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    if sort == "match":
        topics = db.execute(stmt).scalars().all()
        order = {tid: n for n, tid in enumerate(ranked)}
        topics.sort(key=lambda t: order.get(t.id, 999))
        topics = topics[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    else:
        topics = db.execute(by_topic_sort(stmt, sort).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).scalars().all()
    counts = dict(db.execute(select(Topic.section_id, func.count()).where(Topic.hidden.is_(False)).group_by(Topic.section_id)).all())
    sections = [(s, counts.get(s.id, 0)) for s in all_sections(db)]
    base = f"/discussions/s/{section.slug}" if section else "/discussions"
    default_sort = "match" if q else "active"

    def list_url(path: str = base, query: str = q, page_number: int = 1) -> str:
        params = {**({"q": query} if query else {}), **({"sort": sort} if sort not in (default_sort, "match") else {}),
                  **({"page": page_number} if page_number > 1 else {})}
        return f"{path}?{urlencode(params)}" if params else path

    def section_url(item: ForumSection | None) -> str:
        """Picking a section keeps the search and the sort; picking the current one again goes back to all."""
        same = item is None or (section is not None and item.id == section.id)
        return list_url("/discussions" if same else f"/discussions/s/{item.slug}")

    new_path = f"/discussions/new?section={section.slug}" if section else "/discussions/new"
    context = {"topics": topics, "sections": sections, "section": section, "q": q, "sort": sort, "sorts": TOPIC_SORTS, "page": page,
               "pages": pages, "total": total, "in_scope": in_scope, "base": base, "section_cups": SECTION_CUPS,
               "page_url": lambda n: list_url(page_number=n), "section_url": section_url, "clear_url": list_url(query=""),
               "shown": min(page * PAGE_SIZE, total), "helpers": helpful_this_month(db),
               "new_url": new_path if user else f"/login?next={quote(new_path, safe='')}"}
    return render(request, "forum/index.html", context)


@router.get("/discussions")
def forum_index(request: Request, db: DbDep, user: CurrentUser, q: str = "", sort: str = "", page: str = "1"):
    return list_topics(request, db, user, None, q, sort, page)


@router.get("/discussions/s/{slug}")
def forum_section(request: Request, db: DbDep, user: CurrentUser, slug: str, q: str = "", sort: str = "", page: str = "1"):
    section = db.execute(select(ForumSection).where(ForumSection.slug == slug)).scalar_one_or_none()
    if section is None:
        raise HTTPException(status_code=404)
    return list_topics(request, db, user, section, q, sort, page)


@router.get("/discussions/new")
def topic_new_form(request: Request, db: DbDep, user: RequiredUser, section: str = "", session: str = "", ask: str = ""):
    asked = get_user_by_handle(db, ask.strip().lstrip("@").lower()) if ask else None
    form = {"section": section, "title": "", "body": f"@{asked.handle} " if asked else "", "attached_session_id": session}
    context = {"form": form, "errors": {}, "sections": all_sections(db), "my_sessions": my_public_sessions(db, user), "asked": asked}
    return render(request, "forum/new.html", context)


def notify_mentions(db, author: User, body: str, url: str, what: str) -> set[uuid.UUID]:  # noqa: ANN001
    """An @handle in a post reaches that person. "Ask X in the forum" relies on this. Returns who was told."""
    told: set[uuid.UUID] = set()
    for handle in mentions(body):
        person = get_user_by_handle(db, handle)
        if person is not None:
            notify(db, person.id, author, f"@{author.handle} mentioned you in {what}", url)
            told.add(person.id)
    return told


def validate_topic(data, db) -> tuple[dict, dict]:  # noqa: ANN001
    form = {
        "section": (data.get("section") or "").strip(), "title": " ".join((data.get("title") or "").split())[:160],
        "body": (data.get("body") or "").strip()[:20000], "attached_session_id": (data.get("attached_session_id") or "").strip(),
    }
    errors: dict[str, str] = {}
    if len(form["title"]) < 5:
        errors["title"] = _("Give the topic a title (at least 5 characters).")
    if len(form["body"]) < 10:
        errors["body"] = _("Say a bit more (at least 10 characters).")
    section = db.execute(select(ForumSection).where(ForumSection.slug == form["section"])).scalar_one_or_none()
    if section is None:
        errors["section"] = _("Pick a section.")
    return form, errors


@router.post("/discussions/new", dependencies=[Depends(require_csrf)])
async def topic_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    form, errors = validate_topic(await request.form(), db)
    status = 400
    if not errors and not request.app.state.post_limiter.allow(str(user.id)):  # same quota as post_quota, but the text survives
        errors, status = {"body": _(TOO_FAST)}, 429
    if errors:
        context = {"form": form, "errors": errors, "sections": all_sections(db), "my_sessions": my_public_sessions(db, user)}
        return render(request, "forum/new.html", context, status_code=status)
    section = db.execute(select(ForumSection).where(ForumSection.slug == form["section"])).scalar_one()
    topic = Topic(section=section, user=user, title=form["title"], body=form["body"],
                  attached_session=resolve_attachment(db, user, form["attached_session_id"]))
    db.add(topic)
    db.flush()
    index_topic(db, topic.id, topic.title, topic.body)
    notify_followers(db, user, f"@{user.handle} started a topic: {topic.title}", f"/discussions/{topic.id}")
    notify_mentions(db, user, topic.body, f"/discussions/{topic.id}", f"“{topic.title}”")
    response = RedirectResponse(f"/discussions/{topic.id}", status_code=303)
    set_flash(response, settings, signer, "ok", _("Topic posted."))
    return response


def get_topic(db, topic_id: uuid.UUID) -> Topic:  # noqa: ANN001
    topic = db.execute(topic_query().where(Topic.id == topic_id)).scalar_one_or_none()
    if topic is None:
        raise HTTPException(status_code=404)
    return topic


def visible_topic(request: Request, db, topic_id: uuid.UUID) -> Topic:  # noqa: ANN001
    """A hidden topic does not exist for anyone but admins: no reading it, replying to it or voting on it."""
    topic = get_topic(db, topic_id)
    if topic.hidden and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=404)
    return topic


def steep_orb(kind: str, liquor_hex: str | None, n: int) -> str:
    """The little cup of one steep in the strip: the liquor paling as the leaf gives out, as on the canvas."""
    stops = palette_for(kind, liquor_hex)[0]
    t = min(n, 12) / 12
    light, dark = _mix(stops[0], (255, 233, 247), 0.25 + t * 0.6), _mix(stops[0], (20, 4, 50), 0.6 - t * 0.5)
    return (f"radial-gradient(circle at 32% 28%, rgba(255,255,255,0.85) 0, rgba(255,255,255,0) 26%), "
            f"radial-gradient(circle at 50% 60%, {light} 0, {dark} 100%)")


def thread_sessions(db, topic: Topic) -> list[TeaSession]:  # noqa: ANN001
    """The public sessions people attached in this thread: the opening post's first, then by reply, each once."""
    rows = db.execute(
        select(TeaSession).join(Reply, Reply.attached_session_id == TeaSession.id)
        .where(Reply.topic_id == topic.id, Reply.hidden.is_(False), TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False))
        .options(selectinload(TeaSession.user), selectinload(TeaSession.tea), selectinload(TeaSession.steeps))
        .order_by(Reply.created_at).limit(12)
    ).scalars().all()
    found = [topic.shown_session] if topic.shown_session else []
    for sess in rows:
        if all(sess.id != other.id for other in found):
            found.append(sess)
    return found[:6]


def related_topics(db, topic: Topic, limit: int = 4) -> list[Topic]:  # noqa: ANN001
    return db.execute(
        select(Topic).where(Topic.section_id == topic.section_id, Topic.id != topic.id, Topic.hidden.is_(False))
        .order_by(func.coalesce(Topic.last_reply_at, Topic.created_at).desc(), Topic.id).limit(limit)
    ).scalars().all()


def blog_pick(db, sessions: list[TeaSession]) -> Post | None:  # noqa: ANN001
    """A published post about a tea from this thread, otherwise the newest one."""
    published = select(Post).where(Post.published_at.is_not(None)).order_by(Post.published_at.desc()).limit(1)
    tea_ids = [sess.tea_id for sess in sessions if sess.tea_id]
    post = db.execute(published.where(Post.tea_id.in_(tea_ids))).scalars().first() if tea_ids else None
    return post or db.execute(published).scalars().first()


def reply_order(sort: str) -> tuple:
    if sort == "newest":
        return (Reply.created_at.desc(), Reply.id)
    return (Reply.marked_helpful_by_author.desc(), Reply.helpful_count.desc(), Reply.created_at, Reply.id)


def topic_view(request: Request, db, user: User | None, topic: Topic, sort: str = "", raw_offset: str = "0", focus: str = "",  # noqa: ANN001, PLR0913
               draft: str = "", draft_session: str = "", form_error: str = "", status_code: int = 200):
    is_admin = getattr(request.state, "is_admin", False)
    sort = sort if sort in REPLY_SORTS else "helpful"
    seen = Reply.hidden.is_(False) if not is_admin else true()
    total = db.scalar(select(func.count()).select_from(Reply).where(Reply.topic_id == topic.id, seen)) or 0
    offset, limit = max(0, whole_number(raw_offset, 0)), REPLY_PAGE
    if focus:  # a link to one reply opens the thread far enough down to contain it
        ids = [str(i) for i in db.execute(select(Reply.id).where(Reply.topic_id == topic.id, seen).order_by(*reply_order(sort))).scalars()]
        if focus in ids:
            offset, limit = 0, max(REPLY_PAGE, math.ceil((ids.index(focus) + 1) / REPLY_PAGE) * REPLY_PAGE)
    replies = db.execute(
        select(Reply).where(Reply.topic_id == topic.id, seen)
        .options(selectinload(Reply.user), selectinload(Reply.attached_session).selectinload(TeaSession.steeps),
                 selectinload(Reply.attached_session).selectinload(TeaSession.user),
                 selectinload(Reply.attached_session).selectinload(TeaSession.tea))
        .order_by(*reply_order(sort)).offset(offset).limit(limit)
    ).scalars().all()
    my_votes: set[uuid.UUID] = set()
    if user is not None and replies:
        my_votes = set(db.execute(
            select(ReplyVote.reply_id).where(ReplyVote.user_id == user.id, ReplyVote.reply_id.in_([r.id for r in replies]))
        ).scalars().all())
    authors = {topic.user_id, *(r.user_id for r in replies)}
    session_counts = dict(db.execute(
        select(TeaSession.user_id, func.count()).where(TeaSession.user_id.in_(authors), TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False))
        .group_by(TeaSession.user_id)
    ).all())
    topic_votes = db.scalar(select(func.count()).select_from(HelpfulVote).where(HelpfulVote.kind == "topic", HelpfulVote.ref_id == topic.id)) or 0
    my_topic_vote = user is not None and db.execute(select(HelpfulVote.id).where(HelpfulVote.user_id == user.id, HelpfulVote.kind == "topic", HelpfulVote.ref_id == topic.id)).first() is not None
    following = user is not None and db.execute(select(TopicFollow.id).where(TopicFollow.user_id == user.id, TopicFollow.topic_id == topic.id)).first() is not None
    sessions = thread_sessions(db, topic)
    same_tea = len(sessions) > 1 and len({sess.tea_id or sess.tea_name for sess in sessions}) == 1
    from prolyv.web.routes_social import is_saved

    remaining = max(0, total - offset - len(replies))
    params = {"sort": sort} if sort != "helpful" else {}
    next_offset = offset + len(replies)
    context = {
        "topic": topic, "replies": replies, "my_votes": my_votes, "saved": is_saved(db, user, "topic", topic.id),
        "is_author": user is not None and user.id == topic.user_id, "is_admin_view": is_admin,
        "my_sessions": my_public_sessions(db, user), "total": total, "sort": sort, "sorts": REPLY_SORTS, "offset": offset,
        "remaining": remaining, "more_count": min(REPLY_PAGE, remaining),
        "more_url": f"/discussions/{topic.id}?{urlencode({**params, 'offset': next_offset})}",
        "first_url": f"/discussions/{topic.id}?{urlencode(params)}#replies" if params else f"/discussions/{topic.id}#replies",
        "session_counts": session_counts, "topic_votes": topic_votes, "my_topic_vote": my_topic_vote, "following": following,
        "sessions": sessions, "same_tea": same_tea, "related": related_topics(db, topic), "blog_post": blog_pick(db, sessions),
        "steep_orb": steep_orb, "draft": draft, "draft_session": draft_session, "form_error": form_error,
        "share_url": f"{request.app.state.settings.base_url}/discussions/{topic.id}", "login_url": f"/login?next={quote(f'/discussions/{topic.id}', safe='')}",
    }
    return render(request, "forum/topic.html", context, status_code=status_code)


@router.get("/discussions/{topic_id}")
def topic_page(request: Request, db: DbDep, user: CurrentUser, topic_id: str, sort: str = "", offset: str = "0", reply: str = ""):
    try:
        parsed = uuid.UUID(topic_id)
    except ValueError:
        raise HTTPException(status_code=404) from None
    return topic_view(request, db, user, visible_topic(request, db, parsed), sort, offset, reply)


def reply_url(topic_id: uuid.UUID, reply_id: uuid.UUID, sort: str = "") -> str:
    """Opens the thread with this reply on the page, whatever the sort: long threads load in parts."""
    params = {**({"sort": sort} if sort in REPLY_SORTS and sort != "helpful" else {}), "reply": str(reply_id)}
    return f"/discussions/{topic_id}?{urlencode(params)}#reply-{reply_id}"


def refresh_reply_stats(db, topic: Topic) -> None:  # noqa: ANN001
    """Replies hidden by moderators count for nothing: not in the number, the last-reply time or Solved."""
    db.flush()
    count, last = db.execute(select(func.count(Reply.id), func.max(Reply.created_at)).where(Reply.topic_id == topic.id, Reply.hidden.is_(False))).one()
    topic.reply_count, topic.last_reply_at = count, last
    topic.solved = db.execute(select(Reply.id).where(Reply.topic_id == topic.id, Reply.hidden.is_(False), Reply.marked_helpful_by_author.is_(True))).first() is not None


def reply_visibility_changed(db, reply: Reply) -> None:  # noqa: ANN001
    """After a moderator hides or restores a reply: fix the topic's numbers and the search index."""
    refresh_reply_stats(db, db.get(Topic, reply.topic_id))
    if reply.hidden:
        remove_reply(db, reply.id)
    else:
        index_reply(db, reply.id, reply.topic_id, reply.body)


@router.post("/discussions/{topic_id}/replies", dependencies=[Depends(require_csrf)])
def reply_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser,
                 topic_id: uuid.UUID, body: str = Form(""), attached_session_id: str = Form("")):
    topic = visible_topic(request, db, topic_id)
    body = body.strip()[:20000]
    response = RedirectResponse(f"/discussions/{topic.id}#reply", status_code=303)
    if len(body) < 2:
        set_flash(response, settings, signer, "error", _("Write a reply first."))
        return response
    if not request.app.state.post_limiter.allow(str(user.id)):  # same quota as post_quota, but the reply stays in the box
        return topic_view(request, db, user, topic, draft=body, draft_session=attached_session_id, form_error=_(TOO_FAST), status_code=429)
    reply = Reply(topic=topic, user=user, body=body, attached_session=resolve_attachment(db, user, attached_session_id))
    db.add(reply)
    db.flush()
    refresh_reply_stats(db, topic)
    index_reply(db, reply.id, topic.id, body)
    url = reply_url(topic.id, reply.id)
    told = notify_mentions(db, user, body, url, f"“{topic.title}”")
    followers = db.execute(select(TopicFollow.user_id).where(TopicFollow.topic_id == topic.id).limit(MAX_FANOUT)).scalars().all()
    for person_id in ({topic.user_id, *followers} - told):  # one notification each, and notify() skips the author of the reply
        notify(db, person_id, user, f"@{user.handle} replied to “{topic.title}”", url)
    response.headers["location"] = url
    return response


def get_reply(request: Request, db, topic: Topic, reply_id: uuid.UUID) -> Reply:  # noqa: ANN001
    reply = db.execute(select(Reply).where(Reply.id == reply_id, Reply.topic_id == topic.id)).scalar_one_or_none()
    if reply is None or (reply.hidden and not getattr(request.state, "is_admin", False)):
        raise HTTPException(status_code=404)
    return reply


@router.post("/discussions/{topic_id}/follow", dependencies=[Depends(require_csrf)])
def topic_follow(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, topic_id: uuid.UUID):
    topic = visible_topic(request, db, topic_id)
    follow = db.execute(select(TopicFollow).where(TopicFollow.user_id == user.id, TopicFollow.topic_id == topic.id)).scalar_one_or_none()
    response = RedirectResponse(f"/discussions/{topic.id}", status_code=303)
    if follow is None:
        db.add(TopicFollow(user_id=user.id, topic_id=topic.id))
        set_flash(response, settings, signer, "ok", _("Following. New replies will show up in your notifications."))
    else:
        db.delete(follow)
        set_flash(response, settings, signer, "ok", _("No longer following this topic."))
    return response


@router.post("/discussions/{topic_id}/helpful", dependencies=[Depends(require_csrf)])
def topic_helpful(request: Request, db: DbDep, user: RequiredUser, topic_id: uuid.UUID):
    topic = visible_topic(request, db, topic_id)
    if topic.user_id != user.id:
        vote = db.execute(select(HelpfulVote).where(HelpfulVote.user_id == user.id, HelpfulVote.kind == "topic", HelpfulVote.ref_id == topic.id)).scalar_one_or_none()
        if vote is None:
            db.add(HelpfulVote(user_id=user.id, kind="topic", ref_id=topic.id))
        else:
            db.delete(vote)
    return RedirectResponse(f"/discussions/{topic.id}#opening-post", status_code=303)


@router.post("/discussions/{topic_id}/replies/{reply_id}/helpful", dependencies=[Depends(require_csrf)])
def reply_helpful(request: Request, db: DbDep, user: RequiredUser, topic_id: uuid.UUID, reply_id: uuid.UUID, sort: str = Form("")):
    topic = visible_topic(request, db, topic_id)
    reply = get_reply(request, db, topic, reply_id)
    if reply.user_id != user.id:
        vote = db.execute(select(ReplyVote).where(ReplyVote.reply_id == reply.id, ReplyVote.user_id == user.id)).scalar_one_or_none()
        if vote is None:
            db.add(ReplyVote(reply=reply, user_id=user.id))
            reply.helpful_count += 1
        else:
            db.delete(vote)
            reply.helpful_count = max(0, reply.helpful_count - 1)
    return RedirectResponse(reply_url(topic.id, reply.id, sort), status_code=303)


@router.post("/discussions/{topic_id}/replies/{reply_id}/mark", dependencies=[Depends(require_csrf)])
def reply_mark_helpful(request: Request, db: DbDep, user: RequiredUser, topic_id: uuid.UUID, reply_id: uuid.UUID, sort: str = Form("")):
    topic = visible_topic(request, db, topic_id)
    if topic.user_id != user.id and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403)
    reply = get_reply(request, db, topic, reply_id)
    if reply.user_id == topic.user_id:  # nobody marks the author's own answer as the one that solved the author's question
        raise HTTPException(status_code=403)
    reply.marked_helpful_by_author = not reply.marked_helpful_by_author
    if reply.marked_helpful_by_author:
        notify(db, reply.user_id, user, f"Your reply in “{topic.title}” was marked helpful", reply_url(topic.id, reply.id))
    refresh_reply_stats(db, topic)
    return RedirectResponse(reply_url(topic.id, reply.id, sort), status_code=303)


@router.post("/discussions/{topic_id}/replies/{reply_id}/delete", dependencies=[Depends(require_csrf)])
def reply_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser,
                 topic_id: uuid.UUID, reply_id: uuid.UUID, sort: str = Form("")):
    topic = get_topic(db, topic_id)
    reply = get_reply(request, db, topic, reply_id)
    if reply.user_id != user.id and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403)
    db.delete(reply)
    remove_reply(db, reply.id)
    refresh_reply_stats(db, topic)
    params = f"?{urlencode({'sort': sort})}" if sort in REPLY_SORTS and sort != "helpful" else ""
    response = RedirectResponse(f"/discussions/{topic.id}{params}#replies", status_code=303)
    set_flash(response, settings, signer, "ok", _("Reply deleted."))
    return response


@router.post("/discussions/{topic_id}/pin", dependencies=[Depends(require_csrf)])
def topic_pin(request: Request, db: DbDep, user: RequiredUser, topic_id: uuid.UUID):
    if not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403)
    topic = get_topic(db, topic_id)
    topic.pinned = not topic.pinned
    return RedirectResponse(f"/discussions/{topic.id}", status_code=303)


@router.post("/discussions/{topic_id}/delete", dependencies=[Depends(require_csrf)])
def topic_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, topic_id: uuid.UUID):
    topic = get_topic(db, topic_id)
    if topic.user_id != user.id and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403)
    remove_topic(db, topic.id)
    db.execute(delete(HelpfulVote).where(HelpfulVote.kind == "topic", HelpfulVote.ref_id == topic.id))
    db.execute(delete(Notification).where(Notification.url.like(f"/discussions/{topic.id}%")))  # they would lead to a 404
    db.delete(topic)
    response = RedirectResponse("/discussions", status_code=303)
    set_flash(response, settings, signer, "ok", _("Topic deleted."))
    return response


def topics_for_user(db, user_id: uuid.UUID, limit: int = 5, offset: int = 0) -> list[Topic]:  # noqa: ANN001
    return db.execute(topic_query().where(Topic.user_id == user_id, Topic.hidden.is_(False)).order_by(Topic.created_at.desc(), Topic.id).offset(offset).limit(limit)).scalars().all()


def topic_count_for_user(db, user_id: uuid.UUID) -> int:  # noqa: ANN001
    return db.scalar(select(func.count()).select_from(Topic).where(Topic.user_id == user_id, Topic.hidden.is_(False))) or 0


def helpful_reply_count(db, user_id: uuid.UUID) -> int:  # noqa: ANN001
    return db.scalar(
        select(func.count()).select_from(Reply)
        .where(Reply.user_id == user_id, (Reply.helpful_count > 0) | (Reply.marked_helpful_by_author.is_(True)))
    ) or 0


def latest_topics(db, limit: int = 30) -> list[Topic]:  # noqa: ANN001
    return db.execute(topic_query().where(Topic.hidden.is_(False)).order_by(Topic.created_at.desc()).limit(limit)).scalars().all()
