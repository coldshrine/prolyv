import re
import uuid

from sqlalchemy import select

from prolyv.models import SessionComment, TeaSession, Visibility
from tests.conftest import csrf_token, telegram_login
from tests.test_teas import make_tea


def new_session(client, token, **overrides):
    data = {"csrf_token": token, "tea_name": "Tie Guan Yin", "grams": "7", "water_ml": "110", "temp_c": "95",
            "vessel": "gaiwan", "rinse": "on", "rating": "4", "taste_tags": ["floral", "creamy"],
            "note": "Orchid and butter.\n\nKeep steep six short.", "visibility": "private", "allow_comments": "on",
            "brewed_at": "2026-11-05", "steep_seconds": ["15", "10", "15"], "steep_note": ["rinse first", "", "longer"]}
    data.update(overrides)
    return client.post("/sessions/new", data=data, follow_redirects=False)


def session_id_from(response) -> str:
    match = re.search(r"/sessions/([0-9a-f-]{36})", response.headers["location"])
    assert match, response.headers.get("location")
    return match.group(1)


def test_diary_and_new_session_require_login(client):
    assert client.get("/diary", follow_redirects=False).headers["location"] == "/login?next=/diary"
    assert client.get("/sessions/new", follow_redirects=False).status_code == 303


def test_create_session_with_steeps_and_view_it(client, db):
    telegram_login(client, username="olena_tea")
    response = new_session(client, csrf_token(client))
    assert response.status_code == 303
    sid = session_id_from(response)

    sess = db.get(TeaSession, uuid.UUID(sid))
    assert sess.tea_name == "Tie Guan Yin" and sess.grams == 7 and sess.water_ml == 110 and sess.temp_c == 95
    assert sess.vessel == "gaiwan" and sess.rinse and sess.rating == 4 and sess.taste_tags == ["floral", "creamy"]
    assert [(s.n, s.seconds, s.note) for s in sess.steeps] == [(1, 15, "rinse first"), (2, 10, None), (3, 15, "longer")]
    assert sess.visibility == Visibility.private and sess.brewed_at.date().isoformat() == "2026-11-05"

    page = client.get(f"/sessions/{sid}").text
    assert "Tie Guan Yin" in page and "private · only you can see this" in page
    assert "7 g" in page and "110 ml" in page and "95 °C" in page and "Gaiwan" in page
    assert "15 s" in page and "rinse first" in page and "★★★★☆" in page
    assert "Session saved" in page and "Make public" in page and "Brew it the same way" in page
    assert "Keep steep six short." in page

    diary = client.get("/diary").text
    assert "Tie Guan Yin" in diary and "Last 30 days" in diary and "Cups calendar" in diary and "Shelf" in diary
    assert "1 session logged" in diary


def test_private_sessions_are_hidden_from_others(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client)))
    client.cookies.clear()
    assert client.get(f"/sessions/{sid}").status_code == 404
    telegram_login(client, tg_id="2", username="mike")
    assert client.get(f"/sessions/{sid}").status_code == 404
    assert client.get(f"/sessions/{sid}/edit").status_code == 404
    assert "Tie Guan Yin" not in client.get("/u/olena_tea").text


def test_public_session_shows_on_profile_and_tea_card(client, db):
    tea = make_tea(db, "tie-guan-yin", "Tie Guan Yin")
    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    sid = session_id_from(new_session(client, token, tea_id=str(tea.id), visibility="public"))
    client.cookies.clear()

    page = client.get(f"/sessions/{sid}").text
    assert "public session" in page and "Share" in page and "tea card" in page
    assert 'property="og:title" content="Tie Guan Yin · Olena K.' in page
    assert "Tie Guan Yin" in client.get("/u/olena_tea").text
    assert "1</p><p class=\"stat-label\">public sessions" in client.get("/u/olena_tea").text.replace("\n", "")
    tea_page = client.get("/teas/tie-guan-yin").text
    assert "@olena_tea" in tea_page and "No public sessions yet" not in tea_page


def test_visibility_toggle_edit_and_delete(client, db):
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    sid = session_id_from(new_session(client, token))
    assert client.post(f"/sessions/{sid}/visibility", data={"csrf_token": token, "visibility": "public"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(TeaSession, uuid.UUID(sid)).is_public

    edit_page = client.get(f"/sessions/{sid}/edit").text
    assert 'value="Tie Guan Yin"' in edit_page and 'value="15"' in edit_page
    response = client.post(f"/sessions/{sid}/edit", data={"csrf_token": token, "tea_name": "Tie Guan Yin (autumn)", "grams": "6.5",
                                                           "rating": "5", "visibility": "public", "brewed_at": "2026-11-04",
                                                           "steep_seconds": ["20", "20"], "steep_note": ["", ""]}, follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    sess = db.get(TeaSession, uuid.UUID(sid))
    assert sess.tea_name == "Tie Guan Yin (autumn)" and sess.grams == 6.5 and sess.rating == 5 and len(sess.steeps) == 2

    assert client.post(f"/sessions/{sid}/delete", data={"csrf_token": token}, follow_redirects=False).headers["location"] == "/diary"
    assert client.get(f"/sessions/{sid}").status_code == 404


def test_only_the_owner_can_edit_or_delete(client):
    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client), visibility="public"))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    assert client.get(f"/sessions/{sid}/edit").status_code == 403
    assert client.post(f"/sessions/{sid}/delete", data={"csrf_token": token}, follow_redirects=False).status_code == 403
    assert client.get(f"/sessions/{sid}").status_code == 200


def test_validation_errors(client):
    telegram_login(client)
    token = csrf_token(client)
    response = new_session(client, token, tea_name="", grams="900", temp_c="abc", brewed_at="2099-01-01", steep_seconds=["0"])
    assert response.status_code == 400
    text = response.text
    assert "Which tea was it?" in text and "Leaf must be between" in text and "Temperature must be a number" in text
    assert "That date is in the future" in text and "between 1 second and an hour" in text


def test_comments_on_public_sessions(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    owner_token = csrf_token(client)
    sid = session_id_from(new_session(client, owner_token, visibility="public"))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    assert client.post(f"/sessions/{sid}/comments", data={"csrf_token": token, "body": "Brewed this too, lovely."}, follow_redirects=False).status_code == 303
    page = client.get(f"/sessions/{sid}").text
    assert "Brewed this too, lovely." in page and "Comments · 1" in page and "@mike" in page
    comment = db.execute(select(SessionComment)).scalar_one()

    client.cookies.clear()
    telegram_login(client, tg_id="3", username="stranger")
    stranger_token = csrf_token(client)
    assert client.post(f"/sessions/{sid}/comments/{comment.id}/delete", data={"csrf_token": stranger_token}, follow_redirects=False).status_code == 403

    client.cookies.clear()
    telegram_login(client, tg_id="1", username="olena_tea")  # the session owner can delete any comment
    assert client.post(f"/sessions/{sid}/comments/{comment.id}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 303
    assert "Brewed this too, lovely." not in client.get(f"/sessions/{sid}").text


def test_comments_respect_allow_comments(client):
    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client), visibility="public", allow_comments=""))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    client.post(f"/sessions/{sid}/comments", data={"csrf_token": csrf_token(client), "body": "nope"}, follow_redirects=False)
    page = client.get(f"/sessions/{sid}").text
    assert "Comments are off" in page and "nope" not in page


def test_new_session_prefills_from_tea_and_from_session(client, db):
    tea = make_tea(db, "sencha", "Sencha", category="green", country="Japan", region="Shizuoka",
                   brewing={"grams": 5, "water_ml": 150, "temp_c": 75, "steeps": 3, "seconds": [60, 30, 60]})
    telegram_login(client, tg_id="1", username="olena_tea")
    page = client.get("/sessions/new", params={"tea": "sencha"}).text
    assert 'value="Sencha"' in page and 'value="5"' in page and 'value="150"' in page and 'value="75"' in page
    assert 'data-suggested="60,30,60"' in page and f'value="{tea.id}"' in page

    sid = session_id_from(new_session(client, csrf_token(client), visibility="public", steep_seconds=["20", "25"]))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    copy = client.get("/sessions/new", params={"from": sid}).text
    assert 'value="Tie Guan Yin"' in copy and 'data-suggested="20,25"' in copy and 'value="110"' in copy
    assert "rinse first" not in copy  # a copy takes the parameters, not the notes


def test_tea_suggestions(client, db):
    make_tea(db, "tie-guan-yin", "Tie Guan Yin", aliases=["TGY"])
    telegram_login(client)
    assert "Tie Guan Yin" in client.get("/teas/suggest", params={"tea_name": "tgy"}).text
    assert "Not in the base yet" in client.get("/teas/suggest", params={"tea_name": "zzz"}).text
    assert client.get("/teas/suggest", params={"tea_name": "t"}).text.strip() == ""


def jpeg_with_exif(size=(1600, 1200)) -> bytes:
    from io import BytesIO

    from PIL import Image

    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    buffer = BytesIO()
    Image.new("RGB", size, "green").save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def test_photos_are_reencoded_without_metadata_and_can_be_removed(client, db):
    from io import BytesIO
    from pathlib import Path

    from PIL import Image

    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    data = {"csrf_token": token, "tea_name": "Photo Oolong", "visibility": "public", "allow_comments": "on", "brewed_at": "2026-11-05"}
    response = client.post("/sessions/new", data=data, files=[("photos", ("a.jpg", jpeg_with_exif(), "image/jpeg"))], follow_redirects=False)
    sid = session_id_from(response)
    sess = db.get(TeaSession, uuid.UUID(sid))
    photo = sess.photos[0]
    photo_id, photo_url, photo_thumb = photo.id, photo.url, photo.thumb_url
    assert photo_url.endswith("-1200.webp") and photo_thumb.endswith("-400.webp")
    served = client.get(photo_url)
    assert served.status_code == 200
    with Image.open(BytesIO(served.content)) as image:
        assert image.format == "WEBP" and max(image.size) == 1200 and not image.getexif()
    assert b"SecretCamera" not in served.content
    assert photo_thumb in client.get(f"/sessions/{sid}").text

    edit = {"csrf_token": token, "tea_name": "Photo Oolong", "visibility": "public", "brewed_at": "2026-11-05", "remove_photo": str(photo_id)}
    client.post(f"/sessions/{sid}/edit", data=edit, follow_redirects=False)
    db.expire_all()
    assert db.get(TeaSession, uuid.UUID(sid)).photos == []
    assert client.get(photo_url).status_code == 404

    media = Path(client.app.state.settings.media_dir)
    client.post(f"/sessions/{sid}/delete", data={"csrf_token": token}, follow_redirects=False)
    assert not (media / "sessions" / uuid.UUID(sid).hex).exists()


def test_bad_uploads_are_refused(client, db):
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    data = {"csrf_token": token, "tea_name": "Fake", "brewed_at": "2026-11-05"}
    response = client.post("/sessions/new", data=data, files=[("photos", ("evil.jpg", b"<script>alert(1)</script>", "image/jpeg"))], follow_redirects=False)
    assert response.status_code == 303
    sid = session_id_from(response)
    assert db.get(TeaSession, uuid.UUID(sid)).photos == []
    assert "doesn" in client.get(f"/sessions/{sid}").text  # the flash explains it

    from prolyv.uploads import MAX_BYTES, PhotoError, save_session_photo

    try:
        save_session_photo(client.app.state.settings.media_dir, uuid.uuid4(), b"x" * (MAX_BYTES + 1))
        raise AssertionError("expected PhotoError")
    except PhotoError as exc:
        assert "10 MB" in str(exc)


def test_story_card_is_a_1080x1920_png_for_allowed_viewers(client):
    from io import BytesIO

    from PIL import Image

    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    private = session_id_from(new_session(client, token, tea_name="铁观音 Tie Guan Yin Чай", rating="5"))
    response = client.get(f"/sessions/{private}/story.png")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (1080, 1920)
    assert client.get(f"/sessions/{private}/story.png").content == response.content  # cached
    client.cookies.clear()
    assert client.get(f"/sessions/{private}/story.png").status_code == 404


def test_export_contains_my_data_only(client):
    telegram_login(client, tg_id="2", username="mike")
    new_session(client, csrf_token(client), tea_name="Mike Tea", visibility="public")
    client.cookies.clear()
    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    new_session(client, token, tea_name="Export Oolong")
    assert "Download my data" in client.get("/settings").text
    response = client.get("/settings/export")
    assert response.headers["content-type"].startswith("application/json") and "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["profile"]["handle"] == "olena_tea" and [s["tea"] for s in data["sessions"]] == ["Export Oolong"]
    assert data["sessions"][0]["steeps"][0]["seconds"] == 15 and data["sign_in_methods"][0]["provider"] == "telegram"
    assert "hash" not in response.text
