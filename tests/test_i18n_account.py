"""Sign-in, settings, profiles, search, error pages and the about / rules / privacy / terms pages in Russian."""

from __future__ import annotations

import re
from datetime import datetime

from prolyv.models import User
from tests.conftest import csrf_token, telegram_login


def russian(client) -> None:
    client.cookies.set("lang", "ru")


def test_login_page(client):
    russian(client)
    page = client.get("/login").text
    assert "<title>Войти · " in page and "Каждый пролив в вашем дневнике" in page
    assert "Войти через Google" in page and "Войти через Telegram" in page and "Политика конфиденциальности" in page
    assert '<a href="/rules">Правила сообщества</a>' in page
    for english in ("Continue with Google", "No passwords", "Read the feed without an account", "By continuing"):
        assert english not in page


def test_static_pages(client):
    russian(client)
    for path, title, english in (
        ("/about", "Дом для каждого пролива", "A home for every steep"),
        ("/rules", "Будьте вежливы", "Be decent"),
        ("/privacy", "Что мы храним", "What we store"),
        ("/terms", "Условия использования", "Terms of use"),
    ):
        page = client.get(path).text
        assert title in page and english not in page, path
    privacy = client.get("/privacy").text
    assert "выбранный вами язык интерфейса" in privacy and "<strong>Данные для входа.</strong>" in privacy
    assert '<a href="/settings">Настройки</a>' in privacy and "«Удалённый аккаунт»" in privacy


def test_privacy_names_the_language_cookie_in_english_too(client):
    assert "the interface language you pick" in client.get("/privacy").text


def test_error_pages(client):
    russian(client)
    missing = client.get("/no-such-page/at-all")
    assert missing.status_code == 404 and "Здесь ничего не заваривается" in missing.text and "Ошибка 404" in missing.text
    assert "Nothing steeping here" not in missing.text
    telegram_login(client)
    expired = client.post("/settings", data={"csrf_token": "wrong", "display_name": "X", "handle": "x_x_x"})
    assert expired.status_code == 403 and "Форма устарела" in expired.text


def test_htmx_login_prompt(client):
    russian(client)
    response = client.post("/settings", headers={"hx-request": "true"}, follow_redirects=False)
    assert response.status_code == 401 and response.text == "Пожалуйста, войдите."


def test_settings_page_and_messages(client):
    telegram_login(client)
    russian(client)
    page = client.get("/settings").text
    for text in ("Привязанные аккаунты", "Язык интерфейса", "Удалить мой аккаунт", "Сохранить профиль", "Ваш профиль: /u/olena_tea"):
        assert text in page, text
    assert 'href="/lang/en?next=/settings%23language"' in page and "Switch to English" in page
    assert "Linked accounts" not in page and "Delete my account" not in page
    token = csrf_token(client)
    bad = client.post("/settings", data={"csrf_token": token, "display_name": "", "handle": "a", "bio": ""})
    assert bad.status_code == 400 and "Имя должно быть от 1 до 50 символов." in bad.text and "3–24 символа" in bad.text
    saved = client.post("/settings", data={"csrf_token": token, "display_name": "Olena", "handle": "olena_tea", "bio": ""})
    assert "Профиль сохранён." in saved.text
    assert "Нужен хотя бы один способ входа" in client.get("/settings?error=providers").text
    assert "@nobody_here свободен" in client.get("/settings/handle-check", params={"handle": "nobody_here"}).text


def test_settings_language_switch_goes_back_to_the_card(client):
    telegram_login(client)
    response = client.get("/lang/ru", params={"next": "/settings#language"}, follow_redirects=False)
    assert response.headers["location"] == "/settings#language"
    page = client.get("/settings").text
    assert 'href="/lang/en?next=/settings%23language"' in page and "Выбран" in page


def test_sign_in_flash_in_russian(client):
    russian(client)
    welcome = telegram_login(client)
    assert welcome.status_code == 303
    assert "Добро пожаловать на" in client.get(welcome.headers["location"]).text
    assert "Вы вышли." in client.post("/logout", data={"csrf_token": csrf_token(client)}).text


def test_profile_page(client, db):
    telegram_login(client)
    user = db.query(User).filter_by(handle="olena_tea").one()
    user.created_at = datetime(2026, 5, 3)
    db.commit()
    russian(client)
    page = client.get("/u/olena_tea").text
    assert "Публичный дневник · с мая 2026" in page and "0 подписок" in page
    assert "публичных сессий" in page and "Редактировать профиль" in page and "Публичных сессий пока нет" in page
    assert "Темы и ответы" in page and "Чайный профиль" in page
    for english in ("Public diary", "Edit profile", "public sessions", "No public sessions yet"):
        assert english not in page, english
    shelf = client.get("/u/olena_tea", params={"tab": "shelf"}).text
    assert "Полка пуста" in shelf


def test_search_page(client):
    russian(client)
    page = client.get("/search", params={"q": "zzzzqqq"}).text
    assert "По запросу «zzzzqqq» ничего не нашлось" in page and "базу чаёв от А до Я" in page
    assert "Чаи, темы, посты, сессии" in page and "Nothing matches" not in page


def test_sign_in_email_in_the_page_language(tmp_path, google):
    from starlette.testclient import TestClient

    from tests.conftest import build_app, make_settings

    app = build_app(make_settings(tmp_path, base_url="http://localhost:8000"), google)
    with TestClient(app, base_url="http://localhost:8000", cookies={"lang": "ru"}) as local:
        page = local.get("/login").text
        assert "Получить ссылку для входа на e-mail" in page
        token = re.search(r'class="email-login">\s*<input type="hidden" name="csrf_token" value="([^"]+)"', page).group(1)
        sent = local.post("/auth/email/start", data={"email": "olena@example.com", "csrf_token": token}).text
        assert "Ваша ссылка для входа" in sent and "действует 15 минут" in sent
