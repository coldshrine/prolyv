#!/usr/bin/env python
"""Import tea cards from data/teas/**/*.json into the database and enrich them from Wikimedia.

Idempotent: re-running updates the text from the card files (unless an admin locked the card in the UI),
refreshes Wikipedia links and Commons photos, and leaves everything else alone. Every HTTP response is cached in
data/cache/http/ so a second run is offline-fast; --refresh bypasses the cache.

    uv run python scripts/import_teas.py                 # everything
    uv run python scripts/import_teas.py --group japan   # one folder
    uv run python scripts/import_teas.py --only sencha   # one card
    uv run python scripts/import_teas.py --no-network    # cards + cached data only
    uv run python scripts/import_teas.py --dry-run       # validate the JSON, touch nothing
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx2 as httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prolyv.config import get_settings  # noqa: E402
from prolyv.db import make_engine, make_session_factory  # noqa: E402
from prolyv.models import Tea, TeaAlias, TeaCategory, TeaForm, TeaImage, TeaStatus  # noqa: E402

CARDS_DIR = ROOT / "data" / "teas"
CACHE_DIR = ROOT / "data" / "cache"
HTTP_CACHE = CACHE_DIR / "http"
WIKI_CACHE = CACHE_DIR / "wiki"

USER_AGENT = "ProlyvTeaCatalog/0.1 (tea diary catalog importer; https://github.com/coldshrine/prolyv)"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

REQUIRED = (
    "slug", "name_en", "category", "origin_country", "origin_region", "processing", "form", "harvest_season",
    "appearance_dry", "appearance_liquor", "appearance_wet", "taste_profile", "taste_tags", "brewing",
    "caffeine_level", "vintage_note", "description", "wikipedia_scope", "popularity",
)
TASTE_TAGS = {
    "floral", "fruity", "stone-fruit", "citrus", "dried-fruit", "honey", "sweet", "creamy", "nutty", "cocoa", "malty",
    "roasted", "smoky", "mineral", "grassy", "vegetal", "umami", "marine", "woody", "earthy", "camphor", "medicinal",
    "spicy", "herbal", "bitter", "brisk", "tart",
}
CAFFEINE = {"none", "low", "medium", "high"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Commons licences we may show. Matched case-insensitively against LicenseShortName.
LICENSE_OK = re.compile(r"(cc0|cc[- ]by|public domain|^pd\b|gfdl|no restrictions|attribution)", re.IGNORECASE)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
BAD_IMAGE_WORDS = (
    "flag", "map", "icon", "logo", "symbol", "wiki", "commons", "edit", "question", "ambox", "star", "crystal",
    "emblem", "seal", "coat of arms", "coat_of_arms", "padlock", "disambig", "sound", "speaker", "locator", "arrow",
    "nuvola", "pog", "pictogram", "gnome", "oojs", "increase", "decrease", "check", "cscr", "p vip", "folder",
    "merge", "split", "stub", "portal", "unbalanced", "wikidata", "wikisource", "wiktionary", "translation",
    "diagram", "chart", "graph", "scheme", "structure", "molecule", "formula", "caffeine", "theanine",
)


# ------------------------------------------------------------------------------------------------ HTTP + cache


class Http:
    def __init__(self, enabled: bool, refresh: bool, pause: float = 0.12) -> None:
        self.enabled, self.refresh, self.pause = enabled, refresh, pause
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=40, follow_redirects=True)
        self.requests = 0
        self.cache_hits = 0
        self._last = 0.0

    def get_json(self, url: str, params: dict | None = None) -> dict | None:
        full = f"{url}?{urlencode(params)}" if params else url
        path = HTTP_CACHE / f"{hashlib.sha1(full.encode()).hexdigest()}.json"
        if path.exists() and not self.refresh:
            self.cache_hits += 1
            return json.loads(path.read_text())["body"]
        if not self.enabled:
            return None
        body = self._fetch(full)
        if body is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": full, "fetched_at": _now(), "body": body}, ensure_ascii=False))
        return body

    def _fetch(self, url: str) -> dict | None:
        for attempt in range(4):
            wait = self.pause - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.client.get(url)
                self.requests += 1
            except httpx.HTTPError as exc:
                print(f"    network error ({exc.__class__.__name__}), retry {attempt + 1}", file=sys.stderr)
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code == 404:
                return {"_missing": True}
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(2.0 * (attempt + 1))
                continue
            if response.status_code != 200:
                print(f"    HTTP {response.status_code} for {url}", file=sys.stderr)
                return None
            try:
                return response.json()
            except ValueError:
                return None
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def strip_html(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", "", value)
    text = html.unescape(text).strip()
    return re.sub(r"\s+", " ", text)[:300] or None


# ------------------------------------------------------------------------------------------------ cards


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_cards(group: str | None, only: str | None) -> tuple[list[dict], list[str]]:
    cards, errors = [], []
    folders = sorted(p for p in CARDS_DIR.iterdir() if p.is_dir() and (group is None or p.name == group))
    for folder in folders:
        for path in sorted(folder.glob("*.json")):
            if path.name.startswith("_"):
                continue
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{rel(path)}: invalid JSON ({exc})")
                continue
            card["_group"], card["_path"] = folder.name, rel(path)
            if only and card.get("slug") != only:
                continue
            problems = validate(card)
            if path.stem != card.get("slug"):
                problems.append(f"file name {path.stem!r} does not match slug {card.get('slug')!r}")
            if problems:
                errors.append(f"{rel(path)}: " + "; ".join(problems))
                continue
            cards.append(card)
    seen: dict[str, str] = {}
    unique = []
    for card in cards:
        if card["slug"] in seen:
            errors.append(f"{card['_path']}: duplicate slug (also in {seen[card['slug']]})")
            continue
        seen[card["slug"]] = card["_path"]
        unique.append(card)
    return unique, errors


def validate(card: dict) -> list[str]:
    problems = [f"missing {field}" for field in REQUIRED if field not in card]
    if problems:
        return problems
    if not SLUG_RE.fullmatch(card["slug"]) or len(card["slug"]) > 80:
        problems.append("bad slug")
    if card["category"] not in TeaCategory.__members__:
        problems.append(f"bad category {card['category']!r}")
    if card["form"] not in TeaForm.__members__:
        problems.append(f"bad form {card['form']!r}")
    if card["caffeine_level"] not in CAFFEINE:
        problems.append(f"bad caffeine_level {card['caffeine_level']!r}")
    bad_tags = [t for t in card["taste_tags"] if t not in TASTE_TAGS]
    if bad_tags:
        problems.append(f"unknown taste_tags {bad_tags}")
    brewing = card["brewing"]
    if not isinstance(brewing, dict) or not all(k in brewing for k in ("grams", "water_ml", "temp_c", "steeps", "seconds")):
        problems.append("brewing is incomplete")
    elif len(brewing["seconds"]) != brewing["steeps"]:
        problems.append("brewing.seconds length != steeps")
    if card["wikipedia_scope"] not in ("exact", "umbrella", "none"):
        problems.append("bad wikipedia_scope")
    if not isinstance(card["popularity"], int) or not 1 <= card["popularity"] <= 5:
        problems.append("popularity must be 1-5")
    if len(card["description"].split("\n\n")) < 2:
        problems.append("description needs at least two paragraphs")
    if not isinstance(card.get("aliases", []), list):
        problems.append("aliases must be a list")
    return problems


# ------------------------------------------------------------------------------------------------ Wikimedia


def wikipedia_page(http: Http, title: str) -> dict | None:
    data = http.get_json(
        WIKIPEDIA_API,
        {
            "action": "query", "format": "json", "formatversion": 2, "titles": title, "redirects": 1,
            "prop": "pageprops|info|images|pageimages", "ppprop": "wikibase_item", "inprop": "url",
            "imlimit": 50, "piprop": "name",
        },
    )
    if not data or "query" not in data:
        return None
    page = (data["query"].get("pages") or [{}])[0]
    if page.get("missing") or "title" not in page:
        return None
    return {
        "title": page["title"],
        "url": page.get("fullurl") or f"https://en.wikipedia.org/wiki/{quote(page['title'].replace(' ', '_'))}",
        "wikidata": (page.get("pageprops") or {}).get("wikibase_item"),
        "images": [im["title"] for im in page.get("images", [])],
        "pageimage": page.get("pageimage"),
    }


def wikipedia_summary(http: Http, title: str) -> dict | None:
    data = http.get_json(WIKIPEDIA_SUMMARY + quote(title.replace(" ", "_"), safe=""))
    if not data or data.get("_missing"):
        return None
    return data


def wikidata_entity(http: Http, qid: str) -> dict | None:
    data = http.get_json(WIKIDATA_ENTITY.format(qid=qid))
    if not data or data.get("_missing"):
        return None
    entity = (data.get("entities") or {}).get(qid) or {}
    claims = entity.get("claims", {})

    def values(prop: str) -> list[str]:
        out = []
        for claim in claims.get(prop, []):
            value = (claim.get("mainsnak", {}).get("datavalue") or {}).get("value")
            if isinstance(value, str):
                out.append(value)
        return out

    return {"images": values("P18"), "commons_category": next(iter(values("P373")), None)}


def commons_category_files(http: Http, category: str, limit: int = 20) -> list[str]:
    data = http.get_json(
        COMMONS_API,
        {"action": "query", "format": "json", "formatversion": 2, "list": "categorymembers",
         "cmtitle": f"Category:{category}", "cmnamespace": 6, "cmlimit": limit, "cmtype": "file"},
    )
    if not data or "query" not in data:
        return []
    return [m["title"] for m in data["query"].get("categorymembers", [])]


def commons_search(http: Http, query: str, limit: int = 8) -> list[str]:
    data = http.get_json(
        COMMONS_API,
        {"action": "query", "format": "json", "formatversion": 2, "list": "search", "srnamespace": 6,
         "srlimit": limit, "srsearch": f'"{query}" tea filetype:bitmap'},
    )
    if not data or "query" not in data:
        return []
    return [m["title"] for m in data["query"].get("search", [])]


def commons_imageinfo(http: Http, titles: list[str]) -> list[dict]:
    """Resolve File: titles to URLs + credits, keeping only free licences and real photos."""
    results: list[dict] = []
    for start in range(0, len(titles), 20):
        chunk = titles[start : start + 20]
        data = http.get_json(
            COMMONS_API,
            {"action": "query", "format": "json", "formatversion": 2, "prop": "imageinfo",
             "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 960, "titles": "|".join(chunk)},
        )
        if not data or "query" not in data:
            continue
        for page in data["query"].get("pages", []):
            info = (page.get("imageinfo") or [None])[0]
            if not info:
                continue
            meta = info.get("extmetadata") or {}
            license_name = strip_html((meta.get("LicenseShortName") or {}).get("value"))
            if not license_name or not LICENSE_OK.search(license_name):
                continue
            if not (info.get("mime") or "").startswith(("image/jpeg", "image/png", "image/webp")):
                continue
            if (info.get("width") or 0) < 400 or (info.get("height") or 0) < 300:
                continue
            results.append(
                {
                    "title": page["title"],
                    "url": info["url"],
                    "thumb_url": info.get("thumburl") or info["url"],
                    "source_page": info.get("descriptionurl"),
                    "author": strip_html((meta.get("Artist") or {}).get("value")),
                    "license": license_name,
                    "license_url": strip_html((meta.get("LicenseUrl") or {}).get("value")),
                }
            )
    return results


def looks_like_photo(file_title: str) -> bool:
    name = file_title.lower()
    return name.endswith(IMAGE_EXT) and not any(word in name for word in BAD_IMAGE_WORDS)


def enrich(http: Http, card: dict, max_images: int) -> dict:
    """Links + photo candidates for one card. Umbrella articles only contribute the link, never their photos."""
    out: dict = {"wikipedia_url": None, "wikidata_id": None, "images": [], "summary": None}
    title, scope = card.get("wikipedia_title"), card["wikipedia_scope"]
    candidates: list[str] = []
    page = summary = entity = None
    if title and scope != "none":
        page = wikipedia_page(http, title)
        summary = wikipedia_summary(http, title)
        if page:
            out["wikipedia_url"] = page["url"]
            if scope == "exact":
                out["wikidata_id"] = page.get("wikidata")
                if page.get("wikidata"):
                    entity = wikidata_entity(http, page["wikidata"])
                if entity:
                    candidates += [f"File:{name}" for name in entity["images"]]
                if page.get("pageimage"):
                    candidates.append(f"File:{page['pageimage']}")
                candidates += [t for t in page["images"] if looks_like_photo(t)]
                if entity and entity.get("commons_category"):
                    candidates += [t for t in commons_category_files(http, entity["commons_category"]) if looks_like_photo(t)]
    if summary:
        out["summary"] = {"title": summary.get("title"), "description": summary.get("description"), "extract": summary.get("extract")}
    search_terms = [card["name_en"]] + [a for a in card.get("aliases", [])[:1]]
    for term in search_terms:
        if len(candidates) >= max_images * 3:
            break
        candidates += [t for t in commons_search(http, term) if looks_like_photo(t)]
    deduped = list(dict.fromkeys(candidates))[: max_images * 3]
    if deduped:
        resolved = commons_imageinfo(http, deduped)
        order = {t: i for i, t in enumerate(deduped)}
        resolved.sort(key=lambda r: order.get(r["title"], 999))
        out["images"] = resolved[:max_images]
    return out


# ------------------------------------------------------------------------------------------------ database


def upsert(db, card: dict, extra: dict, network: bool) -> tuple[Tea, bool]:
    tea = db.execute(select(Tea).where(Tea.slug == card["slug"])).scalar_one_or_none()
    created = tea is None
    if created:
        tea = Tea(slug=card["slug"], name_en=card["name_en"], category=TeaCategory[card["category"]],
                  origin_country=card["origin_country"], form=TeaForm[card["form"]])
        db.add(tea)
    if created or not tea.locked:
        tea.name_en = card["name_en"]
        tea.name_native = card.get("name_native")
        tea.name_romanized = card.get("name_romanized")
        tea.category = TeaCategory[card["category"]]
        tea.origin_country = card["origin_country"]
        tea.origin_region = card.get("origin_region")
        tea.cultivar = card.get("cultivar")
        tea.processing = card.get("processing")
        tea.form = TeaForm[card["form"]]
        tea.harvest_season = card.get("harvest_season")
        tea.appearance_dry = card.get("appearance_dry")
        tea.appearance_liquor = card.get("appearance_liquor")
        tea.appearance_wet = card.get("appearance_wet")
        tea.taste_profile = card.get("taste_profile")
        tea.taste_tags = list(card.get("taste_tags", []))
        tea.brewing = dict(card.get("brewing", {}))
        tea.caffeine_level = card.get("caffeine_level")
        tea.vintage_note = card.get("vintage_note")
        tea.description = card["description"]
        tea.popularity = int(card.get("popularity", 3))
        wanted = [a.strip() for a in card.get("aliases", []) if a and a.strip()]
        tea.aliases.clear()
        db.flush()  # delete the old aliases before inserting the new set (unique (tea_id, alias))
        tea.aliases = [TeaAlias(alias=a) for a in dict.fromkeys(wanted)]
    tea.status = TeaStatus.published
    if extra.get("wikipedia_url"):
        tea.wikipedia_url = extra["wikipedia_url"]
    if extra.get("wikidata_id"):
        tea.wikidata_id = extra["wikidata_id"]
    if extra.get("images") or (network and not tea.locked):
        tea.images = [
            TeaImage(url=i["url"], thumb_url=i["thumb_url"], source_page=i["source_page"], author=i["author"],
                     license=i["license"], license_url=i["license_url"], is_primary=(n == 0), sort=n)
            for n, i in enumerate(extra.get("images", []))
        ]
    tea.refresh_search_text()
    return tea, created


def write_wiki_digest(card: dict, extra: dict) -> None:
    WIKI_CACHE.mkdir(parents=True, exist_ok=True)
    digest = {
        "slug": card["slug"], "name_en": card["name_en"], "wikipedia_title": card.get("wikipedia_title"),
        "wikipedia_scope": card["wikipedia_scope"], "wikipedia_url": extra.get("wikipedia_url"),
        "wikidata_id": extra.get("wikidata_id"), "summary": extra.get("summary"),
        "images": [i["title"] for i in extra.get("images", [])],
    }
    (WIKI_CACHE / f"{card['slug']}.json").write_text(json.dumps(digest, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--group")
    parser.add_argument("--only", help="one slug")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="ignore the HTTP cache")
    parser.add_argument("--max-images", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true", help="validate cards only")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    args = parser.parse_args(argv)

    cards, errors = load_cards(args.group, args.only)
    print(f"{len(cards)} valid card(s), {len(errors)} with problems")
    for line in errors:
        print(f"  ! {line}")
    if args.dry_run:
        return 1 if errors else 0

    url = args.database_url or get_settings().database_url
    engine = make_engine(url)
    factory = make_session_factory(engine)
    http = Http(enabled=not args.no_network, refresh=args.refresh)
    created = updated = with_images = 0
    report = []
    with factory() as db:
        for n, card in enumerate(cards, 1):
            extra = enrich(http, card, args.max_images)
            write_wiki_digest(card, extra)
            tea, is_new = upsert(db, card, extra, network=not args.no_network)
            db.commit()
            created += is_new
            updated += not is_new
            with_images += bool(tea.images)
            report.append({"slug": tea.slug, "images": len(tea.images), "wikipedia": bool(tea.wikipedia_url), "new": is_new})
            print(f"  [{n:3}/{len(cards)}] {'new' if is_new else 'upd'} {tea.slug:40} photos={len(tea.images)} "
                  f"wiki={'y' if tea.wikipedia_url else '-'}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "import_report.json").write_text(json.dumps(
        {"at": _now(), "created": created, "updated": updated, "with_images": with_images, "errors": errors,
         "cards": report}, ensure_ascii=False, indent=1))
    print(f"\ndone: {created} new, {updated} updated, {with_images}/{len(cards)} with photos, "
          f"{http.requests} requests, {http.cache_hits} cache hits, {len(errors)} card errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
