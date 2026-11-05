"""Blog: admin-written Markdown posts with TikTok/Instagram embeds, brew-along sessions and comments."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from prolyv.handles import slugify
from prolyv.markdown import render_markdown
from prolyv.models import Post, PostComment, Tea, TeaSession, TeaStatus, Visibility, utcnow
from prolyv.search import index_post, remove
from prolyv.social import notify
from prolyv.web.deps import (
    AdminUser,
    CurrentUser,
    DbDep,
    RequiredUser,
    SettingsDep,
    SignerDep,
    post_quota,
    require_csrf,
)
from prolyv.web.flash import set_flash
from prolyv.web.templating import render

router = APIRouter()
PAGE_SIZE = 10


def post_query():
    return select(Post).options(selectinload(Post.author), selectinload(Post.tea).selectinload(Tea.images))


def published_posts():
    return post_query().where(Post.published_at.is_not(None))


def latest_posts(db, limit: int = 30) -> list[Post]:  # noqa: ANN001
    return db.execute(published_posts().order_by(Post.published_at.desc()).limit(limit)).scalars().all()


@router.get("/blog")
def blog_index(request: Request, db: DbDep, user: CurrentUser, page: int = 1):
    page = max(1, page)
    total = db.scalar(select(func.count()).select_from(Post).where(Post.published_at.is_not(None))) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    posts = db.execute(published_posts().order_by(Post.published_at.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).scalars().all()
    drafts = 0
    if getattr(request.state, "is_admin", False):
        drafts = db.scalar(select(func.count()).select_from(Post).where(Post.published_at.is_(None))) or 0
    return render(request, "blog/index.html", {"posts": posts, "page": page, "pages": pages, "total": total, "drafts": drafts})


def get_post(db, slug: str, is_admin: bool) -> Post:  # noqa: ANN001
    post = db.execute(post_query().where(Post.slug == slug.lower())).scalar_one_or_none()
    if post is None or (not post.is_published and not is_admin):
        raise HTTPException(status_code=404)
    return post


@router.get("/blog/{slug}")
def blog_post(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, slug: str):
    is_admin = getattr(request.state, "is_admin", False)
    post = get_post(db, slug, is_admin)
    brew_alongs = db.execute(
        select(TeaSession).options(selectinload(TeaSession.steeps), selectinload(TeaSession.user), selectinload(TeaSession.tea))
        .where(TeaSession.post_id == post.id, TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False))
        .order_by(TeaSession.brewed_at.desc()).limit(12)
    ).scalars().all()
    brew_along_count = db.scalar(select(func.count()).select_from(TeaSession).where(TeaSession.post_id == post.id, TeaSession.visibility == Visibility.public)) or 0
    og = {"title": f"{post.title} · Prolyv blog", "description": post.summary or post.title, "url": f"{settings.base_url}/blog/{post.slug}"}
    if post.cover_url:
        og["image"] = post.cover_url
    elif post.tea and post.tea.primary_image:
        og["image"] = post.tea.primary_image.thumb_url or post.tea.primary_image.url
    from prolyv.web.routes_social import is_saved

    comments = [c for c in post.comments if not c.hidden or is_admin]
    context = {"post": post, "brew_alongs": brew_alongs, "brew_along_count": brew_along_count, "og": og, "is_admin_view": is_admin,
               "comments": comments, "saved": is_saved(db, user, "post", post.id)}
    return render(request, "blog/post.html", context)


@router.post("/blog/{slug}/comments", dependencies=[Depends(require_csrf), Depends(post_quota)])
def post_comment_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, slug: str, body: str = Form("")):
    post = get_post(db, slug, getattr(request.state, "is_admin", False))
    body = body.strip()[:2000]
    response = RedirectResponse(f"/blog/{post.slug}#comments", status_code=303)
    if len(body) < 2:
        set_flash(response, settings, signer, "error", "Write a comment first.")
        return response
    db.add(PostComment(post=post, user=user, body=body))
    notify(db, post.author_id, user, f"@{user.handle} commented on “{post.title}”", f"/blog/{post.slug}#comments")
    return response


@router.post("/blog/{slug}/comments/{comment_id}/delete", dependencies=[Depends(require_csrf)])
def post_comment_delete(request: Request, db: DbDep, user: RequiredUser, slug: str, comment_id: uuid.UUID):
    is_admin = getattr(request.state, "is_admin", False)
    post = get_post(db, slug, is_admin)
    comment = next((c for c in post.comments if c.id == comment_id), None)
    if comment is None:
        raise HTTPException(status_code=404)
    if comment.user_id != user.id and not is_admin:
        raise HTTPException(status_code=403)
    db.delete(comment)
    return RedirectResponse(f"/blog/{post.slug}#comments", status_code=303)


# ------------------------------------------------------------------------------------------ admin


def unique_post_slug(db, wanted: str, exclude: uuid.UUID | None = None) -> str:  # noqa: ANN001
    base = slugify(wanted).replace("_", "-").strip("-")[:100] or "post"
    candidate, n = base, 1
    while True:
        stmt = select(Post.id).where(Post.slug == candidate)
        if exclude is not None:
            stmt = stmt.where(Post.id != exclude)
        if db.execute(stmt).first() is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def read_post_form(data) -> tuple[dict, dict]:  # noqa: ANN001
    form = {k: (data.get(k) or "").strip() for k in ("title", "slug", "summary", "body_md", "cover_url", "video_url", "tea_slug")}
    form["publish"] = data.get("publish") in ("on", "1", "true")
    errors: dict[str, str] = {}
    if len(form["title"]) < 3:
        errors["title"] = "Give the post a title."
    if len(form["body_md"]) < 20:
        errors["body_md"] = "The post needs a body (Markdown)."
    for key in ("cover_url", "video_url"):
        if form[key] and not form[key].startswith(("http://", "https://")):
            errors[key] = "Use a full https:// link."
    return form, errors


def apply_post_form(db, post: Post, form: dict) -> None:  # noqa: ANN001
    post.title = form["title"][:160]
    post.slug = unique_post_slug(db, form["slug"] or form["title"], exclude=post.id)
    post.summary = form["summary"][:300]
    post.body_md = form["body_md"]
    post.body_html = render_markdown(form["body_md"])
    post.cover_url = form["cover_url"] or None
    post.video_url = form["video_url"] or None
    post.tea = None
    if form["tea_slug"]:
        post.tea = db.execute(select(Tea).where(Tea.slug == form["tea_slug"].lower(), Tea.status == TeaStatus.published)).scalar_one_or_none()
    if form["publish"] and post.published_at is None:
        post.published_at = utcnow()
    elif not form["publish"]:
        post.published_at = None


def form_from_post(post: Post) -> dict:
    return {"title": post.title, "slug": post.slug, "summary": post.summary, "body_md": post.body_md, "cover_url": post.cover_url or "",
            "video_url": post.video_url or "", "tea_slug": post.tea.slug if post.tea else "", "publish": post.is_published}


@router.get("/admin/posts")
def admin_posts(request: Request, db: DbDep, user: AdminUser):
    posts = db.execute(post_query().order_by(Post.published_at.is_(None).desc(), Post.updated_at.desc())).scalars().all()
    return render(request, "admin/posts.html", {"posts": posts})


@router.get("/admin/posts/new")
def admin_post_new_form(request: Request, user: AdminUser):
    return render(request, "admin/post_edit.html", {"form": {"publish": False}, "errors": {}, "post": None})


@router.post("/admin/posts/new", dependencies=[Depends(require_csrf)])
async def admin_post_new(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser):
    form, errors = read_post_form(await request.form())
    if errors:
        return render(request, "admin/post_edit.html", {"form": form, "errors": errors, "post": None}, status_code=400)
    post = Post(author=user, slug="pending", title=form["title"])
    db.add(post)
    db.flush()
    apply_post_form(db, post, form)
    db.flush()
    index_post(db, post)
    response = RedirectResponse(f"/blog/{post.slug}", status_code=303)
    set_flash(response, settings, signer, "ok", "Published." if post.is_published else "Saved as a draft (only admins can see it).")
    return response


def admin_get_post(db, post_id: uuid.UUID) -> Post:  # noqa: ANN001
    post = db.execute(post_query().where(Post.id == post_id)).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404)
    return post


@router.get("/admin/posts/{post_id}/edit")
def admin_post_edit_form(request: Request, db: DbDep, user: AdminUser, post_id: uuid.UUID):
    post = admin_get_post(db, post_id)
    return render(request, "admin/post_edit.html", {"form": form_from_post(post), "errors": {}, "post": post})


@router.post("/admin/posts/{post_id}/edit", dependencies=[Depends(require_csrf)])
async def admin_post_edit(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, post_id: uuid.UUID):
    post = admin_get_post(db, post_id)
    form, errors = read_post_form(await request.form())
    if errors:
        return render(request, "admin/post_edit.html", {"form": form, "errors": errors, "post": post}, status_code=400)
    apply_post_form(db, post, form)
    post.updated_at = utcnow()
    index_post(db, post)
    response = RedirectResponse(f"/blog/{post.slug}", status_code=303)
    set_flash(response, settings, signer, "ok", "Post saved.")
    return response


@router.post("/admin/posts/{post_id}/delete", dependencies=[Depends(require_csrf)])
def admin_post_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, post_id: uuid.UUID):
    post = admin_get_post(db, post_id)
    remove(db, "post", post.id)
    db.delete(post)
    response = RedirectResponse("/admin/posts", status_code=303)
    set_flash(response, settings, signer, "ok", "Post deleted.")
    return response
