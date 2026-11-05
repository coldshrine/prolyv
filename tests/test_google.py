from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from prolyv.models import Identity, Provider, User
from tests.conftest import GOOGLE_CLIENT_ID, google_login, set_cookie_names, telegram_login


def test_start_redirects_to_google_with_state_and_nonce(client):
    response = client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code == 302
    url = urlparse(response.headers["location"])
    assert url.netloc == "accounts.google.com"
    query = parse_qs(url.query)
    assert query["client_id"] == [GOOGLE_CLIENT_ID]
    assert query["redirect_uri"] == ["http://testserver/auth/google/callback"]
    assert query["scope"] == ["openid email profile"]
    assert len(query["state"][0]) >= 24 and len(query["nonce"][0]) >= 24
    assert "prolyv_oauth" in set_cookie_names(response)


def test_callback_creates_user_with_verified_email(client, db, google):
    response = google_login(client, google, sub="g-1", email="olena@example.com", name="Olena Kovalenko")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert "prolyv_session" in set_cookie_names(response)

    user = db.execute(select(User)).scalar_one()
    assert user.handle == "olena"  # from given_name
    assert user.display_name == "Olena Kovalenko"
    assert user.avatar_url == "https://lh3.example/g-1.jpg"
    identity = db.execute(select(Identity)).scalar_one()
    assert identity.provider == Provider.google
    assert identity.provider_user_id == "g-1"
    assert identity.email == "olena@example.com"
    assert google.token_requests[0]["grant_type"] == "authorization_code"


def test_callback_ignores_unverified_email(client, db, google):
    google_login(client, google, sub="g-2", email="spoof@example.com", email_verified=False)
    identity = db.execute(select(Identity)).scalar_one()
    assert identity.email is None


def test_callback_rejects_wrong_state(client, db, google):
    response = google_login(client, google, state_override="not-the-state")
    assert response.status_code == 400
    assert "stale" in response.text
    assert db.execute(select(User)).first() is None


def test_callback_rejects_wrong_nonce(client, db, google):
    response = google_login(client, google, nonce_override="replayed-nonce")
    assert response.status_code == 400
    assert "could not verify" in response.text
    assert db.execute(select(User)).first() is None


def test_callback_rejects_token_for_another_audience(client, db, google):
    response = google_login(client, google, aud="someone-else")
    assert response.status_code == 400
    assert db.execute(select(User)).first() is None


def test_callback_without_oauth_cookie_is_rejected(client, google):
    response = client.get("/auth/google/callback", params={"code": "x", "state": "y"}, follow_redirects=False)
    assert response.status_code == 400


def test_callback_handles_failed_exchange(client, google):
    start = client.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = client.get("/auth/google/callback", params={"code": "unknown-code", "state": state}, follow_redirects=False)
    assert response.status_code == 400
    assert "Google sign-in failed" in response.text


def test_user_cancelling_at_google_goes_back_to_login(client):
    response = client.get("/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "cancelled" in client.get("/login").text


def test_same_email_different_google_account_joins_existing_user(client, db, google):
    google_login(client, google, sub="g-old", email="same@example.com", name="Olena K.")
    client.cookies.clear()  # a fresh browser, not logged in
    google_login(client, google, sub="g-new", email="same@example.com", name="Olena K.")

    users = db.execute(select(User)).scalars().all()
    assert len(users) == 1
    subs = sorted(i.provider_user_id for i in db.execute(select(Identity)).scalars().all())
    assert subs == ["g-new", "g-old"]


def test_logged_in_user_links_second_provider(client, db, google):
    telegram_login(client, tg_id="42", username="olena_tea")
    response = google_login(client, google, sub="g-1", email="olena@example.com")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"

    user = db.execute(select(User)).scalar_one()
    assert {i.provider for i in user.identities} == {Provider.google, Provider.telegram}
    page = client.get("/settings").text
    assert "Google is now linked" in page
    assert "olena@example.com" in page and "@olena_tea" in page


def test_identity_owned_by_another_account_cannot_be_linked(client, db, google):
    google_login(client, google, sub="g-1", email="first@example.com")  # account A owns g-1
    client.cookies.clear()
    telegram_login(client, tg_id="99", username="second")  # account B, logged in
    response = google_login(client, google, sub="g-1", email="first@example.com")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert "already linked to another Prolyv account" in client.get("/settings").text

    account_b = db.execute(select(User).where(User.handle == "second")).scalar_one()
    assert [i.provider for i in account_b.identities] == [Provider.telegram]
    assert len(db.execute(select(User)).scalars().all()) == 2


def test_next_parameter_is_honoured_for_returning_users(client, google):
    google_login(client, google, sub="g-1")
    client.cookies.clear()
    response = google_login(client, google, sub="g-1", next_url="/discuss")
    assert response.headers["location"] == "/discuss"


def test_next_parameter_rejects_offsite_urls(client, google):
    google_login(client, google, sub="g-1")
    client.cookies.clear()
    response = google_login(client, google, sub="g-1", next_url="https://evil.example/phish")
    assert response.headers["location"] == "/"


def test_google_start_404_when_not_configured(tmp_path, google):
    from starlette.testclient import TestClient

    from tests.conftest import build_app, make_settings

    app = build_app(make_settings(tmp_path, google_client_id="", google_client_secret=""), google)
    with TestClient(app) as client:
        assert client.get("/auth/google/start", follow_redirects=False).status_code == 404
        assert "/auth/google/start" not in client.get("/login").text
