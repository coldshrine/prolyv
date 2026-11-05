"""What the QA pass found: account takeover, leaks of private sessions, admin by name, memory and local-only helpers."""

import uuid
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from prolyv.auth.accounts import is_admin_user
from prolyv.models import Identity, Provider, TeaSession, Topic, User
from tests.conftest import DESKTOP_UA, build_app, csrf_token, make_settings, telegram_login, telegram_params, telegram_post


def fresh(app) -> TestClient:
    return TestClient(app, headers={"user-agent": DESKTOP_UA})


def test_callback_refuses_a_get_and_a_post_without_this_attempts_token(app, client, db):
    payload = telegram_params(tg_id="666", username="attacker")
    client.get("/login")  # the intent cookie alone is what a cross-site attack can count on
    assert client.get("/auth/telegram/callback", params=payload, follow_redirects=False).status_code == 405
    refused = client.post("/auth/telegram/callback", data=payload, follow_redirects=False)
    assert refused.status_code == 400 and "start from the login page" in refused.text
    refused = client.post("/auth/telegram/callback", data={**payload, "state": "guess"}, follow_redirects=False)
    assert refused.status_code == 400
    assert db.execute(select(Identity)).first() is None
    assert telegram_post(client, payload).status_code == 303  # the real flow still works


def test_magic_link_post_needs_the_token_from_its_own_page(tmp_path, google):
    app = build_app(make_settings(tmp_path, base_url="http://localhost:8000"), google)
    client = TestClient(app, base_url="http://localhost:8000", headers={"user-agent": DESKTOP_UA})
    from prolyv.auth.email_links import create_login_token

    db = app.state.session_factory()
    token = create_login_token(db, "attacker@example.com", None)
    db.commit()
    db.close()
    cross_site = client.post("/auth/email/verify", data={"token": token}, follow_redirects=False)
    assert cross_site.status_code == 403 and "prolyv_session" not in cross_site.headers.get("set-cookie", "")
    page = client.get("/auth/email/verify", params={"token": token}).text
    form_token = page.split('name="form_token" value="')[1].split('"')[0]
    assert client.post("/auth/email/verify", data={"token": token, "form_token": form_token}, follow_redirects=False).status_code == 303


def public_session_with_topic(client, db):
    token = csrf_token(client)
    data = {"csrf_token": token, "tea_name": "Secret Oolong", "note": "My private note", "visibility": "public", "brewed_at": "2026-11-05"}
    location = client.post("/sessions/new", data=data, follow_redirects=False).headers["location"]
    session_id = location.rsplit("/", 1)[1]
    section = client.get("/discussions/new").text.split('name="section"')[1].split('<option value="')[2].split('"')[0]
    client.post("/discussions/new", data={"csrf_token": token, "section": section, "title": "With a session", "body": "Look at this one, it brewed in a very surprising way today.",
                                          "attached_session_id": session_id}, follow_redirects=False)
    return session_id, db.execute(select(Topic)).scalars().first().id, token


def test_attached_session_disappears_when_private_or_hidden(app, client, db):
    telegram_login(client, username="olena_tea")
    session_id, topic_id, token = public_session_with_topic(client, db)
    visitor = fresh(app)
    assert "Secret Oolong" in visitor.get(f"/discussions/{topic_id}").text
    assert "f-tag-session" in visitor.get("/discussions").text

    client.post(f"/sessions/{session_id}/visibility", data={"csrf_token": token, "visibility": "private"}, follow_redirects=False)
    page = visitor.get(f"/discussions/{topic_id}").text
    assert "My private note" not in page and "Secret Oolong" not in page
    assert "f-tag-session" not in visitor.get("/discussions").text

    client.post(f"/sessions/{session_id}/visibility", data={"csrf_token": token, "visibility": "public"}, follow_redirects=False)
    db.execute(update(TeaSession).where(TeaSession.id == uuid.UUID(session_id)).values(hidden=True))
    db.commit()
    assert "Secret Oolong" not in visitor.get(f"/discussions/{topic_id}").text


def test_admin_follows_a_verified_sign_in_not_the_handle(tmp_path, google):
    settings = make_settings(tmp_path, base_url="https://prolyv.example", admin_handles="olena_tea", admin_emails="owner@example.com", admin_telegram_ids="99")
    app = build_app(settings, google)
    db = app.state.session_factory()
    squatter = User(handle="olena_tea", display_name="Not the owner")
    by_mail = User(handle="anything", display_name="Owner")
    by_telegram = User(handle="other", display_name="Owner on Telegram")
    db.add_all([squatter, by_mail, by_telegram])
    db.add(Identity(user=squatter, provider=Provider.email, provider_user_id="x@example.com", email="x@example.com", raw_profile={}))
    db.add(Identity(user=by_mail, provider=Provider.email, provider_user_id="owner@example.com", email="Owner@Example.com", raw_profile={}))
    db.add(Identity(user=by_telegram, provider=Provider.telegram, provider_user_id="99", raw_profile={}))
    db.commit()
    assert not is_admin_user(db, settings, squatter)
    assert is_admin_user(db, settings, by_mail) and is_admin_user(db, settings, by_telegram)
    db.close()


def test_a_png_bomb_is_refused_and_a_big_phone_jpeg_is_not(tmp_path):
    from prolyv.uploads import PhotoError, save_session_photo

    bomb = BytesIO()
    Image.new("RGB", (8000, 6200), "green").save(bomb, "PNG")
    with pytest.raises(PhotoError):
        save_session_photo(str(tmp_path), uuid.uuid4(), bomb.getvalue())
    phone = BytesIO()
    Image.new("RGB", (6000, 4000), "green").save(phone, "JPEG", quality=30)
    url, thumb = save_session_photo(str(tmp_path), uuid.uuid4(), phone.getvalue())
    with Image.open(tmp_path / url.removeprefix("/media/")) as big:
        assert big.size == (1200, 800)
    with Image.open(tmp_path / thumb.removeprefix("/media/")) as small:
        assert small.size == (400, 267)


def test_photo_orientation_is_applied_without_keeping_metadata(tmp_path):
    from prolyv.uploads import save_session_photo

    exif = Image.Exif()
    exif[0x0112] = 6  # rotate
    exif[0x0110] = "SecretCamera"
    buffer = BytesIO()
    Image.new("RGB", (2000, 1000), "green").save(buffer, "JPEG", exif=exif)
    url, _ = save_session_photo(str(tmp_path), uuid.uuid4(), buffer.getvalue())
    path = tmp_path / url.removeprefix("/media/")
    with Image.open(path) as big:
        assert big.size == (600, 1200) and not big.getexif()
    assert b"SecretCamera" not in path.read_bytes()


def test_story_file_goes_when_the_session_stops_being_public(client, settings, db):
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    location = client.post("/sessions/new", data={"csrf_token": token, "tea_name": "Story Tea", "visibility": "public", "brewed_at": "2026-11-05"}, follow_redirects=False).headers["location"]
    session_id = location.rsplit("/", 1)[1]
    assert client.get(f"/sessions/{session_id}/story.png").status_code == 200
    stories = Path(settings.media_dir) / "stories"
    assert len(list(stories.glob("*.png"))) == 1
    client.post(f"/sessions/{session_id}/visibility", data={"csrf_token": token, "visibility": "private"}, follow_redirects=False)
    assert list(stories.glob("*.png")) == []


def test_local_only_helpers_refuse_a_real_domain_when_base_url_was_forgotten(tmp_path, google):
    app = build_app(make_settings(tmp_path, base_url="http://localhost:8000", dev_login=True), google)
    real_domain = TestClient(app, base_url="https://prolyv.example.com", headers={"user-agent": DESKTOP_UA})
    assert real_domain.get("/dev/login", follow_redirects=False).status_code == 404
    local = TestClient(app, base_url="http://localhost:8000", headers={"user-agent": DESKTOP_UA})
    assert local.get("/dev/login", follow_redirects=False).status_code == 303
    page = real_domain.get("/login").text
    token = page.split('name="csrf_token" value="')[1].split('"')[0] if 'name="csrf_token"' in page else ""
    answer = real_domain.post("/auth/email/start", data={"email": "a@example.com", "csrf_token": token}, follow_redirects=False)
    assert answer.status_code == 404 and "token=" not in answer.text


def test_a_handle_is_no_admin_on_a_real_host_even_if_base_url_was_forgotten(tmp_path, google):
    app = build_app(make_settings(tmp_path, admin_handles="olena_tea"), google)
    local = TestClient(app, base_url="http://localhost:8000", headers={"user-agent": DESKTOP_UA})
    telegram_login(local, username="olena_tea")
    assert local.get("/admin").status_code == 200
    real = TestClient(app, base_url="https://prolyv.example.com", headers={"user-agent": DESKTOP_UA}, cookies=dict(local.cookies))
    assert real.get("/admin").status_code == 403
