#!/usr/bin/env python
"""Import tea cards from data/teas/**/*.json into the database.

Photos come only from the `images` list in each card file: a card without one has no photos, and photos no longer
listed are removed. The old automatic search of Wikimedia found unchecked pictures, so it runs only on request.

Safe to stop and re-run: every card is committed on its own, a bad card is reported and skipped, responses are cached
in data/cache/http/ and converted photos stay in media/teas/, so a second run makes no requests for them. Cards edited
by an admin in the UI are locked and keep their text, photos and status.

Wikimedia allows 200 requests a minute to a client whose User-Agent has a contact, at most 3 at once, and Retry-After
must be obeyed. This runs one request at a time at 180 a minute. If Wikimedia asks to wait longer than LONGEST_WAIT the
run finishes without the network and exits 1: run it again later and it carries on.

    uv run python scripts/import_teas.py                 # everything
    uv run python scripts/import_teas.py --group japan   # one folder
    uv run python scripts/import_teas.py --only sencha   # one card
    uv run python scripts/import_teas.py --no-network    # cards + cached data only
    uv run python scripts/import_teas.py --dry-run       # validate the JSON, touch nothing
    uv run python scripts/import_teas.py --prune         # also unpublish teas whose card file is gone
    uv run python scripts/import_teas.py --autosearch-photos   # the old search, for cards with no `images` key
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx2 as httpx
from PIL import Image, ImageOps
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prolyv.config import get_settings  # noqa: E402
from prolyv.db import make_engine, make_session_factory  # noqa: E402
from prolyv.models import Tea, TeaAlias, TeaCategory, TeaForm, TeaImage, TeaStatus  # noqa: E402
from prolyv.search import ensure_fts, rebuild  # noqa: E402

CARDS_DIR = ROOT / "data" / "teas"
CACHE_DIR = ROOT / "data" / "cache"
HTTP_CACHE = CACHE_DIR / "http"
WIKI_CACHE = CACHE_DIR / "wiki"

USER_AGENT = "ProlyvTeaCatalog/0.1 (tea diary catalog importer; https://github.com/coldshrine)"
REQUESTS_PER_MINUTE = 180
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 5  # Wikimedia asks for at least this when a 429 or 503 comes without Retry-After
LONGEST_WAIT = 900  # asked to wait longer than this, the run goes on without the network
MAX_FAILURES_IN_A_ROW = 8  # the network is down: stop retrying for every remaining card
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
IMAGE_KINDS = {"dry_leaf", "liquor", "wet_leaf", "cake", "plant", "plantation", "serving", "other"}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
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
    """One request at a time, paced, obeying Retry-After. Nothing here raises: a failed request returns None."""

    def __init__(self, enabled: bool, refresh: bool, pause: float = 60 / REQUESTS_PER_MINUTE, transport=None,  # noqa: ANN001
                 sleep=time.sleep, clock=time.monotonic) -> None:  # noqa: ANN001
        self.enabled, self.refresh, self.pause = enabled, refresh, pause
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=40, follow_redirects=True, transport=transport)
        self.sleep, self.clock = sleep, clock
        self.requests = 0
        self.cache_hits = 0
        self.rate_limited = 0
        self.stopped: str | None = None
        self._failures = 0
        self._last: float | None = None

    def get_json(self, url: str, params: dict | None = None) -> dict | None:
        full = f"{url}?{urlencode(params)}" if params else url
        path = HTTP_CACHE / f"{hashlib.sha1(full.encode()).hexdigest()}.json"
        if path.exists() and not self.refresh:
            try:
                body = json.loads(path.read_text())["body"]
            except (ValueError, KeyError, TypeError):
                path.unlink(missing_ok=True)  # cut short by an earlier crash
            else:
                self.cache_hits += 1
                return body
        body = self._fetch(full)
        if body is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".{os.getpid()}.tmp")
            temp.write_text(json.dumps({"url": full, "fetched_at": _now(), "body": body}, ensure_ascii=False))
            temp.replace(path)
        return body

    def get_bytes(self, url: str, limit: int = 40_000_000) -> bytes | None:
        """A file for the local media copy. Not cached here: the re-encoded files are the cache."""
        response = self._get(url)
        if response is None or response.status_code != 200 or len(response.content) > limit:
            return None
        return response.content

    def _fetch(self, url: str) -> dict | None:
        response = self._get(url)
        if response is None:
            return None
        if response.status_code == 404:
            return {"_missing": True}
        if response.status_code != 200:
            print(f"    HTTP {response.status_code} for {url}", file=sys.stderr)
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _get(self, url: str):  # noqa: ANN202
        """The final response, or None after retries. Waits between all requests, and as long as the server asks."""
        for attempt in range(MAX_ATTEMPTS):
            if not self.enabled:
                return None
            self._pace()
            try:
                response = self.client.get(url)
                self.requests += 1
            except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
                print(f"    network error ({exc.__class__.__name__}) for {url}, attempt {attempt + 1}", file=sys.stderr)
                self._failed()
                self.sleep(BACKOFF_SECONDS * 2 ** attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                self.rate_limited += response.status_code == 429
                wait = retry_after(response) or BACKOFF_SECONDS * 2 ** attempt
                if wait > LONGEST_WAIT:
                    self._stop(f"Wikimedia asked to wait {int(wait)} seconds")
                    return None
                print(f"    HTTP {response.status_code}, waiting {int(wait)} s", file=sys.stderr)
                self.sleep(wait)
                continue
            self._failures = 0
            return response
        self._failed()
        return None

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.pause - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def _failed(self) -> None:
        self._failures += 1
        if self._failures >= MAX_FAILURES_IN_A_ROW:
            self._stop(f"{self._failures} requests failed in a row")

    def _stop(self, reason: str) -> None:
        self.enabled = False
        self.stopped = reason
        print(f"  ! going on without the network: {reason}. Run the importer again later to finish.", file=sys.stderr)


def retry_after(response) -> float | None:  # noqa: ANN001
    """Retry-After in seconds. It may be a number or an HTTP date."""
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
            except ValueError as exc:
                errors.append(f"{rel(path)}: invalid JSON ({exc})")
                continue
            if not isinstance(card, dict):
                errors.append(f"{rel(path)}: not a card")
                continue
            card["_group"], card["_path"] = folder.name, rel(path)
            if only and card.get("slug") != only:
                continue
            try:
                problems = validate(card)
            except (TypeError, AttributeError, KeyError, ValueError) as exc:
                problems = [f"a field has the wrong type ({exc})"]
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
    if "images" in card and not isinstance(card["images"], list):
        problems.append("images must be a list")
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


def commons_search(http: Http, query: str, limit: int = 10, exact: bool = True) -> list[str]:
    phrase = f'"{query}"' if exact else query
    data = http.get_json(
        COMMONS_API,
        {"action": "query", "format": "json", "formatversion": 2, "list": "search", "srnamespace": 6,
         "srlimit": limit, "srsearch": f"{phrase} filetype:bitmap"},
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
                    "url": clean_image_url(info["url"]),
                    "thumb_url": clean_image_url(info.get("thumburl") or info["url"]),
                    "source_url": clean_image_url(info["url"]),
                    "download_url": clean_image_url(info.get("thumburl") or info["url"]),
                    "source_page": info.get("descriptionurl"),
                    "author": strip_html((meta.get("Artist") or {}).get("value")),
                    "license": license_name,
                    "license_url": strip_html((meta.get("LicenseUrl") or {}).get("value")),
                }
            )
    return results


TEA_WORDS = re.compile(r"tea|cha\b|chá|茶|oolong|wulong|pu-?erh|pu'er|puer|sencha|matcha|gyokuro|hojicha|bancha|genmaicha|"
                       r"leaf|leaves|brew|infus|steep|gaiwan|teapot|kyusu|yixing|tisane|rooibos|mate\b|herbal|chai",
                       re.IGNORECASE)


def looks_like_photo(file_title: str) -> bool:
    name = file_title.lower()
    return name.endswith(IMAGE_EXT) and not any(word in name for word in BAD_IMAGE_WORDS)


def looks_like_tea_photo(file_title: str) -> bool:
    """Search hits must mention tea in the file name: 'Taiwanese cuisine.jpg' is not a Dong Ding photo."""
    return looks_like_photo(file_title) and TEA_WORDS.search(file_title) is not None


def clean_image_url(url: str | None) -> str | None:
    """Drop the tracking query the API appends and use the canonical upload host for hotlinking."""
    if not url:
        return None
    url = url.split("?", 1)[0]
    return url.replace("https://thumb.wikimedia.org/", "https://upload.wikimedia.org/")


def curated_images(card: dict) -> list[dict]:
    """Photos listed in the card file. Only free licences, the same rule as the automatic search."""
    images = []
    for item in card.get("images") or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        license_name = strip_html(item.get("license"))
        if not license_name or not LICENSE_OK.search(license_name):
            continue
        images.append({
            "title": item.get("source_page") or item["url"], "url": item["url"], "thumb_url": item.get("thumb_url") or item["url"],
            "source_page": item.get("source_page"), "author": strip_html(item.get("author")), "license": license_name,
            "license_url": item.get("license_url"), "kind": item.get("kind") if item.get("kind") in IMAGE_KINDS else "other",
            "alt": strip_html(item.get("alt")), "source_url": item["url"],
        })
    return images


def localize_image(http: Http, media_dir: Path, slug: str, image: dict) -> dict:
    """Re-encode into media/teas/<slug>/ as WebP 1200 and 400 px, served from our own domain. EXIF is dropped.

    Files already on disk are reused, so re-running costs nothing. If the download fails the remote URL stays."""
    source = image.get("source_url") or image["url"]
    digest = hashlib.sha1(source.encode()).hexdigest()[:12]
    folder = media_dir / "teas" / slug
    big, small = folder / f"{digest}-1200.webp", folder / f"{digest}-400.webp"
    if not (big.exists() and small.exists()):
        data = http.get_bytes(image.get("download_url") or source)
        if data is None:
            return image
        try:
            with Image.open(BytesIO(data)) as raw:
                picture = ImageOps.exif_transpose(raw).convert("RGB")
            folder.mkdir(parents=True, exist_ok=True)
            large = picture.copy()
            large.thumbnail((1200, 1200))
            picture.thumbnail((400, 400))
            # renamed into place only when complete, so a crash never leaves half a photo that looks done
            for target, version, quality in ((big, large, 82), (small, picture, 75)):
                temp = target.with_suffix(".tmp")
                version.save(temp, "WEBP", quality=quality, method=4)
                temp.replace(target)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            print(f"    could not convert {source}: {exc.__class__.__name__}", file=sys.stderr)
            return image
    return {**image, "url": f"/media/teas/{slug}/{big.name}", "thumb_url": f"/media/teas/{slug}/{small.name}"}


def remove_old_photos(media_dir: Path, slug: str, images: list[dict]) -> None:
    """A photo taken off the card must also stop being served."""
    folder = media_dir / "teas" / slug
    if not folder.is_dir():
        return
    keep = {Path(url).name for image in images for url in (image["url"], image["thumb_url"]) if url and url.startswith("/media/")}
    for path in folder.iterdir():
        if path.name not in keep:
            path.unlink(missing_ok=True)


def enrich(http: Http, card: dict, max_images: int, autosearch: bool = False) -> dict:
    """The Wikipedia link and the photos for one card.

    Photos are the card's `images` list, even an empty one. Only with `autosearch`, and only for a card with no such
    key, does the old search of Wikimedia run. Umbrella articles contribute the link, never their photos."""
    out: dict = {"wikipedia_url": None, "wikidata_id": None, "images": [], "summary": None}
    title, scope = card.get("wikipedia_title"), card["wikipedia_scope"]
    search = autosearch and "images" not in card
    candidates: list[str] = []
    page = summary = entity = None
    if title and scope != "none":
        page = wikipedia_page(http, title)
        if search:
            summary = wikipedia_summary(http, title)
        if page:
            out["wikipedia_url"] = page["url"]
            if scope == "exact":
                out["wikidata_id"] = page.get("wikidata")
                if search:
                    if page.get("wikidata"):
                        entity = wikidata_entity(http, page["wikidata"])
                    if entity:
                        candidates += [f"File:{name}" for name in entity["images"]]
                    if page.get("pageimage"):
                        candidates.append(f"File:{page['pageimage']}")
                    # inline article images include navbox pictures (cuisine, flags): keep only tea-looking ones
                    candidates += [t for t in page["images"] if looks_like_tea_photo(t)]
                    if entity and entity.get("commons_category"):
                        candidates += [t for t in commons_category_files(http, entity["commons_category"]) if looks_like_photo(t)]
    if summary:
        out["summary"] = {"title": summary.get("title"), "description": summary.get("description"), "extract": summary.get("extract")}
    if not search:
        out["images"] = curated_images(card)
        return out
    # Commons search: the English name, the native name, then the first alias; results must look like tea photos.
    search_terms = [card["name_en"], card.get("name_native"), *(card.get("aliases") or [])[:1]]
    for term in [t for t in search_terms if t]:
        if len(candidates) >= max_images * 3:
            break
        candidates += [t for t in commons_search(http, term) if looks_like_tea_photo(t)]
    if len(candidates) < max_images:  # looser query as a last resort
        candidates += [t for t in commons_search(http, f"{card['name_en']} tea", exact=False) if looks_like_tea_photo(t)]
    deduped = list(dict.fromkeys(candidates))[: max_images * 3]
    if deduped:
        resolved = commons_imageinfo(http, deduped)
        order = {t: i for i, t in enumerate(deduped)}
        resolved.sort(key=lambda r: order.get(r["title"], 999))
        out["images"] = resolved[:max_images]
    return out


# ------------------------------------------------------------------------------------------------ database


def upsert(db, card: dict, extra: dict, keep_photos_if_none: bool = False) -> tuple[Tea, bool]:
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
        tea.name_zh = card.get("name_zh")
        tea.name_ru = card.get("name_ru")
        tea.history = card.get("history")
        tea.liquor_hex = card["liquor_hex"] if HEX_RE.match(str(card.get("liquor_hex"))) else None
        tea.leaf_hex = card["leaf_hex"] if HEX_RE.match(str(card.get("leaf_hex"))) else None
        tea.confidence = card.get("confidence") if card.get("confidence") in ("high", "medium", "low") else None
        tea.sources = [u for u in card.get("sources") or [] if isinstance(u, str) and u.startswith(("http://", "https://"))]
        tea.i18n = card.get("i18n") if isinstance(card.get("i18n"), dict) else {}
        wanted = [a.strip() for a in card.get("aliases", []) if a and a.strip()]
        tea.aliases.clear()
        db.flush()  # delete the old aliases before inserting the new set (unique (tea_id, alias))
        tea.aliases = [TeaAlias(alias=a) for a in dict.fromkeys(wanted)]
        tea.status = TeaStatus.published
    if extra.get("wikipedia_url"):
        tea.wikipedia_url = extra["wikipedia_url"]
    if extra.get("wikidata_id"):
        tea.wikidata_id = extra["wikidata_id"]
    if (created or not tea.locked) and (extra.get("images") or not keep_photos_if_none):
        tea.images = [
            TeaImage(url=i["url"], thumb_url=i["thumb_url"], source_url=i.get("source_url"), kind=i.get("kind"),
                     alt=i.get("alt") or card["name_en"], source_page=i.get("source_page"), author=i.get("author"),
                     license=i.get("license"), license_url=i.get("license_url"), is_primary=(n == 0), sort=n)
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
        "images": [i.get("title") for i in extra.get("images", [])],
    }
    (WIKI_CACHE / f"{card['slug']}.json").write_text(json.dumps(digest, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--group")
    parser.add_argument("--only", help="one slug")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="ignore the HTTP cache")
    parser.add_argument("--max-images", type=int, default=4, help="for --autosearch-photos")
    parser.add_argument("--autosearch-photos", action="store_true", help="search Wikimedia for cards with no `images` key; these photos are unchecked")
    parser.add_argument("--no-media", action="store_true", help="keep remote image URLs instead of local WebP copies")
    parser.add_argument("--prune", action="store_true", help="unpublish teas whose card file is gone (full runs only, locked cards stay)")
    parser.add_argument("--dry-run", action="store_true", help="validate cards only")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument("--media-dir", help="override MEDIA_DIR")
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
    media_dir = Path(args.media_dir or get_settings().media_dir)
    if not media_dir.is_absolute():
        media_dir = ROOT / media_dir
    created = updated = with_images = remote_images = 0
    report = []
    with factory() as db:
        for n, card in enumerate(cards, 1):
            try:
                extra = enrich(http, card, args.max_images, autosearch=args.autosearch_photos)
                listed = len(card.get("images") or [])
                if len(extra["images"]) < listed:
                    print(f"  ! {card['slug']}: {listed - len(extra['images'])} photo(s) dropped, no free licence or no URL")
                if not args.no_media:
                    extra["images"] = [localize_image(http, media_dir, card["slug"], image) for image in extra["images"]]
                if extra.get("summary"):
                    write_wiki_digest(card, extra)
                tea, is_new = upsert(db, card, extra, keep_photos_if_none=args.autosearch_photos)
                db.commit()
                if not args.no_media and http.enabled and not tea.locked:
                    remove_old_photos(media_dir, tea.slug, extra["images"])
            except Exception as exc:  # noqa: BLE001 - one bad card must not lose the other thousands
                db.rollback()
                errors.append(f"{card['_path']}: {exc.__class__.__name__}: {exc}")
                print(f"  ! {card['slug']}: {exc.__class__.__name__}: {exc}")
                continue
            created += is_new
            updated += not is_new
            with_images += bool(tea.images)
            remote_images += sum(1 for image in tea.images if not image.url.startswith("/media/"))
            report.append({"slug": tea.slug, "images": len(tea.images), "wikipedia": bool(tea.wikipedia_url), "new": is_new})
            print(f"  [{n:4}/{len(cards)}] {'new' if is_new else 'upd'} {tea.slug:40} photos={len(tea.images)} "
                  f"wiki={'y' if tea.wikipedia_url else '-'}")
        orphans = []
        if not args.group and not args.only:
            known = {card["slug"] for card in cards}
            broken = {Path(line.split(":", 1)[0]).stem for line in errors}
            live = db.execute(select(Tea).where(Tea.status == TeaStatus.published, Tea.locked.is_(False))).scalars().all()
            orphans = [tea for tea in live if tea.slug not in known and tea.slug not in broken]
            for tea in orphans:
                print(f"  ? {tea.slug}: no card file{', unpublished' if args.prune else ''}")
                if args.prune:
                    tea.status = TeaStatus.pending
        ensure_fts(engine)
        rebuild(db)
        db.commit()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "import_report.json").write_text(json.dumps(
        {"at": _now(), "created": created, "updated": updated, "with_images": with_images, "remote_images": remote_images,
         "orphans": [tea.slug for tea in orphans], "network_stopped": http.stopped, "errors": errors, "cards": report},
        ensure_ascii=False, indent=1))
    print(f"\ndone: {created} new, {updated} updated, {with_images}/{len(cards)} with photos, {remote_images} photos not copied locally, "
          f"{http.requests} requests, {http.rate_limited} rate limited, {http.cache_hits} cache hits, {len(errors)} card errors, "
          f"{len(orphans)} teas without a card file{' unpublished' if args.prune else ''}")
    if http.stopped:
        print(f"stopped using the network: {http.stopped}. Run the importer again later to finish.")
    return 1 if errors or http.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
