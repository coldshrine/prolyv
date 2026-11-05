import time

import pytest
from sqlalchemy import select

from prolyv.auth.telegram import TelegramAuthError, sign_telegram_params, verify_telegram_auth
from prolyv.models import AuthSession, Identity, Provider, User
from tests.conftest import BOT_TOKEN, set_cookie_names, telegram_login, telegram_params


def test_valid_signature_is_accepted():
    params = telegram_params(tg_id="7", username="olena_tea", photo_url="https://t.me/i/userpic/320/x.jpg")
    user = verify_telegram_auth(params, BOT_TOKEN)
    assert user.id == "7"
    assert user.username == "olena_tea"
    assert user.display_name == "Olena K."
    assert user.photo_url.endswith("x.jpg")


def test_tampered_payload_is_rejected():
    params = telegram_params(username="olena_tea")
    params["username"] = "admin"
    with pytest.raises(TelegramAuthError) as exc:
        verify_telegram_auth(params, BOT_TOKEN)
    assert exc.value.code == "bad_hash"


def test_wrong_bot_token_is_rejected():
    params = telegram_params()
    with pytest.raises(TelegramAuthError) as exc:
        verify_telegram_auth(params, "999:OTHER-TOKEN")
    assert exc.value.code == "bad_hash"


def test_stale_auth_date_is_rejected():
    params = telegram_params(auth_date=int(time.time()) - 25 * 3600)
    with pytest.raises(TelegramAuthError) as exc:
        verify_telegram_auth(params, BOT_TOKEN)
    assert exc.value.code == "stale"


def test_auth_date_just_inside_24h_is_accepted():
    params = telegram_params(auth_date=int(time.time()) - 23 * 3600)
    assert verify_telegram_auth(params, BOT_TOKEN).id == "42"


def test_missing_hash_is_rejected():
    params = telegram_params()
    del params["hash"]
    with pytest.raises(TelegramAuthError) as exc:
        verify_telegram_auth(params, BOT_TOKEN)
    assert exc.value.code == "missing"


def test_optional_fields_are_not_required():
    params = sign_telegram_params({"id": "5", "first_name": "Ann", "auth_date": str(int(time.time()))}, BOT_TOKEN)
    user = verify_telegram_auth(params, BOT_TOKEN)
    assert user.username is None and user.last_name is None
    assert user.display_name == "Ann"


def test_callback_creates_user_and_session(client, db):
    response = telegram_login(client, tg_id="42", username="olena_tea", first_name="Olena", last_name="K.")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"  # first login lands on settings to confirm the handle
    assert "prolyv_session" in set_cookie_names(response)

    user = db.execute(select(User)).scalar_one()
    assert user.handle == "olena_tea"
    assert user.display_name == "Olena K."
    identity = db.execute(select(Identity)).scalar_one()
    assert identity.provider == Provider.telegram and identity.provider_user_id == "42"
    assert identity.raw_profile["username"] == "olena_tea"
    assert db.execute(select(AuthSession)).scalar_one().user_id == user.id


def test_callback_accepts_post_form(client, db):
    client.get("/login")
    response = client.post("/auth/telegram/callback", data=telegram_params(tg_id="43"), follow_redirects=False)
    assert response.status_code == 303
    assert db.execute(select(User)).scalar_one().identities[0].provider_user_id == "43"


def test_callback_rejects_tampered_hash(client, db):
    client.get("/login")
    params = telegram_params()
    params["id"] = "1"
    response = client.get("/auth/telegram/callback", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert "could not be verified" in response.text
    assert db.execute(select(User)).first() is None


def test_callback_rejects_stale_auth(client):
    client.get("/login")
    params = telegram_params(auth_date=int(time.time()) - 2 * 86400)
    response = client.get("/auth/telegram/callback", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert "older than 24 hours" in response.text


def test_callback_requires_login_page_intent(client):
    """A signed callback pasted into a fresh browser must not log that browser in (login CSRF)."""
    response = client.get("/auth/telegram/callback", params=telegram_params(), follow_redirects=False)
    assert response.status_code == 400
    assert "start from the login page" in response.text
    assert "prolyv_session" not in set_cookie_names(response)


def test_second_login_from_a_new_browser_reuses_user(client, db):
    telegram_login(client, tg_id="42")
    client.cookies.clear()  # new browser / cookies lost
    response = telegram_login(client, tg_id="42")
    assert response.status_code == 303
    assert response.headers["location"] == "/"  # returning users skip the welcome step
    assert db.execute(select(User)).scalar_one()
    assert len(db.execute(select(Identity)).scalars().all()) == 1


def test_callback_refuses_when_already_signed_in_without_intent(client):
    """A logged-in browser only gets a Telegram intent from /settings (link flow), never from /login."""
    telegram_login(client, tg_id="42")
    response = client.get("/auth/telegram/callback", params=telegram_params(tg_id="42"), follow_redirects=False)
    assert response.status_code == 400


def test_callback_404_when_telegram_not_configured(tmp_path, google):
    from starlette.testclient import TestClient

    from tests.conftest import build_app, make_settings

    app = build_app(make_settings(tmp_path, telegram_bot_token="", telegram_bot_username=""), google)
    with TestClient(app) as client:
        assert client.get("/auth/telegram/callback", params=telegram_params()).status_code == 404
        assert "telegram.org" not in client.get("/login").text
