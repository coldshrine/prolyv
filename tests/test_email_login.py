import re
from datetime import timedelta

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from prolyv.config import Settings, configured
from prolyv.models import Identity, LoginToken, Provider, User, utcnow
from tests.conftest import build_app, csrf_token, make_settings, telegram_login


@pytest.fixture
def local_client(tmp_path, google):
    app = build_app(make_settings(tmp_path, base_url="http://localhost:8000"), google)
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client


def form_token(page: str) -> str:
    return re.search(r'class="email-login">\s*<input type="hidden" name="csrf_token" value="([^"]+)"', page).group(1)


def request_link(client, email="olena@example.com", **extra) -> str:
    token = form_token(client.get("/login").text)
    response = client.post("/auth/email/start", data={"email": email, "csrf_token": token, **extra})
    assert response.status_code == 200, response.text[:300]
    return re.search(r'href="(http://localhost:8000/auth/email/verify\?[^"]+)"', response.text).group(1).replace("&amp;", "&")


def follow_link(client, link):
    page = client.get(link)
    assert ">Sign in<" in page.text or "Link this e-mail" in page.text
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    data = {"token": token, "form_token": re.search(r'name="form_token" value="([^"]+)"', page.text).group(1)}
    match = re.search(r'name="next" value="([^"]+)"', page.text)
    if match:
        data["next"] = match.group(1)
    return client.post("/auth/email/verify", data=data, follow_redirects=False), token


def test_placeholders_count_as_not_configured():
    for value in ("", "  ", "local-placeholder.apps.googleusercontent.com", "change-me", None):
        assert not configured(value)
    assert configured("123.apps.googleusercontent.com")
    s = Settings(_env_file=None, secret_key="x" * 40, telegram_bot_token="123456:LOCAL-PLACEHOLDER-TOKEN", telegram_bot_username="x")
    assert not s.telegram_enabled and not s.google_enabled


def test_login_page_on_localhost_sends_nobody_to_a_dead_end(tmp_path):
    app = build_app(make_settings(tmp_path, base_url="http://localhost:8000", google_client_id="", google_client_secret=""))
    with TestClient(app, base_url="http://localhost:8000") as client:
        page = client.get("/login").text
        assert "telegram.org" not in page and "/auth/google/start" not in page
        assert "needs a public domain" in page and "Get a sign-in link by email" in page
        assert client.get("/auth/google/start", follow_redirects=False).status_code == 404


def test_email_hidden_in_production_without_smtp(client):
    assert "Get a sign-in link by email" not in client.get("/login").text
    assert client.post("/auth/email/start", data={"email": "a@b.co"}).status_code == 404


def test_magic_link_creates_account_and_is_single_use(local_client):
    client = local_client
    link = request_link(client)
    db = client.app.state.session_factory()
    row = db.execute(select(LoginToken)).scalar_one()
    assert re.search(r"token=([^&]+)", link).group(1) != row.token_hash  # only the hash is stored
    assert row.expires_at <= utcnow() + timedelta(minutes=15)

    response, token = follow_link(client, link)
    assert response.status_code == 303 and response.headers["location"] == "/settings"
    user = db.execute(select(User)).scalar_one()
    assert user.handle == "olena" and user.identities[0].provider == Provider.email
    assert "olena@example.com" in client.get("/settings").text

    client.cookies.clear()
    assert client.post("/auth/email/verify", data={"token": token}, follow_redirects=False).status_code == 403
    assert "expired" in client.get(link).text


def test_fetching_the_link_does_not_burn_it(local_client):
    link = request_link(local_client)
    local_client.get(link)
    local_client.get(link)
    assert follow_link(local_client, link)[0].status_code == 303


def test_expired_link_is_refused(local_client):
    link = request_link(local_client)
    db = local_client.app.state.session_factory()
    db.execute(select(LoginToken)).scalar_one().expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert "expired" in local_client.get(link).text


def test_csrf_and_address_validation(local_client):
    assert local_client.post("/auth/email/start", data={"email": "a@b.co", "csrf_token": "nope"}).status_code == 403
    token = form_token(local_client.get("/login").text)
    response = local_client.post("/auth/email/start", data={"email": "not-an-email", "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert "look like an e-mail" in local_client.get("/login").text


def test_link_requests_are_limited_per_address(local_client):
    for _ in range(5):
        request_link(local_client)
    token = form_token(local_client.get("/login").text)
    page = local_client.post("/auth/email/start", data={"email": "olena@example.com", "csrf_token": token}).text
    assert "auth/email/verify" not in page and "Check your e-mail" in page


def test_second_sign_in_reuses_the_account_and_next_is_honoured(local_client):
    follow_link(local_client, request_link(local_client))
    local_client.cookies.clear()
    response, _ = follow_link(local_client, request_link(local_client, next="/teas"))
    assert response.headers["location"] == "/teas"
    db = local_client.app.state.session_factory()
    assert len(db.execute(select(User)).scalars().all()) == 1


def test_email_matches_existing_google_identity(local_client, google):
    from tests.conftest import google_login

    google_login(local_client, google, sub="g-1", email="same@example.com")
    local_client.cookies.clear()
    follow_link(local_client, request_link(local_client, email="same@example.com"))
    db = local_client.app.state.session_factory()
    user = db.execute(select(User)).scalar_one()
    assert {i.provider for i in user.identities} == {Provider.google, Provider.email}


def test_link_email_from_settings_needs_the_same_browser(local_client):
    telegram_login(local_client, username="olena_tea")
    token = csrf_token(local_client)
    page = local_client.post("/auth/email/start", data={"email": "o@example.com", "csrf_token": token, "next": "/settings"}).text
    link = re.search(r'href="(http://localhost:8000/auth/email/verify\?[^"]+)"', page).group(1).replace("&amp;", "&")
    cookies = dict(local_client.cookies)
    local_client.cookies.clear()
    response, _ = follow_link(local_client, link)
    assert response.status_code == 400 and "same browser" in response.text
    local_client.cookies.update(cookies)
    response, _ = follow_link(local_client, link)
    assert response.status_code == 303
    db = local_client.app.state.session_factory()
    assert {i.provider for i in db.execute(select(Identity)).scalars()} == {Provider.telegram, Provider.email}


def test_demo_login_only_with_flag_on_localhost(tmp_path, google):
    app = build_app(make_settings(tmp_path, base_url="http://localhost:8000", dev_login=True), google)
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert "Continue as demo user" in client.get("/login").text
        assert client.get("/dev/login", follow_redirects=False).status_code == 303
        assert client.get("/settings").status_code == 200
    with pytest.raises(RuntimeError, match="DEV_LOGIN"):
        build_app(make_settings(tmp_path, base_url="https://prolyv.example", dev_login=True), google)


def test_login_page_says_accounts_are_not_open_when_no_method_is_set_up(tmp_path, google):
    settings = make_settings(tmp_path, base_url="https://prolyv.example", google_client_id="", google_client_secret="", telegram_bot_token="", telegram_bot_username="")
    with TestClient(build_app(settings, google), base_url="https://prolyv.example") as client:
        assert "Accounts open very soon" in client.get("/login").text


def test_login_page_has_no_such_notice_once_a_method_works(tmp_path, google):
    settings = make_settings(tmp_path, base_url="https://prolyv.example", google_client_id="", google_client_secret="")
    with TestClient(build_app(settings, google), base_url="https://prolyv.example") as client:
        assert "Accounts open very soon" not in client.get("/login").text
