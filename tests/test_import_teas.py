import json
from pathlib import Path

from sqlalchemy import select

from prolyv.models import Tea

import_teas = __import__("importlib").import_module("scripts.import_teas")

CARD = {
    "slug": "sencha", "name_en": "Sencha", "name_native": "煎茶", "name_romanized": "sencha", "aliases": ["Japanese green"],
    "category": "green", "origin_country": "Japan", "origin_region": "Shizuoka", "cultivar": "Yabukita",
    "processing": "Steamed, rolled, dried.", "form": "loose", "harvest_season": "First flush in May.",
    "appearance_dry": "Needles.", "appearance_liquor": "Green.", "appearance_wet": "Soft.",
    "taste_profile": "Grassy and sweet.", "taste_tags": ["grassy", "umami"],
    "brewing": {"grams": 5, "water_ml": 150, "temp_c": 75, "steeps": 3, "seconds": [60, 30, 60]},
    "caffeine_level": "medium", "vintage_note": "Sold as the current year's first flush.",
    "description": "Paragraph one.\n\nParagraph two.", "wikipedia_title": "Sencha", "wikipedia_scope": "exact", "popularity": 5,
}


def test_validate_accepts_a_good_card_and_reports_problems():
    assert import_teas.validate(dict(CARD)) == []
    bad = dict(CARD, slug="Bad Slug", category="purple", taste_tags=["grassy", "neon"],
               brewing={"grams": 5, "water_ml": 150, "temp_c": 75, "steeps": 3, "seconds": [60]}, popularity=9)
    problems = " ".join(import_teas.validate(bad))
    assert "bad slug" in problems and "bad category" in problems and "neon" in problems
    assert "seconds length" in problems and "popularity" in problems
    assert "missing description" in " ".join(import_teas.validate({k: v for k, v in CARD.items() if k != "description"}))


def test_load_cards_reads_folders_and_flags_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(import_teas, "CARDS_DIR", tmp_path)
    (tmp_path / "japan").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "japan" / "sencha.json").write_text(json.dumps(CARD))
    (tmp_path / "other" / "sencha.json").write_text(json.dumps(CARD))
    (tmp_path / "japan" / "broken.json").write_text("{not json")
    (tmp_path / "japan" / "_index.json").write_text("{}")
    cards, errors = import_teas.load_cards(None, None)
    assert [c["slug"] for c in cards] == ["sencha"]
    assert any("invalid JSON" in e for e in errors) and any("duplicate slug" in e for e in errors)
    only, _ = import_teas.load_cards("japan", "nothing")
    assert only == []


def test_looks_like_photo_filters_icons_and_svgs():
    assert import_teas.looks_like_photo("File:Sencha leaves.jpg")
    assert not import_teas.looks_like_photo("File:Flag of Japan.svg")
    assert not import_teas.looks_like_photo("File:Map of Shizuoka.png")
    assert not import_teas.looks_like_photo("File:Caffeine molecule.png")
    assert import_teas.looks_like_tea_photo("File:Dong Ding tea.jpg")
    assert import_teas.looks_like_tea_photo("File:凍頂烏龍茶.jpg")
    assert not import_teas.looks_like_tea_photo("File:Taiwanese cuisine.jpg")


def test_image_urls_are_cleaned_for_hotlinking():
    dirty = "https://thumb.wikimedia.org/wikipedia/commons/thumb/3/30/Dong_Ding_tea.jpg/960px-Dong_Ding_tea.jpg?utm_source=commons&utm_content=thumbnail"
    assert import_teas.clean_image_url(dirty) == "https://upload.wikimedia.org/wikipedia/commons/thumb/3/30/Dong_Ding_tea.jpg/960px-Dong_Ding_tea.jpg"
    assert import_teas.clean_image_url(None) is None


def test_enrich_uses_only_free_licensed_photos(monkeypatch):
    responses = {
        "en.wikipedia.org/w/api.php": {"query": {"pages": [{"title": "Sencha", "fullurl": "https://en.wikipedia.org/wiki/Sencha",
                                                             "pageprops": {"wikibase_item": "Q1"}, "pageimage": "Sencha cup.jpg",
                                                             "images": [{"title": "File:Sencha field.jpg"}, {"title": "File:Flag of Japan.svg"}]}]}},
        "rest_v1/page/summary": {"title": "Sencha", "description": "Japanese green tea", "extract": "Sencha is a Japanese green tea."},
        "Special:EntityData": {"entities": {"Q1": {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Sencha main.jpg"}}}]}}}},
        "list=categorymembers": {"query": {"categorymembers": []}},
        "list=search": {"query": {"search": [{"title": "File:Unfree sencha tea.jpg"}, {"title": "File:Random noodles.jpg"}]}},
        "prop=imageinfo": {"query": {"pages": [
            {"title": "File:Sencha main.jpg", "imageinfo": [{"url": "https://u/main.jpg", "thumburl": "https://u/main-t.jpg", "descriptionurl": "https://c/main",
                                                             "mime": "image/jpeg", "width": 1200, "height": 900,
                                                             "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}, "Artist": {"value": "<a href='x'>Ann</a>"}, "LicenseUrl": {"value": "https://cc/4"}}}]},
            {"title": "File:Sencha field.jpg", "imageinfo": [{"url": "https://u/field.jpg", "thumburl": "https://u/field-t.jpg", "descriptionurl": "https://c/field",
                                                              "mime": "image/jpeg", "width": 1000, "height": 700,
                                                              "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]},
            {"title": "File:Unfree sencha tea.jpg", "imageinfo": [{"url": "https://u/unfree.jpg", "mime": "image/jpeg", "width": 1000, "height": 700,
                                                               "extmetadata": {"LicenseShortName": {"value": "Fair use"}}}]},
            {"title": "File:Sencha cup.jpg", "imageinfo": [{"url": "https://u/cup.jpg", "mime": "image/jpeg", "width": 200, "height": 100,
                                                            "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]},
        ]}},
    }

    class FakeHttp:
        def get_json(self, url, params=None):
            full = url + "&" + "&".join(f"{k}={v}" for k, v in (params or {}).items())
            for needle, body in responses.items():
                if needle in full:
                    return body
            raise AssertionError(f"unexpected request {full}")

    extra = import_teas.enrich(FakeHttp(), dict(CARD), max_images=4, autosearch=True)
    assert extra["wikipedia_url"] == "https://en.wikipedia.org/wiki/Sencha" and extra["wikidata_id"] == "Q1"
    titles = [i["title"] for i in extra["images"]]
    assert titles == ["File:Sencha main.jpg", "File:Sencha field.jpg"]  # P18 first; unfree + tiny dropped
    assert extra["images"][0]["author"] == "Ann" and extra["images"][0]["license"] == "CC BY-SA 4.0"
    assert extra["summary"]["extract"].startswith("Sencha is")


def test_upsert_is_idempotent_and_respects_locked_cards(app, db):
    extra = {"wikipedia_url": "https://en.wikipedia.org/wiki/Sencha", "wikidata_id": "Q1",
             "images": [{"url": "https://u/a.jpg", "thumb_url": "https://u/a-t.jpg", "source_page": "https://c/a", "author": "Ann", "license": "CC0", "license_url": None}]}
    tea, created = import_teas.upsert(db, dict(CARD), extra)
    db.commit()
    assert created and tea.search_text.startswith("sencha 煎茶") and "japanese green" in tea.search_text
    assert tea.origin_keys == " japan shizuoka " and [a.alias for a in tea.aliases] == ["Japanese green"]
    assert len(tea.images) == 1 and tea.images[0].is_primary

    tea2, created2 = import_teas.upsert(db, dict(CARD, name_en="Sencha (updated)"), extra)
    db.commit()
    assert not created2 and tea2.id == tea.id and tea2.name_en == "Sencha (updated)"
    assert db.scalar(select(__import__("sqlalchemy").func.count()).select_from(Tea)) == 1

    tea2.locked = True
    db.commit()
    tea3, _ = import_teas.upsert(db, dict(CARD, name_en="Sencha (importer)"), extra)
    db.commit()
    assert tea3.name_en == "Sencha (updated)"  # admin edits win over the card file
    tea3.status = __import__("prolyv.models").models.TeaStatus.pending
    db.commit()
    tea4, _ = import_teas.upsert(db, dict(CARD), {"images": []})
    db.commit()
    assert len(tea4.images) == 1 and tea4.status.value == "pending"  # so do its photos and its draft status


def test_curated_images_skip_the_search_and_get_local_webp_copies(tmp_path):
    from io import BytesIO

    from PIL import Image

    class FakeHttp:
        calls = 0

        def get_json(self, url, params=None):
            text = url + str(params)
            assert "list" not in str(params or {}) and "imageinfo" not in text, "automatic photo search must not run"
            return {"query": {"pages": [{"title": "Sencha", "fullurl": "https://en.wikipedia.org/wiki/Sencha", "images": []}]}} if "api.php" in url else None

        def get_bytes(self, url):
            FakeHttp.calls += 1
            buffer = BytesIO()
            Image.new("RGB", (2000, 1000), "green").save(buffer, "JPEG", exif=b"Exif\x00\x00secret")
            return buffer.getvalue()

    card = dict(CARD, images=[
        {"url": "https://u/a.jpg", "source_page": "https://c/a", "author": "<b>Ann</b>", "license": "CC BY 4.0", "kind": "dry_leaf", "alt": "Dry sencha"},
        {"url": "https://u/shop.jpg", "license": "All rights reserved"},
    ])
    http = FakeHttp()
    extra = import_teas.enrich(http, card, max_images=4)
    assert [i["url"] for i in extra["images"]] == ["https://u/a.jpg"] and extra["images"][0]["author"] == "Ann"

    local = import_teas.localize_image(http, tmp_path, "sencha", extra["images"][0])
    assert local["url"].startswith("/media/teas/sencha/") and local["url"].endswith("-1200.webp") and local["thumb_url"].endswith("-400.webp")
    with Image.open(tmp_path / local["url"].removeprefix("/media/")) as big:
        assert big.format == "WEBP" and big.size == (1200, 600) and not big.getexif()
    with Image.open(tmp_path / local["thumb_url"].removeprefix("/media/")) as small:
        assert small.size == (400, 200)
    import_teas.localize_image(http, tmp_path, "sencha", extra["images"][0])
    assert FakeHttp.calls == 1  # files on disk are reused


def test_upsert_stores_encyclopedia_fields(app, db):
    card = dict(CARD, name_zh="煎茶", name_ru="Сенча", history="Paragraph.", liquor_hex="#B5C96A", leaf_hex="bad", confidence="high",
                sources=["https://en.wikipedia.org/wiki/Sencha", "javascript:alert(1)"], i18n={"ru": {"description": "Описание."}})
    tea, _ = import_teas.upsert(db, card, {"images": []})
    db.commit()
    assert tea.name_zh == "煎茶" and tea.liquor_hex == "#B5C96A" and tea.leaf_hex is None and tea.languages == ["en", "ru"]
    assert tea.sources == ["https://en.wikipedia.org/wiki/Sencha"] and "сенча" in tea.search_text and "煎茶" in tea.search_text


def test_dry_run_on_the_real_catalog_folder():
    cards, errors = import_teas.load_cards(None, None)
    assert isinstance(cards, list)
    assert Path(import_teas.CARDS_DIR).exists()


def make_http(handler, **kwargs):
    """Http on a fake transport and a fake clock: `sleep` only records and moves time on."""
    import httpx2 as httpx

    now = [1000.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    http = import_teas.Http(enabled=True, refresh=True, transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: now[0], **kwargs)
    return http, slept, now


def test_user_agent_has_a_contact_and_requests_are_paced_under_the_limit():
    import httpx2 as httpx

    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, content=b"x")

    http, slept, _ = make_http(handler)
    for n in range(4):
        assert http.get_bytes(f"https://upload.wikimedia.org/{n}.jpg") == b"x"
    assert "https://github.com/coldshrine)" in seen[0]
    assert len(slept) == 3 and all(wait >= 60 / 200 for wait in slept)


def test_retry_after_is_obeyed_in_both_forms():
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    import httpx2 as httpx

    replies = [httpx.Response(429, headers={"Retry-After": "37"}), httpx.Response(200, content=b"ok")]
    http, slept, _ = make_http(lambda request: replies.pop(0), pause=0)
    assert http.get_bytes("https://upload.wikimedia.org/a.jpg") == b"ok"
    assert slept == [37] and http.rate_limited == 1

    when = format_datetime(datetime.now(UTC) + timedelta(seconds=90), usegmt=True)
    replies = [httpx.Response(503, headers={"Retry-After": when}), httpx.Response(200, content=b"ok")]
    http, slept, _ = make_http(lambda request: replies.pop(0), pause=0)
    assert http.get_bytes("https://upload.wikimedia.org/a.jpg") == b"ok"
    assert 80 < slept[0] <= 90


def test_no_retry_after_means_backing_off_from_five_seconds():
    import httpx2 as httpx

    http, slept, _ = make_http(lambda request: httpx.Response(429), pause=0)
    assert http.get_bytes("https://upload.wikimedia.org/a.jpg") is None
    assert slept == [5, 10, 20, 40, 80]


def test_a_very_long_wait_stops_using_the_network_instead_of_hanging():
    import httpx2 as httpx

    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(429, headers={"Retry-After": "7200"})

    http, slept, _ = make_http(handler, pause=0)
    assert http.get_bytes("https://upload.wikimedia.org/a.jpg") is None
    assert http.get_json("https://en.wikipedia.org/w/api.php", {"x": "new"}) is None
    assert http.stopped and "7200" in http.stopped and len(calls) == 1 and slept == []


def test_repeated_network_errors_stop_the_retries(tmp_path, monkeypatch):
    import httpx2 as httpx

    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("offline")

    http, _, _ = make_http(handler, pause=0)
    for n in range(10):
        assert http.get_bytes(f"https://upload.wikimedia.org/{n}.jpg") is None
    assert http.stopped and len(calls) <= import_teas.MAX_FAILURES_IN_A_ROW


def test_a_broken_cache_file_counts_as_a_miss(tmp_path, monkeypatch):
    import hashlib

    import httpx2 as httpx

    monkeypatch.setattr(import_teas, "HTTP_CACHE", tmp_path)
    url = "https://en.wikipedia.org/w/api.php?a=1"
    (tmp_path / f"{hashlib.sha1(url.encode()).hexdigest()}.json").write_text('{"body": {"cut sho')
    http, _, _ = make_http(lambda request: httpx.Response(200, json={"fresh": True}))
    http.refresh = False
    assert http.get_json("https://en.wikipedia.org/w/api.php", {"a": 1}) == {"fresh": True}
    assert http.get_json("https://en.wikipedia.org/w/api.php", {"a": 1}) == {"fresh": True} and http.cache_hits == 1 and http.requests == 1


def test_photos_come_only_from_the_card_unless_the_search_is_asked_for():
    class NoSearch:
        def get_json(self, url, params=None):
            assert "api.php" in url and "en.wikipedia" in url and params.get("titles"), f"only the article may be asked for: {url} {params}"
            return {"query": {"pages": [{"title": "Sencha", "fullurl": "https://en.wikipedia.org/wiki/Sencha", "pageprops": {"wikibase_item": "Q1"},
                                         "pageimage": "Sencha.jpg", "images": [{"title": "File:Sencha tea.jpg"}]}]}}

    for card in (dict(CARD, images=[]), dict(CARD), dict(CARD, images=[])):
        extra = import_teas.enrich(NoSearch(), card, max_images=4)
        assert extra["images"] == [] and extra["wikipedia_url"] and extra["wikidata_id"] == "Q1"
    # an empty list stays empty even when the search is switched on
    assert import_teas.enrich(NoSearch(), dict(CARD, images=[]), max_images=4, autosearch=True)["images"] == []


def run_main(tmp_path, monkeypatch, transport, *extra):
    """The importer against files, a database and a media folder in tmp_path, with Wikimedia faked."""
    monkeypatch.setattr(import_teas, "HTTP_CACHE", tmp_path / "cache" / "http")
    monkeypatch.setattr(import_teas, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(import_teas, "WIKI_CACHE", tmp_path / "cache" / "wiki")
    monkeypatch.setattr(import_teas, "CARDS_DIR", tmp_path / "teas")
    real = import_teas.Http
    used = []

    def build(enabled, refresh):
        http = real(enabled=enabled, refresh=refresh, pause=0, transport=transport, sleep=lambda seconds: None)
        used.append(http)
        return http

    monkeypatch.setattr(import_teas, "Http", build)
    code = import_teas.main(["--database-url", f"sqlite:///{tmp_path}/import.db", "--media-dir", str(tmp_path / "media"), *extra])
    monkeypatch.setattr(import_teas, "Http", real)
    return code, used[0]


def test_many_cards_a_bad_one_and_a_second_run(tmp_path, monkeypatch):
    from io import BytesIO

    import httpx2 as httpx
    from PIL import Image
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from prolyv.models import Base
    from prolyv.search import ensure_fts

    engine = create_engine(f"sqlite:///{tmp_path}/import.db")
    Base.metadata.create_all(engine)
    ensure_fts(engine)
    folder = tmp_path / "teas" / "green"
    folder.mkdir(parents=True)
    picture = BytesIO()
    Image.new("RGB", (1600, 900), "green").save(picture, "JPEG")
    for n in range(30):
        card = dict(CARD, slug=f"tea-{n:02}", name_en=f"Tea {n:02}", wikipedia_title=f"Tea {n:02}",
                    images=[{"url": f"https://upload.wikimedia.org/{n}.jpg", "license": "CC0", "source_page": "https://commons.wikimedia.org/x"}])
        (folder / f"tea-{n:02}.json").write_text(json.dumps(card))
    (folder / "tea-05.json").write_text(json.dumps(dict(CARD, slug="tea-05", description=None)))  # wrong type
    (folder / "tea-06.json").write_text(json.dumps(dict(CARD, slug="tea-06", aliases=[7], images=[])))  # crashes while saving
    (folder / "tea-07.json").write_text("[1, 2]")

    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "upload.wikimedia.org":
            if "/9.jpg" in str(request.url) and sum("/9.jpg" in r for r in requests) == 1:
                return httpx.Response(429, headers={"Retry-After": "3"})
            return httpx.Response(200, content=picture.getvalue())
        title = request.url.params.get("titles", "?")
        return httpx.Response(200, json={"query": {"pages": [{"title": title, "fullurl": f"https://en.wikipedia.org/wiki/{title}"}]}})

    transport = httpx.MockTransport(handler)
    code, http = run_main(tmp_path, monkeypatch, transport)
    assert code == 1  # the broken cards are reported
    assert http.rate_limited == 1 and not http.stopped
    with Session(engine) as db:
        teas = db.execute(select(Tea)).scalars().all()
        assert len(teas) == 27 and {t.slug for t in teas}.isdisjoint({"tea-05", "tea-06", "tea-07"})
        assert all(t.images[0].url.startswith("/media/teas/") for t in teas)
    report = json.loads((tmp_path / "cache" / "import_report.json").read_text())
    assert len(report["errors"]) == 3 and report["remote_images"] == 0
    assert all(c["images"] == 1 for c in report["cards"])

    requests.clear()
    code, http = run_main(tmp_path, monkeypatch, transport)
    assert requests == [] and http.requests == 0

    # a photo taken off the card disappears from the site and the disk; a card file removed is reported, then unpublished
    (folder / "tea-10.json").write_text(json.dumps(dict(CARD, slug="tea-10", name_en="Tea 10", wikipedia_title="Tea 10", images=[])))
    (folder / "tea-11.json").unlink()
    code, _ = run_main(tmp_path, monkeypatch, transport)
    with Session(engine) as db:
        tea = db.execute(select(Tea).where(Tea.slug == "tea-10")).scalar_one()
        assert tea.images == [] and not list((tmp_path / "media" / "teas" / "tea-10").iterdir())
        assert db.execute(select(Tea).where(Tea.slug == "tea-11")).scalar_one().status.value == "published"
    assert json.loads((tmp_path / "cache" / "import_report.json").read_text())["orphans"] == ["tea-11"]
    run_main(tmp_path, monkeypatch, transport, "--prune")
    with Session(engine) as db:
        assert db.execute(select(Tea).where(Tea.slug == "tea-11")).scalar_one().status.value == "pending"


def test_import_accepts_an_empty_history(app, db):
    card = dict(CARD, history="", confidence="low", images=[])
    assert import_teas.validate(card) == []
    tea, _ = import_teas.upsert(db, card, {"images": []})
    db.commit()
    assert not tea.localized("en")["history_paragraphs"]
