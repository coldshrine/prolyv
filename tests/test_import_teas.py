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

    extra = import_teas.enrich(FakeHttp(), dict(CARD), max_images=4)
    assert extra["wikipedia_url"] == "https://en.wikipedia.org/wiki/Sencha" and extra["wikidata_id"] == "Q1"
    titles = [i["title"] for i in extra["images"]]
    assert titles == ["File:Sencha main.jpg", "File:Sencha field.jpg"]  # P18 first; unfree + tiny dropped
    assert extra["images"][0]["author"] == "Ann" and extra["images"][0]["license"] == "CC BY-SA 4.0"
    assert extra["summary"]["extract"].startswith("Sencha is")


def test_upsert_is_idempotent_and_respects_locked_cards(app, db):
    extra = {"wikipedia_url": "https://en.wikipedia.org/wiki/Sencha", "wikidata_id": "Q1",
             "images": [{"url": "https://u/a.jpg", "thumb_url": "https://u/a-t.jpg", "source_page": "https://c/a", "author": "Ann", "license": "CC0", "license_url": None}]}
    tea, created = import_teas.upsert(db, dict(CARD), extra, network=True)
    db.commit()
    assert created and tea.search_text.startswith("sencha 煎茶") and "japanese green" in tea.search_text
    assert tea.origin_keys == " japan shizuoka " and [a.alias for a in tea.aliases] == ["Japanese green"]
    assert len(tea.images) == 1 and tea.images[0].is_primary

    tea2, created2 = import_teas.upsert(db, dict(CARD, name_en="Sencha (updated)"), extra, network=True)
    db.commit()
    assert not created2 and tea2.id == tea.id and tea2.name_en == "Sencha (updated)"
    assert db.scalar(select(__import__("sqlalchemy").func.count()).select_from(Tea)) == 1

    tea2.locked = True
    db.commit()
    tea3, _ = import_teas.upsert(db, dict(CARD, name_en="Sencha (importer)"), extra, network=True)
    db.commit()
    assert tea3.name_en == "Sencha (updated)"  # admin edits win over the card file


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
    tea, _ = import_teas.upsert(db, card, {"images": []}, network=False)
    db.commit()
    assert tea.name_zh == "煎茶" and tea.liquor_hex == "#B5C96A" and tea.leaf_hex is None and tea.languages == ["en", "ru"]
    assert tea.sources == ["https://en.wikipedia.org/wiki/Sencha"] and "сенча" in tea.search_text and "煎茶" in tea.search_text


def test_dry_run_on_the_real_catalog_folder():
    cards, errors = import_teas.load_cards(None, None)
    assert isinstance(cards, list)
    assert Path(import_teas.CARDS_DIR).exists()
