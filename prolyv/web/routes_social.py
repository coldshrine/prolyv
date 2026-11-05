"""Follow, notifications, saved items, reports, and the admin hub for users and moderation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from prolyv.auth.accounts import get_user_by_handle, is_admin_user, purge_sessions
from prolyv.models import (
    REPORT_REASONS,
    SAVE_KINDS,
    Follow,
    Notification,
    Post,
    PostComment,
    Reply,
    Report,
    SavedItem,
    SessionComment,
    Tea,
    TeaSession,
    TeaStatus,
    Topic,
    User,
    Visibility,
    utcnow,
)
from prolyv.social import notify
from prolyv.web.deps import AdminUser, DbDep, RequiredUser, SettingsDep, SignerDep, post_quota, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.routes_auth import safe_next
from prolyv.web.templating import render

router = APIRouter()


# ---------------------------------------------------------------------------------------------- follow


@router.post("/u/{handle}/follow", dependencies=[Depends(require_csrf)])
def follow_toggle(request: Request, db: DbDep, user: RequiredUser, handle: str):
    target = get_user_by_handle(db, handle.lower())
    if target is None:
        raise HTTPException(status_code=404)
    if target.id != user.id:
        row = db.execute(select(Follow).where(Follow.follower_id == user.id, Follow.followee_id == target.id)).scalar_one_or_none()
        if row is None:
            db.add(Follow(follower_id=user.id, followee_id=target.id))
            notify(db, target.id, user, f"@{user.handle} started following you", f"/u/{user.handle}")
        else:
            db.delete(row)
    return RedirectResponse(f"/u/{target.handle}", status_code=303)


# ---------------------------------------------------------------------------------------------- notifications


NOTIFICATION_PAGE = 30


@router.get("/notifications")
def notifications_page(request: Request, db: DbDep, user: RequiredUser, page: int = 1):
    page = max(1, page)
    rows = db.execute(
        select(Notification).options(selectinload(Notification.actor)).where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id).offset((page - 1) * NOTIFICATION_PAGE).limit(NOTIFICATION_PAGE + 1)
    ).scalars().all()
    context = {"notifications": rows[:NOTIFICATION_PAGE], "had_unread": {n.id for n in rows if n.read_at is None},
               "next_url": f"/notifications?page={page + 1}" if len(rows) > NOTIFICATION_PAGE else None}
    response = render(request, "social/notifications.html", context)
    # opening the page clears the badge, however long the list is
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    return response


# ---------------------------------------------------------------------------------------------- saved


def is_saved(db, user: User | None, kind: str, ref_id: uuid.UUID) -> bool:  # noqa: ANN001
    if user is None:
        return False
    return db.execute(select(SavedItem.id).where(SavedItem.user_id == user.id, SavedItem.kind == kind, SavedItem.ref_id == ref_id)).first() is not None


@router.post("/saved/toggle", dependencies=[Depends(require_csrf)])
def saved_toggle(request: Request, db: DbDep, user: RequiredUser, kind: str = Form(""), ref_id: uuid.UUID = Form(...), next: str = Form("/saved")):
    if kind not in SAVE_KINDS:
        raise HTTPException(status_code=400)
    row = db.execute(select(SavedItem).where(SavedItem.user_id == user.id, SavedItem.kind == kind, SavedItem.ref_id == ref_id)).scalar_one_or_none()
    if row is None:
        db.add(SavedItem(user_id=user.id, kind=kind, ref_id=ref_id))
    else:
        db.delete(row)
    return RedirectResponse(safe_next(next) or "/saved", status_code=303)


@router.get("/saved")
def saved_page(request: Request, db: DbDep, user: RequiredUser):
    from prolyv.web.routes_diary import session_query
    from prolyv.web.routes_forum import topic_query
    from prolyv.web.routes_teas import CARD_OPTIONS

    items = db.execute(select(SavedItem).where(SavedItem.user_id == user.id).order_by(SavedItem.created_at.desc())).scalars().all()
    ids = {kind: [i.ref_id for i in items if i.kind == kind] for kind in SAVE_KINDS}
    teas = db.execute(select(Tea).options(*CARD_OPTIONS).where(Tea.id.in_(ids["tea"]), Tea.status == TeaStatus.published)).scalars().all()
    sessions = db.execute(session_query().where(
        TeaSession.id.in_(ids["session"]), TeaSession.hidden.is_(False),
        (TeaSession.visibility == Visibility.public) | (TeaSession.user_id == user.id),
    )).scalars().all()
    topics = db.execute(topic_query().where(Topic.id.in_(ids["topic"]), Topic.hidden.is_(False))).scalars().all()
    posts = db.execute(select(Post).where(Post.id.in_(ids["post"]), Post.published_at.is_not(None))).scalars().all()
    return render(request, "social/saved.html", {"teas": teas, "sessions": sessions, "topics": topics, "posts": posts})


# ---------------------------------------------------------------------------------------------- reports


REPORTABLE = {"session": TeaSession, "session_comment": SessionComment, "topic": Topic, "reply": Reply, "post_comment": PostComment, "user": User}


def content_link(db, kind: str, ref_id: uuid.UUID) -> dict | None:  # noqa: ANN001
    """Where a reported thing lives, a short excerpt, and whether it is hidden."""
    obj = db.get(REPORTABLE[kind], ref_id)
    if obj is None:
        return None
    if kind == "session":
        return {"url": f"/sessions/{obj.id}", "excerpt": f"{obj.tea_name}: {obj.note[:160]}", "hidden": obj.hidden, "who": obj.user.handle}
    if kind == "session_comment":
        return {"url": f"/sessions/{obj.session_id}#comments", "excerpt": obj.body[:200], "hidden": obj.hidden, "who": obj.user.handle}
    if kind == "topic":
        return {"url": f"/discussions/{obj.id}", "excerpt": f"{obj.title}: {obj.body[:160]}", "hidden": obj.hidden, "who": obj.user.handle}
    if kind == "reply":
        return {"url": f"/discussions/{obj.topic_id}#reply-{obj.id}", "excerpt": obj.body[:200], "hidden": obj.hidden, "who": obj.user.handle}
    if kind == "post_comment":
        return {"url": f"/blog/{obj.post.slug}#comments", "excerpt": obj.body[:200], "hidden": obj.hidden, "who": obj.user.handle}
    return {"url": f"/u/{obj.handle}", "excerpt": obj.bio[:200] or obj.display_name, "hidden": None, "who": obj.handle}


@router.post("/report", dependencies=[Depends(require_csrf), Depends(post_quota)])
def report_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser,
                  kind: str = Form(""), ref_id: uuid.UUID = Form(...), reason: str = Form("other"), next: str = Form("/")):
    if kind not in REPORTABLE or reason not in REPORT_REASONS or db.get(REPORTABLE[kind], ref_id) is None:
        raise HTTPException(status_code=400)
    existing = db.execute(select(Report.id).where(Report.reporter_id == user.id, Report.kind == kind, Report.ref_id == ref_id)).first()
    if existing is None:
        db.add(Report(reporter_id=user.id, kind=kind, ref_id=ref_id, reason=reason))
    response = RedirectResponse(safe_next(next) or "/", status_code=303)
    set_flash(response, settings, signer, "ok", "Thanks. An admin will take a look.")
    return response


# ---------------------------------------------------------------------------------------------- admin hub


@router.get("/admin")
def admin_hub(request: Request, db: DbDep, user: AdminUser):
    counts = {
        "users": db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))) or 0,
        "teas": db.scalar(select(func.count()).select_from(Tea)) or 0,
        "posts": db.scalar(select(func.count()).select_from(Post)) or 0,
        "reports": db.scalar(select(func.count()).select_from(Report).where(Report.resolved_at.is_(None))) or 0,
        "topics": db.scalar(select(func.count()).select_from(Topic)) or 0,
        "sessions": db.scalar(select(func.count()).select_from(TeaSession)) or 0,
    }
    return render(request, "admin/hub.html", {"counts": counts})


@router.get("/admin/reports")
def admin_reports(request: Request, db: DbDep, user: AdminUser):
    rows = db.execute(
        select(Report).options(selectinload(Report.reporter)).order_by(Report.resolved_at.is_not(None), Report.created_at.desc()).limit(200)
    ).scalars().all()
    entries = [{"report": r, "target": content_link(db, r.kind, r.ref_id), "reason": REPORT_REASONS.get(r.reason, r.reason)} for r in rows]
    return render(request, "admin/reports.html", {"entries": entries})


@router.post("/admin/reports/{report_id}/resolve", dependencies=[Depends(require_csrf)])
def admin_report_resolve(request: Request, db: DbDep, user: AdminUser, report_id: uuid.UUID):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404)
    report.resolved_at = utcnow()
    return RedirectResponse("/admin/reports", status_code=303)


@router.post("/admin/hide", dependencies=[Depends(require_csrf)])
def admin_hide(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser,
               kind: str = Form(""), ref_id: uuid.UUID = Form(...), hidden: str = Form("1"), next: str = Form("/admin/reports")):
    if kind not in REPORTABLE or kind == "user":
        raise HTTPException(status_code=400)
    obj = db.get(REPORTABLE[kind], ref_id)
    if obj is None:
        raise HTTPException(status_code=404)
    obj.hidden = hidden == "1"
    response = RedirectResponse(safe_next(next) or "/admin/reports", status_code=303)
    set_flash(response, settings, signer, "ok", "Hidden from everyone except admins." if obj.hidden else "Visible again.")
    return response


@router.get("/admin/users")
def admin_users(request: Request, db: DbDep, user: AdminUser, q: str = "", page: int = 1):
    q = q.strip().lower()[:60]
    stmt = select(User).where(User.deleted_at.is_(None))
    if q:
        stmt = stmt.where(func.lower(User.handle).contains(q) | func.lower(User.display_name).contains(q))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, (total + 49) // 50)
    page = min(max(1, page), pages)
    users = db.execute(stmt.order_by(User.created_at.desc()).offset((page - 1) * 50).limit(50)).scalars().all()
    return render(request, "admin/users.html", {"users": users, "q": q, "page": page, "pages": pages, "total": total})


@router.post("/admin/users/{handle}/ban", dependencies=[Depends(require_csrf)])
def admin_user_ban(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, handle: str, banned: str = Form("1")):
    target = get_user_by_handle(db, handle.lower())
    if target is None:
        raise HTTPException(status_code=404)
    response = RedirectResponse("/admin/users", status_code=303)
    if target.id == user.id or is_admin_user(db, settings, target):
        set_flash(response, settings, signer, "error", "Admins can't be restricted here.")
        return response
    if banned == "1":
        target.banned_at = utcnow()
        purge_sessions(db, target)
        set_flash(response, settings, signer, "ok", f"@{target.handle} is restricted and signed out.")
    else:
        target.banned_at = None
        set_flash(response, settings, signer, "ok", f"@{target.handle} can sign in again.")
    return response
