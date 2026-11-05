"""Forum: sections, topics with attached sessions, replies with helpful votes, FTS5 search."""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from prolyv.auth.accounts import get_user_by_handle
from prolyv.models import ForumSection, Reply, ReplyVote, TeaSession, Topic, User, Visibility, utcnow
from prolyv.search import index_reply, index_topic, remove_reply, remove_topic, search_topics
from prolyv.social import notify, notify_followers
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, post_quota, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render
from prolyv.web.text import mentions

router = APIRouter()
PAGE_SIZE = 20


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


def list_topics(request: Request, db, user, section: ForumSection | None, q: str, page: int):  # noqa: ANN001
    q = q.strip()[:80]
    page = max(1, page)
    stmt = topic_query()
    if not getattr(request.state, "is_admin", False):
        stmt = stmt.where(Topic.hidden.is_(False))
    if section is not None:
        stmt = stmt.where(Topic.section_id == section.id)
    ranked: list[uuid.UUID] = []
    if q:
        ranked = search_topics(db, q)
        stmt = stmt.where(Topic.id.in_(ranked)) if ranked else stmt.where(False)  # noqa: E712
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    if ranked:
        topics = db.execute(stmt).scalars().all()
        order = {tid: n for n, tid in enumerate(ranked)}
        topics.sort(key=lambda t: order.get(t.id, 999))
        topics = topics[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    else:
        stmt = stmt.order_by(Topic.pinned.desc(), func.coalesce(Topic.last_reply_at, Topic.created_at).desc(), Topic.id)
        topics = db.execute(stmt.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).scalars().all()
    counts = dict(db.execute(select(Topic.section_id, func.count()).where(Topic.hidden.is_(False)).group_by(Topic.section_id)).all())
    sections = [(s, counts.get(s.id, 0)) for s in all_sections(db)]
    base = f"/discussions/s/{section.slug}" if section else "/discussions"

    def page_url(n: int) -> str:
        return f"{base}?{urlencode({**({'q': q} if q else {}), 'page': n})}"

    context = {"topics": topics, "sections": sections, "section": section, "q": q, "page": page, "pages": pages, "total": total,
               "page_url": page_url, "shown": min(page * PAGE_SIZE, total)}
    return render(request, "forum/index.html", context)


@router.get("/discussions")
def forum_index(request: Request, db: DbDep, user: CurrentUser, q: str = "", page: int = 1):
    return list_topics(request, db, user, None, q, page)


@router.get("/discussions/s/{slug}")
def forum_section(request: Request, db: DbDep, user: CurrentUser, slug: str, q: str = "", page: int = 1):
    section = db.execute(select(ForumSection).where(ForumSection.slug == slug)).scalar_one_or_none()
    if section is None:
        raise HTTPException(status_code=404)
    return list_topics(request, db, user, section, q, page)


@router.get("/discussions/new")
def topic_new_form(request: Request, db: DbDep, user: RequiredUser, section: str = "", session: str = "", ask: str = ""):
    asked = get_user_by_handle(db, ask.strip().lstrip("@").lower()) if ask else None
    form = {"section": section, "title": "", "body": f"@{asked.handle} " if asked else "", "attached_session_id": session}
    context = {"form": form, "errors": {}, "sections": all_sections(db), "my_sessions": my_public_sessions(db, user), "asked": asked}
    return render(request, "forum/new.html", context)


def notify_mentions(db, author: User, body: str, url: str, what: str) -> None:  # noqa: ANN001
    """An @handle in a post reaches that person. "Ask X in the forum" relies on this."""
    for handle in mentions(body):
        person = get_user_by_handle(db, handle)
        if person is not None:
            notify(db, person.id, author, f"@{author.handle} mentioned you in {what}", url)


def validate_topic(data, db) -> tuple[dict, dict]:  # noqa: ANN001
    form = {
        "section": (data.get("section") or "").strip(), "title": " ".join((data.get("title") or "").split())[:160],
        "body": (data.get("body") or "").strip()[:20000], "attached_session_id": (data.get("attached_session_id") or "").strip(),
    }
    errors: dict[str, str] = {}
    if len(form["title"]) < 5:
        errors["title"] = "Give the topic a title (at least 5 characters)."
    if len(form["body"]) < 10:
        errors["body"] = "Say a bit more (at least 10 characters)."
    section = db.execute(select(ForumSection).where(ForumSection.slug == form["section"])).scalar_one_or_none()
    if section is None:
        errors["section"] = "Pick a section."
    return form, errors


@router.post("/discussions/new", dependencies=[Depends(require_csrf), Depends(post_quota)])
async def topic_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    form, errors = validate_topic(await request.form(), db)
    if errors:
        context = {"form": form, "errors": errors, "sections": all_sections(db), "my_sessions": my_public_sessions(db, user)}
        return render(request, "forum/new.html", context, status_code=400)
    section = db.execute(select(ForumSection).where(ForumSection.slug == form["section"])).scalar_one()
    topic = Topic(section=section, user=user, title=form["title"], body=form["body"],
                  attached_session=resolve_attachment(db, user, form["attached_session_id"]))
    db.add(topic)
    db.flush()
    index_topic(db, topic.id, topic.title, topic.body)
    notify_followers(db, user, f"@{user.handle} started a topic: {topic.title}", f"/discussions/{topic.id}")
    notify_mentions(db, user, topic.body, f"/discussions/{topic.id}", f"“{topic.title}”")
    response = RedirectResponse(f"/discussions/{topic.id}", status_code=303)
    set_flash(response, settings, signer, "ok", "Topic posted.")
    return response


def get_topic(db, topic_id: uuid.UUID) -> Topic:  # noqa: ANN001
    topic = db.execute(topic_query().where(Topic.id == topic_id)).scalar_one_or_none()
    if topic is None:
        raise HTTPException(status_code=404)
    return topic


@router.get("/discussions/{topic_id}")
def topic_page(request: Request, db: DbDep, user: CurrentUser, topic_id: uuid.UUID):
    topic = get_topic(db, topic_id)
    is_admin = getattr(request.state, "is_admin", False)
    if topic.hidden and not is_admin:
        raise HTTPException(status_code=404)
    replies = db.execute(
        select(Reply).where(Reply.topic_id == topic.id, Reply.hidden.is_(False) if not is_admin else True)
        .options(selectinload(Reply.user), selectinload(Reply.attached_session).selectinload(TeaSession.steeps),
                 selectinload(Reply.attached_session).selectinload(TeaSession.user),
                 selectinload(Reply.attached_session).selectinload(TeaSession.tea),
                 selectinload(Reply.attached_session).selectinload(TeaSession.photos),
                 selectinload(Reply.attached_session).selectinload(TeaSession.comments))
        .order_by(Reply.created_at)
    ).scalars().all()
    my_votes = set()
    if user is not None and replies:
        my_votes = set(db.execute(
            select(ReplyVote.reply_id).where(ReplyVote.user_id == user.id, ReplyVote.reply_id.in_([r.id for r in replies]))
        ).scalars().all())
    from prolyv.web.routes_social import is_saved

    context = {
        "topic": topic, "replies": replies, "my_votes": my_votes, "saved": is_saved(db, user, "topic", topic.id),
        "is_author": user is not None and user.id == topic.user_id, "is_admin_view": is_admin,
        "my_sessions": my_public_sessions(db, user),
    }
    return render(request, "forum/topic.html", context)


@router.post("/discussions/{topic_id}/replies", dependencies=[Depends(require_csrf), Depends(post_quota)])
def reply_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser,
                 topic_id: uuid.UUID, body: str = Form(""), attached_session_id: str = Form("")):
    topic = get_topic(db, topic_id)
    if topic.hidden and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=404)
    body = body.strip()[:20000]
    response = RedirectResponse(f"/discussions/{topic.id}", status_code=303)
    if len(body) < 2:
        set_flash(response, settings, signer, "error", "Write a reply first.")
        return response
    reply = Reply(topic=topic, user=user, body=body, attached_session=resolve_attachment(db, user, attached_session_id))
    db.add(reply)
    topic.reply_count += 1
    topic.last_reply_at = utcnow()
    db.flush()
    index_reply(db, reply.id, topic.id, body)
    notify(db, topic.user_id, user, f"@{user.handle} replied to “{topic.title}”", f"/discussions/{topic.id}#reply-{reply.id}")
    notify_mentions(db, user, body, f"/discussions/{topic.id}#reply-{reply.id}", f"“{topic.title}”")
    response.headers["location"] = f"/discussions/{topic.id}#reply-{reply.id}"
    return response


def get_reply(db, topic: Topic, reply_id: uuid.UUID) -> Reply:  # noqa: ANN001
    reply = db.execute(select(Reply).where(Reply.id == reply_id, Reply.topic_id == topic.id)).scalar_one_or_none()
    if reply is None:
        raise HTTPException(status_code=404)
    return reply


@router.post("/discussions/{topic_id}/replies/{reply_id}/helpful", dependencies=[Depends(require_csrf)])
def reply_helpful(request: Request, db: DbDep, user: RequiredUser, topic_id: uuid.UUID, reply_id: uuid.UUID):
    topic = get_topic(db, topic_id)
    reply = get_reply(db, topic, reply_id)
    if reply.user_id != user.id:
        vote = db.execute(select(ReplyVote).where(ReplyVote.reply_id == reply.id, ReplyVote.user_id == user.id)).scalar_one_or_none()
        if vote is None:
            db.add(ReplyVote(reply=reply, user_id=user.id))
            reply.helpful_count += 1
        else:
            db.delete(vote)
            reply.helpful_count = max(0, reply.helpful_count - 1)
    return RedirectResponse(f"/discussions/{topic.id}#reply-{reply.id}", status_code=303)


@router.post("/discussions/{topic_id}/replies/{reply_id}/mark", dependencies=[Depends(require_csrf)])
def reply_mark_helpful(request: Request, db: DbDep, user: RequiredUser, topic_id: uuid.UUID, reply_id: uuid.UUID):
    topic = get_topic(db, topic_id)
    if topic.user_id != user.id and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403)
    reply = get_reply(db, topic, reply_id)
    reply.marked_helpful_by_author = not reply.marked_helpful_by_author
    if reply.marked_helpful_by_author:
        notify(db, reply.user_id, user, f"Your reply in “{topic.title}” was marked helpful", f"/discussions/{topic.id}#reply-{reply.id}")
    db.flush()
    topic.solved = db.scalar(select(func.count()).select_from(Reply).where(Reply.topic_id == topic.id, Reply.marked_helpful_by_author.is_(True))) > 0
    return RedirectResponse(f"/discussions/{topic.id}#reply-{reply.id}", status_code=303)


@router.post("/discussions/{topic_id}/replies/{reply_id}/delete", dependencies=[Depends(require_csrf)])
def reply_delete(request: Request, db: DbDep, user: RequiredUser, topic_id: uuid.UUID, reply_id: uuid.UUID):
    topic = get_topic(db, topic_id)
    reply = get_reply(db, topic, reply_id)
    if reply.user_id != user.id and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=403)
    db.delete(reply)
    remove_reply(db, reply.id)
    topic.reply_count = max(0, topic.reply_count - 1)
    db.flush()
    topic.solved = db.scalar(select(func.count()).select_from(Reply).where(Reply.topic_id == topic.id, Reply.marked_helpful_by_author.is_(True))) > 0
    return RedirectResponse(f"/discussions/{topic.id}", status_code=303)


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
    db.delete(topic)
    response = RedirectResponse("/discussions", status_code=303)
    set_flash(response, settings, signer, "ok", "Topic deleted.")
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
