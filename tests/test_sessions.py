import hashlib
from datetime import timedelta

from sqlalchemy import select
from starlette.testclient import TestClient

from prolyv.models import AuthSession, utcnow
from tests.conftest import build_app, csrf_token, google_login, make_settings, set_cookie_names, telegram_login


def session_cookie_header(response) -> str:
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith("prolyv_session="))


def test_session_cookie_attributes(client):
    header = session_cookie_header(telegram_login(client)).lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header
    assert "max-age=2592000" in header  # 30 days
    assert "secure" not in header  # BASE_URL is plain http in tests


def test_session_cookie_is_secure_when_base_url_is_https(tmp_path, google):
    app = build_app(make_settings(tmp_path, base_url="https://prolyv.example"), google)
    with TestClient(app, base_url="https://testserver") as client:
        header = session_cookie_header(telegram_login(client)).lower()
        assert "secure" in header and "httponly" in header


def test_login_authenticated_request_logout(client, db):
    telegram_login(client)
    token = client.cookies["prolyv_session"]
    assert client.get("/settings").status_code == 200

    stored = db.execute(select(AuthSession)).scalar_one()
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert stored.user_agent and "Chrome" in stored.user_agent

    response = client.post("/logout", data={"csrf_token": csrf_token(client)}, follow_redirects=False)
    assert response.status_code == 303
    cleared = session_cookie_header(response).lower()
    assert 'prolyv_session=""' in cleared and "max-age=0" in cleared

    db.expire_all()
    assert db.execute(select(AuthSession)).first() is None
    assert client.get("/settings", follow_redirects=False).status_code == 303
    assert "Signed out" in client.get("/").text


def test_logout_needs_csrf(client, db):
    telegram_login(client)
    assert client.post("/logout", data={}, follow_redirects=False).status_code == 403
    db.expire_all()
    assert db.execute(select(AuthSession)).scalar_one()


def test_login_rotates_the_session(client, db, google):
    google_login(client, google, sub="g-1")
    first = client.cookies["prolyv_session"]
    google_login(client, google, sub="g-1")  # signing in again while logged in
    second = client.cookies["prolyv_session"]
    assert first != second

    db.expire_all()
    assert len(db.execute(select(AuthSession)).scalars().all()) == 1
    client.cookies.clear()
    client.cookies.set("prolyv_session", first)  # the pre-rotation token is dead
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_expired_session_is_rejected_and_removed(client, db):
    telegram_login(client)
    stored = db.execute(select(AuthSession)).scalar_one()
    stored.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert client.get("/settings", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.execute(select(AuthSession)).first() is None


def test_active_session_slides_its_expiry(client, db):
    telegram_login(client)
    stored = db.execute(select(AuthSession)).scalar_one()
    stored.last_seen_at = utcnow() - timedelta(hours=2)
    stored.expires_at = utcnow() + timedelta(days=1)
    db.commit()

    response = client.get("/settings")
    assert response.status_code == 200
    assert "prolyv_session" in set_cookie_names(response)  # cookie re-issued with a fresh 30-day max-age
    db.expire_all()
    stored = db.execute(select(AuthSession)).scalar_one()
    assert stored.expires_at > utcnow() + timedelta(days=29)
    assert stored.last_seen_at > utcnow() - timedelta(minutes=1)


def test_recently_seen_session_is_not_rewritten(client):
    telegram_login(client)
    response = client.get("/settings")
    assert "prolyv_session" not in set_cookie_names(response)


def test_unknown_token_is_ignored(client):
    client.cookies.set("prolyv_session", "definitely-not-a-token")
    assert client.get("/settings", follow_redirects=False).status_code == 303
    assert client.get("/").status_code == 200
