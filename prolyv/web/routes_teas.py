"""Tea catalog: /teas list with search and filters, /teas/{slug} cards, origin and category pages, sitemap, admin."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

from prolyv.handles import slugify
from prolyv.models import (
    CATEGORY_LABELS,
    FORM_LABELS,
    Tea,
    TeaAlias,
    TeaCategory,
    TeaForm,
    TeaImage,
    TeaStatus,
    origin_key,
    utcnow,
)
from prolyv.web.deps import AdminUser, CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render

router = APIRouter()

PAGE_SIZE = 24
SORTS = {
    "popular": lambda: (Tea.popularity.desc(), Tea.name_en.asc()),
    "name": lambda: (Tea.name_en.asc(),),
    "newest": lambda: (Tea.created_at.desc(),),
}


def published() -> select:
    return select(Tea).where(Tea.status == TeaStatus.published)


def apply_filters(stmt, q: str, category: str, origin: str, form: str):
    for token in q.lower().split()[:6]:
        stmt = stmt.where(Tea.search_text.like(f"%{token}%"))
    if category:
        stmt = stmt.where(Tea.category == TeaCategory(category))
    if origin:
        stmt = stmt.where(Tea.origin_keys.like(f"% {origin} %"))
    if form:
        stmt = stmt.where(Tea.form == TeaForm(form))
    return stmt


def clean_enum(value: str, enum_cls) -> str:  # noqa: ANN001
    return value if value in enum_cls.__members__ else ""


def origin_label(db, key: str) -> str | None:  # noqa: ANN001
    tea = db.execute(published().where(Tea.origin_keys.like(f"% {key} %")).limit(1)).scalar_one_or_none()
    if tea is None:
        return None
    return next((label for label, k in tea.origin_parts if k == key), None)


def list_page(request: Request, db, user, *, q="", category="", origin="", form="", sort="popular", page=1,  # noqa: ANN001
              title=None, intro=None, canonical=None):
    q = q.strip()[:80]
    category, form = clean_enum(category, TeaCategory), clean_enum(form, TeaForm)
    origin = origin_key(origin) if origin else ""
    sort = sort if sort in SORTS else "popular"
    page = max(1, page)

    stmt = apply_filters(published(), q, category, origin, form)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    teas = db.execute(stmt.order_by(*SORTS[sort]()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).scalars().all()

    category_counts = dict(db.execute(select(Tea.category, func.count()).where(Tea.status == TeaStatus.published).group_by(Tea.category)).all())
    country_counts = db.execute(
        select(Tea.origin_country, func.count()).where(Tea.status == TeaStatus.published)
        .group_by(Tea.origin_country).order_by(func.count().desc(), Tea.origin_country)
    ).all()
    form_counts = dict(db.execute(select(Tea.form, func.count()).where(Tea.status == TeaStatus.published).group_by(Tea.form)).all())

    query = {k: v for k, v in (("q", q), ("origin", origin), ("form", form), ("sort", sort if sort != "popular" else "")) if v}
    if category and (canonical or "/teas") == "/teas":
        query["category"] = category

    def page_url(n: int) -> str:
        return f"{canonical or '/teas'}?{urlencode({**query, 'page': n})}"

    context = {
        "teas": teas, "total": total, "page": page, "pages": pages, "q": q, "category": category, "origin": origin,
        "form": form, "sort": sort, "page_url": page_url,
        "title": title or ("Teas" if not q else f"Teas matching “{q}”"),
        "intro": intro,
        "canonical": canonical or "/teas",
        "categories": [(c.value, CATEGORY_LABELS[c], category_counts.get(c, 0)) for c in TeaCategory],
        "countries": [(origin_key(name), name, count) for name, count in country_counts],
        "forms": [(f.value, FORM_LABELS[f], form_counts.get(f, 0)) for f in TeaForm if form_counts.get(f)],
        "origin_label": origin_label(db, origin) if origin else None,
    }
    return render(request, "teas/list.html", context)


@router.get("/teas")
def tea_list(request: Request, db: DbDep, user: CurrentUser, q: str = "", category: str = "", origin: str = "",
             form: str = "", sort: str = "popular", page: int = 1):
    return list_page(request, db, user, q=q, category=category, origin=origin, form=form, sort=sort, page=page)


@router.get("/teas/c/{category}")
def tea_category(request: Request, db: DbDep, user: CurrentUser, category: str, q: str = "", origin: str = "",
                 form: str = "", sort: str = "popular", page: int = 1):
    if category not in TeaCategory.__members__:
        raise HTTPException(status_code=404)
    label = CATEGORY_LABELS[TeaCategory(category)]
    return list_page(request, db, user, q=q, category=category, origin=origin, form=form, sort=sort, page=page,
                     title=f"{label} teas", canonical=f"/teas/c/{category}")


@router.get("/teas/o/{origin}")
def tea_origin(request: Request, db: DbDep, user: CurrentUser, origin: str, q: str = "", category: str = "",
               form: str = "", sort: str = "popular", page: int = 1):
    label = origin_label(db, origin_key(origin))
    if label is None:
        raise HTTPException(status_code=404)
    return list_page(request, db, user, q=q, category=category, origin=origin, form=form, sort=sort, page=page,
                     title=f"Teas from {label}", canonical=f"/teas/o/{origin_key(origin)}")


# ------------------------------------------------------------------------------------------ community requests


def unique_slug(db, name: str) -> str:  # noqa: ANN001
    base = slugify(name).replace("_", "-").strip("-")[:70] or "tea"
    candidate, n = base, 1
    while db.execute(select(Tea.id).where(Tea.slug == candidate)).first() is not None:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def tea_form_context(form: dict | None = None, errors: dict | None = None, tea: Tea | None = None) -> dict:
    return {
        "form": form or {}, "errors": errors or {}, "tea": tea,
        "category_options": [(c.value, CATEGORY_LABELS[c]) for c in TeaCategory],
        "form_options": [(f.value, FORM_LABELS[f]) for f in TeaForm],
    }


def read_tea_form(data) -> tuple[dict, dict]:  # noqa: ANN001
    form = {k: (data.get(k) or "").strip() for k in (
        "name_en", "name_native", "name_romanized", "aliases", "category", "origin_country", "origin_region",
        "cultivar", "processing", "form", "harvest_season", "appearance_dry", "appearance_liquor", "appearance_wet",
        "taste_profile", "taste_tags", "vintage_note", "description", "wikipedia_url", "caffeine_level",
        "brew_grams", "brew_water_ml", "brew_temp_c", "brew_steeps", "brew_seconds", "brew_notes", "popularity",
    )}
    errors: dict[str, str] = {}
    if not 2 <= len(form["name_en"]) <= 120:
        errors["name_en"] = "Give the tea a name (2–120 characters)."
    if form["category"] not in TeaCategory.__members__:
        errors["category"] = "Pick a category."
    if form["form"] not in TeaForm.__members__:
        errors["form"] = "Pick a form."
    if not form["origin_country"]:
        errors["origin_country"] = "Where is it from?"
    if len(form["description"]) < 40:
        errors["description"] = "Tell us a little more (at least 40 characters)."
    if form["wikipedia_url"] and not form["wikipedia_url"].startswith("https://en.wikipedia.org/wiki/"):
        errors["wikipedia_url"] = "Use a link to the English Wikipedia article."
    return form, errors


def apply_tea_form(db, tea: Tea, form: dict) -> None:  # noqa: ANN001
    tea.name_en = form["name_en"]
    tea.name_native = form["name_native"] or None
    tea.name_romanized = form["name_romanized"] or None
    tea.category = TeaCategory(form["category"])
    tea.origin_country = form["origin_country"]
    tea.origin_region = form["origin_region"] or None
    tea.cultivar = form["cultivar"] or None
    tea.processing = form["processing"] or None
    tea.form = TeaForm(form["form"])
    tea.harvest_season = form["harvest_season"] or None
    tea.appearance_dry = form["appearance_dry"] or None
    tea.appearance_liquor = form["appearance_liquor"] or None
    tea.appearance_wet = form["appearance_wet"] or None
    tea.taste_profile = form["taste_profile"] or None
    tea.taste_tags = [t.strip().lower() for t in form["taste_tags"].split(",") if t.strip()][:6]
    tea.vintage_note = form["vintage_note"] or None
    tea.description = form["description"]
    tea.wikipedia_url = form["wikipedia_url"] or None
    tea.caffeine_level = form["caffeine_level"] if form["caffeine_level"] in ("none", "low", "medium", "high") else None
    if form["popularity"].isdigit() and 1 <= int(form["popularity"]) <= 5:
        tea.popularity = int(form["popularity"])

    def number(value: str, cast=int):  # noqa: ANN001
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None

    seconds = [number(s) for s in form["brew_seconds"].replace(";", ",").split(",") if s.strip()]
    brewing = {
        "grams": number(form["brew_grams"], float), "water_ml": number(form["brew_water_ml"]),
        "temp_c": number(form["brew_temp_c"]), "steeps": number(form["brew_steeps"]) or len([s for s in seconds if s]),
        "seconds": [s for s in seconds if s is not None], "notes": form["brew_notes"] or None,
    }
    tea.brewing = {k: v for k, v in brewing.items() if v not in (None, [], "")}
    wanted = [a.strip() for a in form["aliases"].split(",") if a.strip()]
    tea.aliases.clear()
    db.flush()
    tea.aliases = [TeaAlias(alias=a) for a in dict.fromkeys(wanted)]
    tea.refresh_search_text()


def form_from_tea(tea: Tea) -> dict:
    brewing = tea.brewing or {}
    return {
        "name_en": tea.name_en, "name_native": tea.name_native or "", "name_romanized": tea.name_romanized or "",
        "aliases": ", ".join(a.alias for a in tea.aliases), "category": tea.category.value,
        "origin_country": tea.origin_country, "origin_region": tea.origin_region or "", "cultivar": tea.cultivar or "",
        "processing": tea.processing or "", "form": tea.form.value, "harvest_season": tea.harvest_season or "",
        "appearance_dry": tea.appearance_dry or "", "appearance_liquor": tea.appearance_liquor or "",
        "appearance_wet": tea.appearance_wet or "", "taste_profile": tea.taste_profile or "",
        "taste_tags": ", ".join(tea.taste_tags or []), "vintage_note": tea.vintage_note or "",
        "description": tea.description, "wikipedia_url": tea.wikipedia_url or "", "caffeine_level": tea.caffeine_level or "",
        "brew_grams": brewing.get("grams", ""), "brew_water_ml": brewing.get("water_ml", ""),
        "brew_temp_c": brewing.get("temp_c", ""), "brew_steeps": brewing.get("steeps", ""),
        "brew_seconds": ", ".join(str(s) for s in brewing.get("seconds", [])), "brew_notes": brewing.get("notes") or "",
        "popularity": str(tea.popularity),
    }


@router.get("/teas/new")
def tea_new_form(request: Request, user: RequiredUser):
    return render(request, "teas/new.html", tea_form_context(form={"category": "", "form": "loose"}))


@router.post("/teas/new", dependencies=[Depends(require_csrf)])
async def tea_new(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    data = await request.form()
    form, errors = read_tea_form(data)
    if errors:
        return render(request, "teas/new.html", tea_form_context(form, errors), status_code=400)
    is_admin = settings.is_admin_handle(user.handle)
    tea = Tea(slug=unique_slug(db, form["name_en"]), name_en=form["name_en"], category=TeaCategory(form["category"]),
              origin_country=form["origin_country"], form=TeaForm(form["form"]), created_by=user.id,
              status=TeaStatus.published if is_admin else TeaStatus.pending, popularity=2, locked=True)
    db.add(tea)
    db.flush()
    apply_tea_form(db, tea, form)
    db.flush()
    response = RedirectResponse(f"/teas/{tea.slug}", status_code=303)
    text = "Added to the catalog." if is_admin else "Thanks! Your tea is waiting for a quick review by an admin."
    set_flash(response, settings, signer, "ok", text)
    return response


# ------------------------------------------------------------------------------------------ the card


@router.get("/teas/{slug}")
def tea_detail(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, slug: str):
    tea = db.execute(select(Tea).where(Tea.slug == slug.lower())).scalar_one_or_none()
    if tea is None:
        raise HTTPException(status_code=404)
    is_admin = user is not None and settings.is_admin_handle(user.handle)
    if tea.status != TeaStatus.published and not (is_admin or (user is not None and tea.created_by == user.id)):
        raise HTTPException(status_code=404)
    related = db.execute(
        published().where(Tea.category == tea.category, Tea.id != tea.id).order_by(Tea.popularity.desc(), Tea.name_en).limit(4)
    ).scalars().all()
    image = tea.primary_image
    jsonld = {
        "@context": "https://schema.org", "@type": "Product", "name": tea.name_en,
        "alternateName": [a.alias for a in tea.aliases][:5],
        "description": (tea.taste_profile or tea.paragraphs[0] if tea.paragraphs else tea.name_en)[:300],
        "category": tea.category_label, "url": f"{settings.base_url}/teas/{tea.slug}",
        "countryOfOrigin": tea.origin_country,
    }
    if image:
        jsonld["image"] = image.url
    if tea.wikipedia_url:
        jsonld["sameAs"] = [tea.wikipedia_url]
    from prolyv.web.routes_diary import public_sessions_for_tea

    context = {
        "tea": tea, "related": related, "image": image, "is_admin_view": is_admin,
        "sessions": public_sessions_for_tea(db, tea.id),
        "jsonld": json.dumps(jsonld, ensure_ascii=False),
        "og": {"title": f"{tea.name_en} · Prolyv tea base", "description": (tea.taste_profile or "")[:200],
               "image": image.thumb_url if image else None, "url": f"{settings.base_url}/teas/{tea.slug}"},
    }
    return render(request, "teas/detail.html", context)


# ------------------------------------------------------------------------------------------ admin


@router.get("/admin/teas")
def admin_teas(request: Request, db: DbDep, user: AdminUser):
    pending = db.execute(select(Tea).where(Tea.status == TeaStatus.pending).order_by(Tea.created_at.desc())).scalars().all()
    total = db.scalar(select(func.count()).select_from(published().subquery())) or 0
    with_photos = db.scalar(
        select(func.count(func.distinct(TeaImage.tea_id))).join(Tea, Tea.id == TeaImage.tea_id).where(Tea.status == TeaStatus.published)
    ) or 0
    return render(request, "admin/teas.html", {"pending": pending, "total": total, "with_photos": with_photos})


def admin_tea(db, slug: str) -> Tea:  # noqa: ANN001
    tea = db.execute(select(Tea).where(Tea.slug == slug)).scalar_one_or_none()
    if tea is None:
        raise HTTPException(status_code=404)
    return tea


@router.post("/admin/teas/{slug}/approve", dependencies=[Depends(require_csrf)])
def admin_tea_approve(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, slug: str):
    tea = admin_tea(db, slug)
    tea.status = TeaStatus.published
    tea.updated_at = utcnow()
    response = RedirectResponse("/admin/teas", status_code=303)
    set_flash(response, settings, signer, "ok", f"{tea.name_en} is now published.")
    return response


@router.post("/admin/teas/{slug}/delete", dependencies=[Depends(require_csrf)])
def admin_tea_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, slug: str):
    tea = admin_tea(db, slug)
    db.delete(tea)
    response = RedirectResponse("/admin/teas", status_code=303)
    set_flash(response, settings, signer, "ok", f"{tea.name_en} was removed.")
    return response


@router.get("/admin/teas/{slug}/edit")
def admin_tea_edit_form(request: Request, db: DbDep, user: AdminUser, slug: str):
    tea = admin_tea(db, slug)
    return render(request, "admin/tea_edit.html", tea_form_context(form_from_tea(tea), tea=tea))


@router.post("/admin/teas/{slug}/edit", dependencies=[Depends(require_csrf)])
async def admin_tea_edit(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, slug: str):
    tea = admin_tea(db, slug)
    form, errors = read_tea_form(await request.form())
    if errors:
        return render(request, "admin/tea_edit.html", tea_form_context(form, errors, tea=tea), status_code=400)
    apply_tea_form(db, tea, form)
    tea.locked = True  # the importer will not overwrite an admin's text
    tea.updated_at = utcnow()
    response = RedirectResponse(f"/teas/{tea.slug}", status_code=303)
    set_flash(response, settings, signer, "ok", "Card saved.")
    return response


@router.post("/admin/teas/{slug}/images/{image_id}", dependencies=[Depends(require_csrf)])
def admin_tea_image(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, slug: str,
                    image_id: uuid.UUID, action: str = Form("")):
    tea = admin_tea(db, slug)
    image = next((i for i in tea.images if i.id == image_id), None)
    if image is None or action not in ("primary", "delete"):
        raise HTTPException(status_code=404)
    if action == "primary":
        for other in tea.images:
            other.is_primary = other.id == image.id
    else:
        tea.images.remove(image)
        db.delete(image)
    tea.locked = True
    response = RedirectResponse(f"/admin/teas/{tea.slug}/edit", status_code=303)
    set_flash(response, settings, signer, "ok", "Photos updated.")
    return response


# ------------------------------------------------------------------------------------------ sitemap


@router.get("/sitemap.xml")
def sitemap(db: DbDep, settings: SettingsDep):
    base = settings.base_url
    entries = [(f"{base}/", None), (f"{base}/teas", None), (f"{base}/login", None)]
    entries += [(f"{base}/teas/c/{c.value}", None) for c in TeaCategory]
    rows = db.execute(select(Tea.slug, Tea.updated_at).where(Tea.status == TeaStatus.published).order_by(Tea.slug)).all()
    entries += [(f"{base}/teas/{slug}", updated) for slug, updated in rows]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, updated in entries:
        lastmod = f"<lastmod>{updated.date().isoformat()}</lastmod>" if isinstance(updated, datetime) else ""
        body.append(f"<url><loc>{loc}</loc>{lastmod}</url>")
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml")
