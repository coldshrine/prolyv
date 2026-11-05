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
    assert "Tie Guan Yin" in page and "Private · only you can see this" in page
    assert "7 g" in page and "110 ml" in page and "95 °C" in page and "Gaiwan" in page
    assert "15 s" in page and "rinse first" in page and "★★★★☆" in page
    assert "Session saved" in page and "Make public" in page and "Brew it the same way" in page
    assert "Keep steep six short." in page

    diary = client.get("/diary").text
    assert "Tie Guan Yin" in diary and "Your last 30 days in cups" in diary and "Five weeks in cups" in diary and "Shelf" in diary
    assert "Your tea profile" in diary and "From all 1 session" in diary


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
    assert "public session" in page and "Share" in page and "Tea card" in page
    assert 'property="og:title" content="Tie Guan Yin · Olena K.' in page
    assert "Tie Guan Yin" in client.get("/u/olena_tea").text
    assert "1</p><p class=\"stat-label\">session logged" in client.get("/u/olena_tea").text.replace("\n", "")
    tea_page = client.get("/teas/tie-guan-yin").text
    assert "/u/olena_tea" in tea_page and "No public sessions yet" not in tea_page


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
    assert "Brewed this too, lovely." in page and "1 comment" in page and "@mike" in page
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


def test_private_session_photos_are_only_for_the_author_and_admins(client, db, app):
    from fastapi.testclient import TestClient
    from sqlalchemy import update

    from tests.conftest import DESKTOP_UA

    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    data = {"csrf_token": token, "tea_name": "Secret Oolong", "visibility": "private", "brewed_at": "2026-11-05"}
    response = client.post("/sessions/new", data=data, files=[("photos", ("a.jpg", jpeg_with_exif(), "image/jpeg"))], follow_redirects=False)
    sid = session_id_from(response)
    photo = db.get(TeaSession, uuid.UUID(sid)).photos[0]
    urls = [photo.url, photo.thumb_url]

    def fresh():
        return TestClient(app, headers={"user-agent": DESKTOP_UA})

    stranger = fresh()
    telegram_login(stranger, tg_id="2", username="stranger")
    anonymous = fresh()
    admin = fresh()
    telegram_login(admin, tg_id="3", username="boss")
    app.state.settings.admin_telegram_ids = "3"

    for url in urls:
        owner_view = client.get(url)
        assert owner_view.status_code == 200 and owner_view.headers["content-type"] == "image/webp"
        assert owner_view.headers["cache-control"] == "private, no-store"
        assert stranger.get(url).status_code == 404
        assert anonymous.get(url).status_code == 404
        assert admin.get(url).status_code == 200

    # the path cannot be reached around the route
    tail = urls[0].removeprefix("/media/")
    for sneaky in (f"/media/{tail}", f"/media/teas/../{tail}", f"/media/teas/%2e%2e/{tail}", f"/media/teas/..%2f{tail}", f"//media/{tail}"):
        assert anonymous.get(sneaky).status_code == 404, sneaky
    assert anonymous.get(urls[0].rsplit("/", 1)[0] + "/../../../prolyv.db").status_code == 404

    # public shows it to everyone, hiding by a moderator takes it away again
    client.post(f"/sessions/{sid}/visibility", data={"csrf_token": token, "visibility": "public"}, follow_redirects=False)
    assert anonymous.get(urls[0]).status_code == 200 and stranger.get(urls[1]).status_code == 200
    db.execute(update(TeaSession).where(TeaSession.id == uuid.UUID(sid)).values(hidden=True))
    db.commit()
    assert anonymous.get(urls[0]).status_code == 404
    assert client.get(urls[0]).status_code == 200


def test_story_files_are_not_served_from_media(client, tmp_path, settings):
    from pathlib import Path

    folder = Path(settings.media_dir) / "stories"
    folder.mkdir(parents=True)
    (folder / "abc-1.png").write_bytes(b"x")
    assert client.get("/media/stories/abc-1.png").status_code == 404


def test_diary_reaches_every_session_page_by_page(client, db):
    from datetime import datetime, timedelta

    from prolyv.models import User

    telegram_login(client, username="olena_tea")
    user = db.execute(select(User).where(User.handle == "olena_tea")).scalar_one()
    start = datetime(2024, 1, 1, 12)
    for n in range(65):
        db.add(TeaSession(user_id=user.id, tea_name=f"Tea{n:02}x", brewed_at=start + timedelta(days=n // 40)))
    db.commit()

    seen = []
    url = "/diary"
    for _ in range(5):
        page = client.get(url).text
        seen += re.findall(r"Tea\d\dx", page.split("dy-side")[0])
        found = re.search(r'href="(/diary\?page=\d+)"', page)
        if not found:
            break
        url = found.group(1)
    assert len(seen) == 65 and len(set(seen)) == 65
    assert url == "/diary?page=3"
    assert client.get("/diary?page=2").text.count("Show more") == 1


def test_session_form_keeps_what_was_typed_after_an_error(client):
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    empty = client.post("/sessions/new", data={"csrf_token": token, "tea_name": "", "brewed_at": "2026-01-01"})
    assert empty.status_code in (200, 400) and 'value="None"' not in empty.text
    bad = client.post("/sessions/new", data={"csrf_token": token, "tea_name": "X", "grams": "abc", "water_ml": "inf", "steep_seconds": ["9999", "1e400"], "brewed_at": "2026-01-01"})
    assert bad.status_code == 400 and 'value="abc"' in bad.text and 'value="None"' not in bad.text
    assert "must be a number" in bad.text


def test_editing_a_brew_along_session_keeps_it_on_the_post(client, db):
    from prolyv.models import Post, User

    telegram_login(client, username="olena_tea")
    user = db.execute(select(User)).scalars().first()
    post = Post(slug="p", title="P", author_id=user.id, published_at=__import__("prolyv.models").models.utcnow())
    db.add(post)
    db.commit()
    token = csrf_token(client)
    sid = session_id_from(new_session(client, token, post_id=str(post.id), visibility="public"))
    client.post(f"/sessions/{sid}/edit", data={"csrf_token": token, "tea_name": "Edited", "visibility": "public", "brewed_at": "2026-01-01",
                                                "post_id": re.search(r'name="post_id" value="([^"]*)"', client.get(f"/sessions/{sid}/edit").text).group(1)}, follow_redirects=False)
    db.expire_all()
    assert db.get(TeaSession, uuid.UUID(sid)).post_id == post.id
    client.post(f"/sessions/{sid}/edit", data={"csrf_token": token, "tea_name": "Edited", "visibility": "public", "brewed_at": "2026-01-01"}, follow_redirects=False)
    db.expire_all()
    assert db.get(TeaSession, uuid.UUID(sid)).post_id == post.id


def test_diary_stats_share_one_window_and_filters_use_real_types(client, db):
    from datetime import timedelta

    from prolyv.models import User, utcnow

    green = make_tea(db, "sencha", "Sencha", category="green")
    shu = make_tea(db, "gong-ting", "Gong Ting", category="shu")
    telegram_login(client, username="olena_tea")
    user = db.execute(select(User).where(User.handle == "olena_tea")).scalar_one()
    now = utcnow()
    db.add_all([
        TeaSession(user_id=user.id, tea=green, tea_name="Sencha", rating=4, taste_tags=["grassy"], brewed_at=now - timedelta(days=2)),
        TeaSession(user_id=user.id, tea=green, tea_name="Sencha", rating=2, taste_tags=["grassy", "umami"], brewed_at=now - timedelta(days=3)),
        TeaSession(user_id=user.id, tea_name="Grandma's blend", brewed_at=now - timedelta(days=4)),
        TeaSession(user_id=user.id, tea=shu, tea_name="Gong Ting", rating=5, brewed_at=now - timedelta(days=45)),  # outside the 30 days
    ])
    db.commit()

    page = client.get("/diary").text
    stat = lambda label: re.search(rf'dy-stat-label">{label}</p><p class="dy-stat-value[^"]*">([^<]+)</p><p class="dy-stat-hint">([^<]+)<', page).groups()  # noqa: E731
    assert stat("Sessions") == ("3", "2 more than the 30 days before")
    assert stat("Favourite type") == ("Green", "2 of 3 sessions")
    assert stat("Average rating") == ("3.0", "out of 5")
    assert stat("New teas")[0] == "2"  # Sencha and the blend; Gong Ting was first brewed earlier
    assert "Your favourite type is <b>green</b>" in page and "/diary/month.png" in page
    assert "Five weeks in cups" in page and "<span>Mon</span>" in page and f'/diary?day={(now - timedelta(days=2)).date().isoformat()}' in page
    assert "Grassy · 2" in page and "From all 4 sessions" in page
    assert 'href="/teas/sencha"' in page and "YOUR TEA YEAR" in page
    assert 'href="/diary?type=green#sessions"' in page and 'href="/diary?type=shu#sessions"' in page and 'href="/diary?type=other#sessions"' in page
    assert "?type=oolong" not in page

    main = lambda url: client.get(url).text.split("dy-side")[0]  # noqa: E731
    assert "Grandma" not in main("/diary?type=green") and "Sencha" in main("/diary?type=green")
    assert "Grandma" in main("/diary?type=other") and "Sencha" not in main("/diary?type=other").split('id="sessions"')[1]
    assert "No sessions of this type yet." in client.get("/diary?type=white").text
    day = (now - timedelta(days=4)).date().isoformat()
    assert "Grandma" in main(f"/diary?day={day}").split('id="sessions"')[1] and "Sencha" not in main(f"/diary?day={day}").split('id="sessions"')[1]
    assert client.get("/diary?page=abc").status_code == 200 and client.get("/diary?day=nonsense&type=nonsense").status_code == 200

    response = client.get("/diary/month.png")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png" and "no-store" in response.headers["cache-control"]


def test_month_story_needs_recent_sessions_and_login(client):
    assert client.get("/diary/month.png", follow_redirects=False).status_code == 303
    telegram_login(client, username="olena_tea")
    assert client.get("/diary/month.png").status_code == 404


def test_visibility_checkbox_posts_public_and_private(client, db):
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    both = session_id_from(new_session(client, token, visibility=["private", "public"]))
    only_hidden = session_id_from(new_session(client, token, visibility=["private"]))
    assert db.get(TeaSession, uuid.UUID(both)).is_public
    assert not db.get(TeaSession, uuid.UUID(only_hidden)).is_public
    form = client.get("/sessions/new").text
    assert 'name="visibility" value="private"' in form and 'id="share-feed"' in form and 'id="share-feed" checked' not in form
    assert "Your draft saves automatically." in form and "HOW OTHERS WILL SEE IT" in form and 'aria-label="More leaf"' in form


def test_session_page_blocks_follow_reply_and_helpful(client, db):
    from prolyv.models import Follow, HelpfulVote, Notification, User

    tea = make_tea(db, "sencha", "Sencha", category="green")
    telegram_login(client, tg_id="1", username="olena_tea")
    mine = session_id_from(new_session(client, csrf_token(client), tea_id=str(tea.id), visibility="public", steep_seconds=["15", "20"], steep_note=["soft", "brisk"]))
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    other = session_id_from(new_session(client, token, tea_id=str(tea.id), visibility="public"))

    page = client.get(f"/sessions/{mine}").text
    assert "GREEN · 2 STEEPS" in page and "Steep by steep" in page and "Steep 2 · 20 s · brisk" in page
    assert 'action="/u/olena_tea/follow"' in page and "Other sessions of this tea" in page
    assert f"/discussions/new?session={mine}" in page and f"/sessions/{mine}/compare" in page and f'href="/sessions/{other}"' in page
    assert "Attach my session" not in page

    assert client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert db.execute(select(Follow)).scalar_one() is not None
    assert ">Following<" in client.get(f"/sessions/{mine}").text

    # a comment line break stays, a pasted link works, the author hears about it once
    response = client.post(f"/sessions/{mine}/comments", data={"csrf_token": token, "body": "Lovely.\nhttps://example.com/a"}, follow_redirects=False)
    assert response.status_code == 303 and "#comment-" in response.headers["location"]
    comment = db.execute(select(SessionComment)).scalar_one()
    assert 'href="https://example.com/a"' in client.get(f"/sessions/{mine}").text

    client.cookies.clear()
    telegram_login(client, tg_id="1", username="olena_tea")
    owner_token = csrf_token(client)
    # a reply prefills the handle and the mention notifies
    assert 'data-reply="mike"' in client.get(f"/sessions/{mine}").text
    assert "@mike </textarea>" in client.get(f"/sessions/{mine}?reply=mike").text
    assert "@&lt;script" not in client.get(f"/sessions/{mine}?reply=<script>").text and "<script>@" not in client.get(f"/sessions/{mine}?reply=<script>").text
    client.post(f"/sessions/{mine}/comments", data={"csrf_token": owner_token, "body": "@mike filtered water"}, follow_redirects=False)
    mike = db.execute(select(User).where(User.handle == "mike")).scalar_one()
    olena = db.execute(select(User).where(User.handle == "olena_tea")).scalar_one()
    assert db.execute(select(Notification).where(Notification.user_id == mike.id, Notification.text.like("%mentioned you%"))).scalars().first() is not None
    assert len(db.execute(select(Notification).where(Notification.user_id == olena.id, Notification.text.like("%commented on your%"))).scalars().all()) == 1

    # Helpful is a toggle, and not for your own comment
    url = f"/sessions/{mine}/comments/{comment.id}/helpful"
    assert client.post(url, data={"csrf_token": owner_token}, follow_redirects=False).status_code == 303
    assert db.execute(select(HelpfulVote).where(HelpfulVote.kind == "session_comment", HelpfulVote.ref_id == comment.id)).scalar_one() is not None
    assert "Helpful · 1" in client.get(f"/sessions/{mine}").text
    client.post(url, data={"csrf_token": owner_token}, follow_redirects=False)
    db.expire_all()
    assert db.execute(select(HelpfulVote)).scalars().first() is None
    client.post(url, data={"csrf_token": owner_token}, follow_redirects=False)
    assert client.post(url, follow_redirects=False).status_code == 403  # no CSRF token

    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    client.post(url, data={"csrf_token": csrf_token(client)}, follow_redirects=False)
    db.expire_all()
    assert len(db.execute(select(HelpfulVote)).scalars().all()) == 1  # own comment: nothing added

    # deleting the comment takes its votes along
    client.post(f"/sessions/{mine}/comments/{comment.id}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False)
    db.expire_all()
    assert db.execute(select(HelpfulVote)).scalars().first() is None


def test_session_comments_come_in_pages(client, db):
    from prolyv.models import User

    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client), visibility="public"))
    user = db.execute(select(User)).scalars().first()
    from datetime import datetime, timedelta

    start = datetime(2026, 1, 1)
    for n in range(25):
        db.add(SessionComment(session_id=uuid.UUID(sid), user_id=user.id, body=f"Note{n:02}x", created_at=start + timedelta(minutes=n)))
    db.commit()
    first = client.get(f"/sessions/{sid}").text
    assert "25 comments" in first and "Note00x" in first and "Note24x" not in first and f"/sessions/{sid}?c=2" in first
    swapped = client.get(f"/sessions/{sid}?c=2", headers={"HX-Request": "true"}).text
    assert "Note24x" in swapped and "Note00x" not in swapped
    assert "Note00x" in client.get(f"/sessions/{sid}?c=2").text
    assert client.get(f"/sessions/{sid}?c=abc").status_code == 200


def test_compare_respects_can_view(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    public = session_id_from(new_session(client, token, visibility="public", tea_name="Tea A", steep_seconds=["15", "20"], steep_note=["", ""]))
    private = session_id_from(new_session(client, token, visibility="private", tea_name="Secret Tea"))
    assert client.get(f"/sessions/{public}/compare?with={private}").status_code == 200
    client.cookies.clear()
    assert client.get(f"/sessions/{public}/compare", follow_redirects=False).status_code == 303

    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    assert "no other session" in client.get(f"/sessions/{public}/compare").text
    mine = session_id_from(new_session(client, token, tea_name="Tea A", steep_seconds=["30"], steep_note=[""]))
    page = client.get(f"/sessions/{public}/compare").text
    assert "Compare sessions" in page and "Tea A" in page and "30 s" in page and "20 s" in page and "Secret Tea" not in page
    assert client.get(f"/sessions/{public}/compare?with={mine}").status_code == 200
    assert client.get(f"/sessions/{public}/compare?with={private}").status_code == 404
    assert client.get(f"/sessions/{private}/compare?with={mine}").status_code == 404
    assert client.get(f"/sessions/{public}/compare?with=bad").status_code == 404


def test_malformed_session_urls_are_404(client):
    telegram_login(client)
    assert client.get("/sessions/notauuid").status_code == 404
    assert client.get("/sessions/notauuid/compare").status_code == 404
    assert client.get("/sessions/notauuid/story.png").status_code == 404


def test_story_text_handles_crlf_long_words_and_paragraphs():
    from PIL import Image, ImageDraw

    from prolyv.story import _clean, _font, _wrap, first_paragraph

    assert first_paragraph("Lilac and cream\r\nin the first.\r\n\r\nSecond part") == "Lilac and cream in the first."
    assert _clean("a\r\nb\n\nc") == "a b c"
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _font("Manrope", 36, 500)
    lines = _wrap(draw, "x" * 400, font, 900, 3)
    assert len(lines) == 3 and all(draw.textlength(line, font=font) <= 900 for line in lines) and lines[-1].endswith("…")
