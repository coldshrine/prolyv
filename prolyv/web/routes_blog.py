"""Blog: admin-written Markdown posts with TikTok/Instagram embeds, brew-along sessions and comments."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import selectinload

from prolyv.brand import SITE_NAME
from prolyv.handles import slugify
from prolyv.i18n import N_, _
from prolyv.markdown import insecure_images, render_markdown
from prolyv.models import HelpfulVote, Post, PostComment, Tea, TeaSession, TeaStatus, Topic, Visibility, utcnow, video_embed
from prolyv.search import index_post, remove
from prolyv.social import notify
from prolyv.web.cup import cup_styles
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
ALONG_PAGE = 12
COMMENT_PAGE = 10
TABS = (("all", N_("All")), ("video", N_("Video breakdowns")), ("guide", N_("Guides")))  # labels shown with _()
HAS_VIDEO = func.coalesce(Post.video_url, "") != ""


def post_query():
    return select(Post).options(selectinload(Post.author), selectinload(Post.tea).selectinload(Tea.images))


def published_posts():
    return post_query().where(Post.published_at.is_not(None))


def latest_posts(db, limit: int = 30) -> list[Post]:  # noqa: ANN001
    return db.execute(published_posts().order_by(Post.published_at.desc()).limit(limit)).scalars().all()


def shown_sessions(*conditions):  # noqa: ANN001, ANN201
    """What counts as brewing along in public: attached to a post, public, not hidden by a moderator."""
    return (TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False), *conditions)


def comment_counts(db, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:  # noqa: ANN001
    stmt = select(PostComment.post_id, func.count()).where(PostComment.post_id.in_(post_ids), PostComment.hidden.is_(False)).group_by(PostComment.post_id)
    return dict(db.execute(stmt).all()) if post_ids else {}


def along_counts(db, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:  # noqa: ANN001
    stmt = select(TeaSession.post_id, func.count()).where(*shown_sessions(TeaSession.post_id.in_(post_ids))).group_by(TeaSession.post_id)
    return dict(db.execute(stmt).all()) if post_ids else {}


def most_brewed(db, limit: int = 3) -> list[tuple[Post, int]]:  # noqa: ANN001
    count = func.count(TeaSession.id)
    stmt = (select(Post, count).join(TeaSession, TeaSession.post_id == Post.id)
            .where(Post.published_at.is_not(None), *shown_sessions()).group_by(Post.id).order_by(desc(count), Post.published_at.desc()).limit(limit))
    return [(post, n) for post, n in db.execute(stmt).all()]


def social_links(settings) -> list[dict]:  # noqa: ANN001
    """The hero pills exist only for accounts set in the settings; the @name comes from the address."""
    links = []
    for provider, label, url in (("tiktok", "TikTok", settings.social_tiktok), ("instagram", "Instagram", settings.social_instagram)):
        if url:
            name = re.search(r"\.com/@?([\w.\-]+)", url)
            links.append({"provider": provider, "url": url, "label": f"{label} @{name.group(1)}" if name else label})
    return links


@router.get("/blog")
def blog_index(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, page: int = 1, tab: str = "all"):
    tab = tab if tab in dict(TABS) else "all"
    where = {"video": (HAS_VIDEO,), "guide": (~HAS_VIDEO,)}.get(tab, ())
    total = db.scalar(select(func.count()).select_from(Post).where(Post.published_at.is_not(None), *where)) or 0
    ordered = published_posts().where(*where).order_by(Post.published_at.desc(), Post.id)
    skip = 1 if tab == "all" and total else 0  # All opens with the newest post as the big card, and the list starts after it
    pages = max(1, (total - skip + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), pages)
    posts = db.execute(ordered.offset(skip + (page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).scalars().all()
    latest = db.execute(ordered.limit(1)).scalar_one_or_none() if skip and page == 1 else None
    ids = [post.id for post in posts] + ([latest.id] if latest else [])
    drafts = 0
    if getattr(request.state, "is_admin", False):
        drafts = db.scalar(select(func.count()).select_from(Post).where(Post.published_at.is_(None))) or 0
    tab_query = "" if tab == "all" else f"tab={tab}&"
    return render(request, "blog/index.html", {
        "posts": posts, "latest": latest, "page": page, "pages": pages, "total": total, "drafts": drafts, "tab": tab, "tabs": TABS,
        "tab_query": tab_query, "comment_counts": comment_counts(db, ids), "along_counts": along_counts(db, ids), "popular": most_brewed(db),
        "social": social_links(settings),
    })


def get_post(db, slug: str, is_admin: bool) -> Post:  # noqa: ANN001
    post = db.execute(post_query().where(Post.slug == slug.lower())).scalar_one_or_none()
    if post is None or (not post.is_published and not is_admin):
        raise HTTPException(status_code=404)
    return post


def embed_frame(embed: dict | None) -> str | None:
    """The player that replaces the cover when someone presses play."""
    if embed and embed["provider"] == "tiktok":
        return f"https://www.tiktok.com/player/v1/{embed['id']}"
    if embed and embed["provider"] == "instagram":
        return f"{embed['url']}embed/"
    return None


def brew_summary(db, post: Post) -> dict:  # noqa: ANN001
    """Heading and summary line of the brew-along block, from every public session and not only the page on screen."""
    rows = db.execute(select(TeaSession.user_id, TeaSession.rating, TeaSession.taste_tags).where(*shown_sessions(TeaSession.post_id == post.id))).all()
    ratings = [row.rating for row in rows if row.rating]
    tags = Counter(tag for row in rows for tag in (row.taste_tags or []) if tag)
    top_tag = min(tags, key=lambda tag: (-tags[tag], tag)) if tags else None
    return {"sessions": len(rows), "people": len({row.user_id for row in rows}), "average": round(sum(ratings) / len(ratings), 1) if ratings else None, "top_tag": top_tag}


def session_params(tea: Tea | None) -> list[dict]:
    """The parameter tiles, from the tea card. Without numbers there is nothing to show."""
    brewing = (tea.brewing if tea else None) or {}
    tiles = []
    if brewing.get("grams"):
        tiles.append({"k": _("Leaf"), "v": _("{grams} g", grams=f"{brewing['grams']:g}")})
    if brewing.get("water_ml"):
        tiles.append({"k": _("Water"), "v": _("{ml} ml", ml=brewing["water_ml"])})
    if brewing.get("temp_c"):
        tiles.append({"k": _("Temp"), "v": f"{brewing['temp_c']} °C"})
    if brewing.get("steeps"):
        tiles.append({"k": _("Steeps"), "v": str(brewing["steeps"])})
    return tiles


def steep_rows(tea: Tea | None) -> list[dict]:
    """One row per real steep time on the tea card. The cup colours go along so the page script only has to swap them."""
    seconds = [s for s in ((tea.brewing if tea else None) or {}).get("seconds") or [] if isinstance(s, int | float) and s > 0]
    rows = []
    for n, value in enumerate(seconds, start=1):
        style = cup_styles(tea.category.value, 200, tea.liquor_hex, n)
        rows.append({"n": n, "seconds": f"{value:g}", "swirl": style["swirl"], "core": style["core"]})
    return rows


def linked_topic(db, post: Post) -> Topic | None:  # noqa: ANN001
    """A forum topic counts as this post's thread only when its text links to the post."""
    address = f"/blog/{post.slug}"
    topics = db.execute(
        select(Topic).where(Topic.hidden.is_(False), Topic.body.contains(address, autoescape=True))
        .order_by(Topic.reply_count.desc(), func.coalesce(Topic.last_reply_at, Topic.created_at).desc()).limit(20)
    ).scalars().all()
    whole = re.compile(re.escape(address) + r"(?![\w\-])")
    return next((topic for topic in topics if whole.search(topic.body)), None)


def helpful_state(db, user, comment_ids: list[uuid.UUID]) -> tuple[dict[uuid.UUID, int], set[uuid.UUID]]:  # noqa: ANN001
    if not comment_ids:
        return {}, set()
    where = (HelpfulVote.kind == "post_comment", HelpfulVote.ref_id.in_(comment_ids))
    counts = dict(db.execute(select(HelpfulVote.ref_id, func.count()).where(*where).group_by(HelpfulVote.ref_id)).all())
    mine = set(db.scalars(select(HelpfulVote.ref_id).where(*where, HelpfulVote.user_id == user.id))) if user else set()
    return counts, mine


def post_context(request: Request, db, settings, user, post: Post, along: int = 1, cpage: int = 1) -> dict:  # noqa: ANN001
    is_admin = getattr(request.state, "is_admin", False)
    summary = brew_summary(db, post)
    along_pages = max(1, (summary["sessions"] + ALONG_PAGE - 1) // ALONG_PAGE)
    along = min(max(1, along), along_pages)
    brew_alongs = db.execute(
        select(TeaSession).options(selectinload(TeaSession.steeps), selectinload(TeaSession.user), selectinload(TeaSession.tea))
        .where(*shown_sessions(TeaSession.post_id == post.id))
        .order_by(TeaSession.brewed_at.desc(), TeaSession.id).offset((along - 1) * ALONG_PAGE).limit(ALONG_PAGE)
    ).scalars().all()

    shown_comment = (PostComment.post_id == post.id,) if is_admin else (PostComment.post_id == post.id, PostComment.hidden.is_(False))
    comment_total = db.scalar(select(func.count()).select_from(PostComment).where(*shown_comment)) or 0
    comment_pages = max(1, (comment_total + COMMENT_PAGE - 1) // COMMENT_PAGE)
    cpage = min(max(1, cpage), comment_pages)
    comments = db.execute(
        select(PostComment).options(selectinload(PostComment.user)).where(*shown_comment)
        .order_by(PostComment.created_at.desc(), PostComment.id).offset((cpage - 1) * COMMENT_PAGE).limit(COMMENT_PAGE)
    ).scalars().all()
    helpful_counts, helpful_mine = helpful_state(db, user, [c.id for c in comments])

    og = {"title": f"{post.title} · {SITE_NAME}", "description": post.summary or post.title, "url": f"{settings.base_url}/blog/{post.slug}"}
    if post.cover_url:
        og["image"] = post.cover_url
    elif post.tea and post.tea.primary_image:
        og["image"] = post.tea.primary_image.thumb_url or post.tea.primary_image.url
    from prolyv.web.routes_social import is_saved

    return {
        "post": post, "post_url": f"/blog/{post.slug}", "og": og, "is_admin_view": is_admin, "saved": is_saved(db, user, "post", post.id),
        "embed": post.embed, "embed_src": embed_frame(post.embed), "params": session_params(post.tea), "steeps": steep_rows(post.tea),
        "brew_alongs": brew_alongs, "brew": summary, "along_page": along, "along_pages": along_pages, "topic": linked_topic(db, post),
        "comments": comments, "comment_total": comment_total, "comment_page": cpage, "comment_pages": comment_pages,
        "comments_left": comment_total - cpage * COMMENT_PAGE, "helpful_counts": helpful_counts, "helpful_mine": helpful_mine,
        "comment_error": None, "comment_body": "",
    }


@router.get("/blog/{slug}")
def blog_post(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, slug: str, along: int = 1, cpage: int = 1):
    post = get_post(db, slug, getattr(request.state, "is_admin", False))
    return render(request, "blog/post.html", post_context(request, db, settings, user, post, along, cpage))


@router.post("/blog/{slug}/comments", dependencies=[Depends(require_csrf)])
def post_comment_create(request: Request, db: DbDep, settings: SettingsDep, user: RequiredUser, slug: str, body: str = Form("")):
    post = get_post(db, slug, getattr(request.state, "is_admin", False))
    typed = body[:2000]
    text = typed.strip()

    def refused(message: str, status_code: int):  # noqa: ANN202
        context = post_context(request, db, settings, user, post)
        context.update(comment_error=message, comment_body=typed)
        return render(request, "blog/post.html", context, status_code=status_code)

    if len(text) < 2:
        return refused(_("Write a comment first."), 400)
    try:  # the same quota as every other post, called here so the refusal keeps what the person typed
        post_quota(request, user)
    except HTTPException:
        return refused(_("You are posting a lot right now. Your text is still here: try again in a little while."), 429)
    db.add(PostComment(post=post, user=user, body=text))
    url = f"/blog/{post.slug}#comments"
    notify(db, post.author_id, user, f"@{user.handle} commented on “{post.title}”", url)
    from prolyv.web.routes_forum import notify_mentions

    notify_mentions(db, user, text, url, f"a comment on “{post.title}”")
    return RedirectResponse(url, status_code=303)


def get_comment(db, post: Post, comment_id: uuid.UUID) -> PostComment:  # noqa: ANN001
    comment = db.get(PostComment, comment_id)
    if comment is None or comment.post_id != post.id:
        raise HTTPException(status_code=404)
    return comment


@router.post("/blog/{slug}/comments/{comment_id}/helpful", dependencies=[Depends(require_csrf)])
def post_comment_helpful(request: Request, db: DbDep, user: RequiredUser, slug: str, comment_id: uuid.UUID):
    post = get_post(db, slug, getattr(request.state, "is_admin", False))
    comment = get_comment(db, post, comment_id)
    if comment.hidden and not getattr(request.state, "is_admin", False):
        raise HTTPException(status_code=404)
    if comment.user_id != user.id:
        where = (HelpfulVote.kind == "post_comment", HelpfulVote.ref_id == comment.id, HelpfulVote.user_id == user.id)
        vote = db.execute(select(HelpfulVote).where(*where)).scalar_one_or_none()
        if vote is None:
            db.add(HelpfulVote(user_id=user.id, kind="post_comment", ref_id=comment.id))
        else:
            db.delete(vote)
        db.flush()
    if request.headers.get("hx-request"):
        counts, mine = helpful_state(db, user, [comment.id])
        return render(request, "blog/_helpful.html", {"c": comment, "post": post, "post_url": f"/blog/{post.slug}", "helpful_counts": counts, "helpful_mine": mine})
    return RedirectResponse(f"/blog/{post.slug}#comment-{comment.id}", status_code=303)


@router.post("/blog/{slug}/comments/{comment_id}/delete", dependencies=[Depends(require_csrf)])
def post_comment_delete(request: Request, db: DbDep, user: RequiredUser, slug: str, comment_id: uuid.UUID):
    is_admin = getattr(request.state, "is_admin", False)
    post = get_post(db, slug, is_admin)
    comment = get_comment(db, post, comment_id)
    if comment.user_id != user.id and not is_admin:
        raise HTTPException(status_code=403)
    db.execute(delete(HelpfulVote).where(HelpfulVote.kind == "post_comment", HelpfulVote.ref_id == comment.id))
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


def is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def check_video_url(url: str) -> tuple[str, str | None]:
    """(address to store, error). video_embed only knows the canonical forms, so the form brings a link to one or says why it can't."""
    if not url.startswith("https://"):
        return url, _("Use a full https:// link.")
    profile_reel = re.match(r"https://(?:www\.)?instagram\.com/[\w.\-]+/(p|reel|reels)/([\w\-]+)", url)
    if profile_reel:
        url = f"https://www.instagram.com/{'p' if profile_reel.group(1) == 'p' else 'reel'}/{profile_reel.group(2)}/"
    tiktok_mobile = re.match(r"https://m\.tiktok\.com/(@[\w.\-]+/video/\d+)", url)
    if tiktok_mobile:
        url = f"https://www.tiktok.com/{tiktok_mobile.group(1)}"
    host = (urlsplit(url).hostname or "").lower()
    if video_embed(url)["provider"] == "link":
        if host in ("vm.tiktok.com", "vt.tiktok.com") or (is_host(host, "tiktok.com") and urlsplit(url).path.startswith("/t/")):
            return url, _("TikTok short links can't be embedded. Open the link in a browser and paste the full address: tiktok.com/@account/video/1234…")
        if is_host(host, "tiktok.com"):
            return url, _("This TikTok link can't be embedded. Paste the full address: tiktok.com/@account/video/1234…")
        if is_host(host, "instagram.com"):
            return url, _("This Instagram link can't be embedded. Paste the address of the post or reel: instagram.com/reel/AbC123/")
    return url, None


def read_post_form(data, db) -> tuple[dict, dict]:  # noqa: ANN001
    form = {k: (data.get(k) or "").strip() for k in ("title", "slug", "summary", "body_md", "cover_url", "video_url", "tea_slug")}
    form["publish"] = data.get("publish") in ("on", "1", "true")
    errors: dict[str, str] = {}
    if len(form["title"]) < 3:
        errors["title"] = _("Give the post a title.")
    if len(form["body_md"]) < 20:
        errors["body_md"] = _("The post needs a body (Markdown).")
    else:
        blocked = insecure_images(form["body_md"])
        if blocked:
            errors["body_md"] = _("Images need an https:// link, browsers block this one: {url}", url=blocked[0])
    if form["cover_url"] and not form["cover_url"].startswith("https://"):
        errors["cover_url"] = _("Use a full https:// link.")
    if form["video_url"]:
        form["video_url"], video_error = check_video_url(form["video_url"])
        if video_error:
            errors["video_url"] = video_error
    if form["tea_slug"]:
        form["tea_slug"] = form["tea_slug"].lower()
        if db.execute(select(Tea.id).where(Tea.slug == form["tea_slug"], Tea.status == TeaStatus.published)).first() is None:
            errors["tea_slug"] = _("No published tea with this slug. Copy it from the end of the tea's address.")
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
    form, errors = read_post_form(await request.form(), db)
    if errors:
        return render(request, "admin/post_edit.html", {"form": form, "errors": errors, "post": None}, status_code=400)
    post = Post(author=user, slug="pending", title=form["title"])
    db.add(post)
    db.flush()
    apply_post_form(db, post, form)
    db.flush()
    index_post(db, post)
    response = RedirectResponse(f"/blog/{post.slug}", status_code=303)
    set_flash(response, settings, signer, "ok", _("Published.") if post.is_published else _("Saved as a draft (only admins can see it)."))
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
    form, errors = read_post_form(await request.form(), db)
    if errors:
        return render(request, "admin/post_edit.html", {"form": form, "errors": errors, "post": post}, status_code=400)
    apply_post_form(db, post, form)
    post.updated_at = utcnow()
    index_post(db, post)
    response = RedirectResponse(f"/blog/{post.slug}", status_code=303)
    set_flash(response, settings, signer, "ok", _("Post saved."))
    return response


@router.post("/admin/posts/{post_id}/delete", dependencies=[Depends(require_csrf)])
def admin_post_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, post_id: uuid.UUID):
    post = admin_get_post(db, post_id)
    remove(db, "post", post.id)
    comment_ids = select(PostComment.id).where(PostComment.post_id == post.id)
    db.execute(delete(HelpfulVote).where(HelpfulVote.kind == "post_comment", HelpfulVote.ref_id.in_(comment_ids)))
    db.delete(post)
    response = RedirectResponse("/admin/posts", status_code=303)
    set_flash(response, settings, signer, "ok", _("Post deleted."))
    return response
