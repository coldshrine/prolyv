"""Shared fixtures: an app on a temporary SQLite file, a test client, Telegram/Google login helpers."""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from starlette.testclient import TestClient

from prolyv.app import create_app
from prolyv.auth.telegram import sign_telegram_params
from prolyv.config import Settings
from prolyv.forum_search import ensure_forum_sections
from prolyv.models import Base

BOT_TOKEN = "123456:TEST-BOT-TOKEN-0123456789"
GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
INSTAGRAM_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 Instagram 335.0.0.33.95 (iPhone15,2; iOS 17_5; en_US; scale=3.00; 1179x2556)"
)
TIKTOK_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 musical_ly_33.4.0 JsSdk/2.0 NetType/WIFI Channel/App Store ByteLocale/en Region/UA "
    "WKWebView/1 BytedanceWebview/d8a21c6"
)


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "secret_key": "test-secret-key-test-secret-key-test-secret-key-0000",
        "database_url": f"sqlite:///{tmp_path}/test.db",
        "media_dir": f"{tmp_path}/media",
        "base_url": "http://testserver",
        "telegram_bot_token": BOT_TOKEN,
        "telegram_bot_username": "prolyv_test_bot",
        "google_client_id": GOOGLE_CLIENT_ID,
        "google_client_secret": "test-client-secret",
        "auth_rate_limit": 1000,
        "auth_rate_window": 60,
        "post_rate_limit": 1000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class GoogleStub:
    """Google's token and JWKS endpoints behind an httpx MockTransport; mints RS256 ID tokens."""

    def __init__(self, key: RSAKey, client_id: str) -> None:
        self.key = key
        self.client_id = client_id
        self.codes: dict[str, dict] = {}
        self.token_requests: list[dict] = []
        self.transport = httpx.MockTransport(self.handle)

    def register_code(self, code: str, **claims) -> None:
        self.codes[code] = claims

    def mint(self, claims: dict) -> str:
        now = int(time.time())
        payload = {"iss": "https://accounts.google.com", "aud": self.client_id, "iat": now, "exp": now + 3600}
        payload.update(claims)
        return jwt.encode({"alg": "RS256", "kid": "test-key"}, payload, self.key)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            claims = self.codes.pop(form.get("code", ""), None)
            if claims is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = {"access_token": "ya29.test", "id_token": self.mint(claims), "token_type": "Bearer", "expires_in": 3599}
            return httpx.Response(200, json=token)
        if request.url.host == "www.googleapis.com" and request.url.path == "/oauth2/v3/certs":
            return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        return httpx.Response(404, json={"error": "unexpected request"})


@pytest.fixture(scope="session")
def rsa_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "test-key"})


@pytest.fixture
def google(rsa_key) -> GoogleStub:
    return GoogleStub(rsa_key, GOOGLE_CLIENT_ID)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


def build_app(settings: Settings, google: GoogleStub | None = None):
    app = create_app(settings, google_transport=google.transport if google else None)
    Base.metadata.create_all(app.state.engine)
    ensure_forum_sections(app.state.engine)
    return app


@pytest.fixture
def app(settings, google):
    app = build_app(settings, google)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, headers={"user-agent": DESKTOP_UA})


@pytest.fixture
def db(app):
    session = app.state.session_factory()
    yield session
    session.close()


def telegram_params(tg_id="42", first_name="Olena", last_name="K.", username="olena_tea", photo_url=None, auth_date=None):
    raw = {
        "id": str(tg_id),
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "photo_url": photo_url,
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
    }
    return sign_telegram_params(raw, BOT_TOKEN)


def telegram_login(client: TestClient, **kwargs):
    """Visit /login (sets the intent cookie) and hit the Telegram callback with validly signed params."""
    client.get("/login")
    return client.get("/auth/telegram/callback", params=telegram_params(**kwargs), follow_redirects=False)


def google_login(client: TestClient, google: GoogleStub, sub="google-sub-1", email="olena@example.com", name="Olena K.",
                 email_verified=True, state_override=None, nonce_override=None, next_url=None, **extra):
    """Full code flow: /auth/google/start -> parse state+nonce -> callback with a minted ID token."""
    params = {"next": next_url} if next_url else {}
    start = client.get("/auth/google/start", params=params, follow_redirects=False)
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    state, nonce = query["state"][0], query["nonce"][0]
    code = f"code-{sub}-{state[:6]}"
    claims = {"sub": sub, "name": name, "given_name": name.split()[0], "picture": f"https://lh3.example/{sub}.jpg",
              "nonce": nonce_override or nonce}
    if email is not None:
        claims.update({"email": email, "email_verified": email_verified})
    claims.update(extra)
    google.register_code(code, **claims)
    return client.get("/auth/google/callback", params={"code": code, "state": state_override or state}, follow_redirects=False)


def csrf_token(client: TestClient) -> str:
    response = client.get("/settings")
    assert response.status_code == 200, response.status_code
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "no csrf token on /settings"
    return match.group(1)


def set_cookie_names(response) -> list[str]:
    return [header.split("=", 1)[0] for header in response.headers.get_list("set-cookie")]
