"""Tea catalog: /teas list with search and filters, /teas/{slug} cards, origin and category pages, sitemap, admin."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import load_only, selectinload

from prolyv.handles import slugify
from prolyv.models import (
    CATEGORY_LABELS,
    EXPLAINER_SLUG,
    FORM_LABELS,
    Tea,
    TeaAlias,
    TeaCategory,
    TeaForm,
    TeaImage,
    TeaStatus,
    fold_text,
    origin_key,
    origin_levels,
    utcnow,
)
from prolyv.search import index_tea, remove
from prolyv.web.cup import HEX_RE
from prolyv.web.deps import AdminUser, CurrentUser, DbDep, SettingsDep, SignerDep, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render

router = APIRouter()

PAGE_SIZE = 24
ORIGIN_CHIPS = 12
CATEGORY_TITLES = {"tisane": "Tisanes", "heicha": "Heicha: dark teas"}
SORTS = {
    "popular": lambda: (Tea.popularity.desc(), Tea.name_en.asc()),
    "name": lambda: (Tea.name_en.asc(),),
    "newest": lambda: (Tea.created_at.desc(),),
}


# what a card in a list shows: its photos, and none of the long text a real card carries
CARD_COLUMNS = (Tea.slug, Tea.name_en, Tea.name_native, Tea.name_zh, Tea.name_ru, Tea.category, Tea.origin_country,
                Tea.origin_region, Tea.taste_tags, Tea.liquor_hex, Tea.status, Tea.locked)
CARD_OPTIONS = (load_only(*CARD_COLUMNS), selectinload(Tea.images))


# The tea list proper: published, without style-* cards, which are articles shown on /teas/types.
CATALOG = (Tea.status == TeaStatus.published, Tea.slug.not_like(EXPLAINER_SLUG))
MIN_REGION_TEAS = 3  # a region with fewer teas is too thin for a list or the sitemap


def published() -> select:
    return select(Tea).where(*CATALOG)


def count_of(db, stmt) -> int:  # noqa: ANN001
    return db.scalar(stmt.with_only_columns(func.count(), maintain_column_froms=True).order_by(None)) or 0


def apply_filters(stmt, q: str, category: str, origin: str, form: str):
    for token in fold_text(q).split()[:6]:
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Tea.search_text.like(f"%{escaped}%", escape="\\"))
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


def sub_origins(db, key: str, limit: int = 60) -> list[dict]:  # noqa: ANN001
    """The regions inside a country or region, most teas first, for the chips on its page."""
    rows = db.execute(select(Tea.origin_country, Tea.origin_region).where(*CATALOG, Tea.origin_keys.like(f"% {key} %"))).all()
    found: dict[str, dict] = {}
    for country, region in rows:
        levels = origin_levels(region)
        keys = [origin_key(level) for level in levels]
        inside = levels if origin_key(country) == key else levels[keys.index(key) + 1:] if key in keys else []
        for level in dict.fromkeys(inside):
            entry = found.setdefault(origin_key(level), {"name": level, "key": origin_key(level), "count": 0})
            entry["count"] += 1
    listed = [entry for entry in found.values() if entry["count"] >= MIN_REGION_TEAS]
    return sorted(listed, key=lambda r: (-r["count"], r["name"]))[:limit]


def list_page(request: Request, db, user, *, q="", category="", origin="", form="", sort="popular", page=1,  # noqa: ANN001
              title=None, intro=None, canonical=None):
    q = q.strip()[:80]
    category, form = clean_enum(category, TeaCategory), clean_enum(form, TeaForm)
    origin = origin_key(origin) if origin else ""
    sort = sort if sort in SORTS else "popular"
    page = max(1, page)

    stmt = apply_filters(published(), q, category, origin, form)
    total = count_of(db, stmt)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    teas = db.execute(stmt.options(*CARD_OPTIONS).order_by(*SORTS[sort]()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).scalars().all()

    # each menu counts what is left once the other filters apply
    def counts(column, *filters):  # noqa: ANN001, ANN202
        return db.execute(select(column, func.count()).where(*filters).group_by(column)).all()

    scope = apply_filters(select(Tea.id).where(*CATALOG), q, "", origin, form).whereclause
    category_counts = dict(counts(Tea.category, scope))
    scope = apply_filters(select(Tea.id).where(*CATALOG), q, category, "", form).whereclause
    country_counts = sorted(counts(Tea.origin_country, scope), key=lambda row: (-row[1], row[0]))
    scope = apply_filters(select(Tea.id).where(*CATALOG), q, category, origin, "").whereclause
    form_counts = dict(counts(Tea.form, scope))

    query = {k: v for k, v in (("q", q), ("origin", origin), ("form", form), ("sort", sort if sort != "popular" else "")) if v}
    if category and (canonical or "/teas") == "/teas":
        query["category"] = category
    base = canonical or "/teas"

    def with_query(path: str, **drop) -> str:  # noqa: ANN003
        kept = {k: v for k, v in query.items() if k not in drop and k != "category"}
        return f"{path}?{urlencode(kept)}" if kept else path

    on_origin_page = base.startswith("/teas/o/")

    def category_url(value: str | None) -> str:
        if on_origin_page:
            keep = {k: v for k, v in query.items() if k not in ("origin", "category")}
            if value:
                keep["category"] = value
            return f"{base}?{urlencode(keep)}" if keep else base
        return with_query(f"/teas/c/{value}" if value else "/teas")

    def page_url(n: int) -> str:
        return f"{base}?{urlencode({**query, 'page': n})}"

    context = {
        "teas": teas, "total": total, "page": page, "pages": pages, "q": q, "category": category, "origin": origin,
        "form": form, "sort": sort, "page_url": page_url, "shown": min(page * PAGE_SIZE, total), "category_url": category_url,
        "on_origin_page": on_origin_page, "thin_page": on_origin_page and total < MIN_REGION_TEAS,
        "title": title or ("Teas" if not q else f"Teas matching “{q}”"),
        "intro": intro,
        "canonical": canonical or "/teas",
        "categories": [(c.value, CATEGORY_LABELS[c], category_counts.get(c, 0)) for c in TeaCategory],
        "countries": [(origin_key(name), name, count) for name, count in country_counts],
        "forms": [(f.value, FORM_LABELS[f], form_counts.get(f, 0)) for f in TeaForm if form_counts.get(f)],
        "origin_label": origin_label(db, origin) if origin else None,
        "sub_origins": sub_origins(db, origin) if origin and on_origin_page else [],
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
                     title=CATEGORY_TITLES.get(category, f"{label} teas"), canonical=f"/teas/c/{category}")


@router.get("/teas/o/{origin}")
def tea_origin(request: Request, db: DbDep, user: CurrentUser, origin: str, q: str = "", category: str = "",
               form: str = "", sort: str = "popular", page: int = 1):
    label = origin_label(db, origin_key(origin))
    if label is None:
        raise HTTPException(status_code=404)
    return list_page(request, db, user, q=q, category=category, origin=origin, form=form, sort=sort, page=page,
                     title=f"Teas from {label}", canonical=f"/teas/o/{origin_key(origin)}")


# ------------------------------------------------------------------------------------------ the admin form


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
        "name_en", "name_native", "name_romanized", "name_zh", "name_ru", "history", "liquor_hex", "leaf_hex", "sources", "aliases", "category", "origin_country", "origin_region",
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
    for key in ("liquor_hex", "leaf_hex"):
        if form[key] and not HEX_RE.match(form[key]):
            errors[key] = "Use a colour like #C9963C."
    limits = {"brew_grams": (0.1, 100, "Grams"), "brew_water_ml": (10, 5000, "Water"), "brew_temp_c": (30, 100, "Temperature"), "brew_steeps": (1, 40, "Steeps"), "popularity": (1, 5, "Popularity")}
    for key, (low, high, label) in limits.items():
        if form[key]:
            try:
                in_range = low <= float(form[key]) <= high
            except ValueError:
                in_range = False
            if not in_range:
                errors[key] = f"{label} must be a number from {low:g} to {high:g}."
    if form["brew_seconds"] and not all(part.strip().isdigit() for part in form["brew_seconds"].split(",") if part.strip()):
        errors["brew_seconds"] = "Use whole seconds separated by commas, like 15, 10, 15."
    if any(line.strip() and not line.strip().startswith(("http://", "https://")) for line in form["sources"].splitlines()):
        errors["sources"] = "One link per line, starting with https://."
    return form, errors


def apply_tea_form(db, tea: Tea, form: dict) -> None:  # noqa: ANN001
    tea.name_en = form["name_en"]
    tea.name_native = form["name_native"] or None
    tea.name_romanized = form["name_romanized"] or None
    tea.name_zh = form["name_zh"] or None
    tea.name_ru = form["name_ru"] or None
    tea.history = form["history"] or None
    tea.liquor_hex = form["liquor_hex"] or None
    tea.leaf_hex = form["leaf_hex"] or None
    tea.sources = [u.strip() for u in form["sources"].splitlines() if u.strip().startswith(("http://", "https://"))]
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
        "name_zh": tea.name_zh or "", "name_ru": tea.name_ru or "", "history": tea.history or "",
        "liquor_hex": tea.liquor_hex or "", "leaf_hex": tea.leaf_hex or "", "sources": "\n".join(tea.sources or []),
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
        "popularity": str(tea.popularity), "published": tea.status == TeaStatus.published,
    }


# ------------------------------------------------------------------------------------------ the card


CATEGORY_BLURBS = {
    TeaCategory.green: "Unoxidised: the leaf is heated soon after picking to keep it fresh, grassy and sweet.",
    TeaCategory.white: "Barely handled: withered and dried, from silver buds to big leaves and aged cakes.",
    TeaCategory.yellow: "Green tea with an extra slow 'sealed yellowing' step that softens it.",
    TeaCategory.oolong: "Partly oxidised, from floral high-mountain balls to dark charcoal-roasted rock tea.",
    TeaCategory.black: "Fully oxidised: hong cha, Darjeeling, Assam, Ceylon and their blends.",
    TeaCategory.sheng: "Raw pu-erh from Yunnan, sold by harvest year and able to age for decades.",
    TeaCategory.shu: "Ripe pu-erh: pile-fermented, dark, smooth and ready to drink.",
    TeaCategory.heicha: "Other post-fermented dark teas: Liu Bao, Fu brick, Anhua and Sichuan bricks.",
    TeaCategory.tisane: "Not from the tea plant: herbs, flowers, roots and traditional blends.",
}


@router.get("/teas/types")
def tea_types(request: Request, db: DbDep, user: CurrentUser):
    counts = dict(db.execute(select(Tea.category, func.count()).where(*CATALOG).group_by(Tea.category)).all())
    types = [{"key": c.value, "label": CATEGORY_LABELS[c], "blurb": CATEGORY_BLURBS[c], "count": counts.get(c, 0)} for c in TeaCategory]
    explainers = db.execute(
        select(Tea).options(load_only(Tea.slug, Tea.name_en, Tea.taste_profile, Tea.description))
        .where(Tea.status == TeaStatus.published, Tea.slug.like(EXPLAINER_SLUG)).order_by(Tea.name_en)
    ).scalars().all()
    return render(request, "teas/types.html", {"types": types, "explainers": explainers})


@router.get("/teas/origins")
def tea_origins(request: Request, db: DbDep, user: CurrentUser):
    rows = db.execute(select(Tea.origin_country, Tea.origin_region).where(*CATALOG)).all()
    countries: dict[str, dict] = {}
    for country, region in rows:
        entry = countries.setdefault(country, {"name": country, "key": origin_key(country), "count": 0, "regions": {}})
        entry["count"] += 1
        for level in dict.fromkeys(origin_levels(region)):
            reg = entry["regions"].setdefault(origin_key(level), {"name": level, "key": origin_key(level), "count": 0})
            reg["count"] += 1
    ordered = sorted(countries.values(), key=lambda c: (-c["count"], c["name"]))
    for entry in ordered:
        regions = sorted((r for r in entry["regions"].values() if r["count"] >= MIN_REGION_TEAS), key=lambda r: (-r["count"], r["name"]))
        entry["regions"], entry["region_count"] = regions[:ORIGIN_CHIPS], len(regions)
    return render(request, "teas/origins.html", {"countries": ordered})


def first_letter(name: str) -> str:
    first = fold_text(name[:2])[:1].upper()
    return first if "A" <= first <= "Z" else "#"


def tea_letters(db) -> dict[str, list]:  # noqa: ANN001
    rows = db.execute(select(Tea.slug, Tea.name_en, Tea.name_zh, Tea.name_ru).where(*CATALOG)).all()
    letters: dict[str, list] = {}
    for row in rows:
        letters.setdefault(first_letter(row.name_en), []).append(row)
    return dict(sorted(letters.items(), key=lambda item: (item[0] == "#", item[0])))


@router.get("/teas/a-z")
def tea_a_to_z(request: Request, db: DbDep, user: CurrentUser, letter: str = ""):
    """One letter at a time: the whole base on one page would be megabytes of links."""
    letters = tea_letters(db)
    letter = letter.upper() if letter.upper() in letters else next(iter(letters), "")
    rows = sorted(letters.get(letter, []), key=lambda row: fold_text(row.name_en))
    context = {"letters": {key: len(value) for key, value in letters.items()}, "letter": letter, "rows": rows,
               "total": sum(len(value) for value in letters.values())}
    return render(request, "teas/a_z.html", context)


def absolute_url(settings, url: str | None) -> str | None:  # noqa: ANN001
    if url and url.startswith("/"):
        return f"{settings.base_url}{url}"
    return url


@router.get("/teas/{slug}")
def tea_detail(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, slug: str, lang: str = "en"):
    tea = db.execute(select(Tea).where(Tea.slug == slug.lower())).scalar_one_or_none()
    if tea is None:
        raise HTTPException(status_code=404)
    is_admin = getattr(request.state, "is_admin", False)
    if tea.status != TeaStatus.published and not is_admin:
        raise HTTPException(status_code=404)
    languages = tea.languages
    lang = lang if lang in languages else "en"
    text = tea.localized(lang)
    related = db.execute(
        published().options(*CARD_OPTIONS).where(Tea.category == tea.category, Tea.id != tea.id).order_by(Tea.popularity.desc(), Tea.name_en).limit(4)
    ).scalars().all()
    image = tea.primary_image
    page_url = f"{settings.base_url}/teas/{tea.slug}"
    alternate = [a for a in [tea.name_zh, tea.name_ru, tea.name_native, tea.name_romanized, *[a.alias for a in tea.aliases]] if a]
    summary = (text["taste_profile"] or (text["description_paragraphs"] or [tea.name_en])[0])[:300]
    jsonld = {
        "@context": "https://schema.org", "@type": "Article", "headline": tea.name_en, "about": {
            "@type": "Thing", "name": tea.name_en, "alternateName": alternate[:8], "description": summary},
        "description": summary, "url": page_url, "inLanguage": lang,
        "dateModified": tea.updated_at.date().isoformat(), "publisher": {"@type": "Organization", "name": "Prolyv"},
    }
    if image:
        jsonld["image"] = absolute_url(settings, image.url)
    if tea.wikipedia_url:
        jsonld["about"]["sameAs"] = tea.wikipedia_url
    from prolyv.models import TeaSession, Visibility
    from prolyv.web.routes_diary import public_sessions_for_tea
    from prolyv.web.routes_social import is_saved

    session_total, session_avg = db.execute(
        select(func.count(), func.avg(TeaSession.rating)).where(TeaSession.tea_id == tea.id, TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False))
    ).one()
    context = {
        "session_total": session_total, "session_avg": round(session_avg, 1) if session_avg else None,
        "saved": is_saved(db, user, "tea", tea.id),
        "tea": tea, "text": text, "lang": lang, "languages": languages, "related": related, "image": image, "is_admin_view": is_admin,
        "sessions": public_sessions_for_tea(db, tea.id), "page_url": page_url,
        "jsonld": json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/"),
        "og": {"title": f"{tea.name_en} · Prolyv tea base", "description": summary[:200],
               "image": absolute_url(settings, image.url) if image else None,
               "url": page_url if lang == "en" else f"{page_url}?lang={lang}"},
    }
    return render(request, "teas/detail.html", context)


# ------------------------------------------------------------------------------------------ admin


@router.get("/admin/teas")
def admin_teas(request: Request, db: DbDep, user: AdminUser, q: str = "", page: int = 1, drafts: int = 0):
    q = q.strip()[:80]
    stmt = apply_filters(select(Tea), q, "", "", "")
    if drafts:
        stmt = stmt.where(Tea.status == TeaStatus.pending)
    total_found = count_of(db, stmt)
    pages = max(1, (total_found + 49) // 50)
    page = min(max(1, page), pages)
    teas = db.execute(stmt.options(*CARD_OPTIONS).order_by(Tea.status.asc(), Tea.name_en).offset((page - 1) * 50).limit(50)).scalars().all()
    total = count_of(db, published())
    drafts = db.scalar(select(func.count()).select_from(Tea).where(Tea.status == TeaStatus.pending)) or 0
    with_photos = db.scalar(
        select(func.count(func.distinct(TeaImage.tea_id))).join(Tea, Tea.id == TeaImage.tea_id).where(Tea.status == TeaStatus.published)
    ) or 0
    context = {"teas": teas, "q": q, "page": page, "pages": pages, "total": total, "drafts": drafts, "with_photos": with_photos}
    return render(request, "admin/teas.html", context)


@router.get("/admin/teas/new")
def admin_tea_new_form(request: Request, user: AdminUser):
    return render(request, "admin/tea_edit.html", tea_form_context(form={"category": "", "form": "loose", "popularity": "3", "published": True}))


@router.post("/admin/teas/new", dependencies=[Depends(require_csrf)])
async def admin_tea_new(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser):
    data = await request.form()
    form, errors = read_tea_form(data)
    if errors:
        return render(request, "admin/tea_edit.html", tea_form_context(form, errors), status_code=400)
    tea = Tea(slug=unique_slug(db, form["name_en"]), name_en=form["name_en"], category=TeaCategory(form["category"]),
              origin_country=form["origin_country"], form=TeaForm(form["form"]), created_by=user.id, popularity=3, locked=True,
              status=TeaStatus.published if data.get("published") else TeaStatus.pending)
    db.add(tea)
    db.flush()
    apply_tea_form(db, tea, form)
    db.flush()
    index_tea(db, tea)
    response = RedirectResponse(f"/teas/{tea.slug}", status_code=303)
    set_flash(response, settings, signer, "ok", "Added to the tea base." if tea.status == TeaStatus.published else "Saved as a draft.")
    return response


def admin_tea(db, slug: str) -> Tea:  # noqa: ANN001
    tea = db.execute(select(Tea).where(Tea.slug == slug)).scalar_one_or_none()
    if tea is None:
        raise HTTPException(status_code=404)
    return tea


@router.post("/admin/teas/{slug}/delete", dependencies=[Depends(require_csrf)])
def admin_tea_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: AdminUser, slug: str):
    tea = admin_tea(db, slug)
    remove(db, "tea", tea.id)
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
    data = await request.form()
    form, errors = read_tea_form(data)
    if errors:
        return render(request, "admin/tea_edit.html", tea_form_context(form, errors, tea=tea), status_code=400)
    apply_tea_form(db, tea, form)
    tea.status = TeaStatus.published if data.get("published") else TeaStatus.pending
    tea.locked = True  # the importer will not overwrite an admin's text
    tea.updated_at = utcnow()
    db.flush()
    index_tea(db, tea)
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
    from prolyv.models import ForumSection, Post, Topic

    base = settings.base_url
    pages = ["/", "/teas", "/teas/types", "/teas/origins", "/teas/a-z", "/blog", "/discussions", "/about", "/rules", "/privacy", "/terms"]
    entries = [(f"{base}{page}", None) for page in pages]
    entries += [(f"{base}/teas/a-z?letter={quote(letter)}", None) for letter in tea_letters(db)]
    entries += [(f"{base}/teas/c/{c.value}", None) for c in TeaCategory]
    origin_counts: dict[str, int] = {}
    for row in db.execute(select(Tea.origin_keys).where(*CATALOG)).scalars():
        for key in row.split():
            origin_counts[key] = origin_counts.get(key, 0) + 1
    entries += [(f"{base}/teas/o/{key}", None) for key, count in sorted(origin_counts.items()) if count >= MIN_REGION_TEAS]
    # thin cards on rare teas carry noindex, so they stay out of here too
    rows = db.execute(select(Tea.slug, Tea.updated_at).where(Tea.status == TeaStatus.published, Tea.confidence.is_distinct_from("low")).order_by(Tea.slug)).all()
    entries += [(f"{base}/teas/{slug}", updated) for slug, updated in rows]
    entries += [(f"{base}/blog/{slug}", updated) for slug, updated in db.execute(select(Post.slug, Post.updated_at).where(Post.published_at.is_not(None)).order_by(Post.published_at.desc())).all()]
    entries += [(f"{base}/discussions/s/{slug}", None) for slug in db.execute(select(ForumSection.slug).order_by(ForumSection.sort)).scalars()]
    entries += [(f"{base}/discussions/{tid}", updated) for tid, updated in db.execute(select(Topic.id, Topic.updated_at).where(Topic.hidden.is_(False)).order_by(Topic.created_at.desc()).limit(5000)).all()]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, updated in entries:
        lastmod = f"<lastmod>{updated.date().isoformat()}</lastmod>" if isinstance(updated, datetime) else ""
        body.append(f"<url><loc>{loc}</loc>{lastmod}</url>")
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml")
