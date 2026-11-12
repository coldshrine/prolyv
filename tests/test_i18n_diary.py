"""The diary and tea sessions in Russian: the diary, the new-session form, a session page, compare and the story cards."""

from __future__ import annotations

import html
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

from prolyv.i18n import set_lang
from prolyv.models import TeaSession
from tests.conftest import csrf_token, telegram_login
from tests.test_diary import new_session, session_id_from


def russian(client) -> None:
    client.cookies.set("lang", "ru")


def text(response) -> str:
    return html.unescape(response.text)


def test_diary_in_russian(client):
    telegram_login(client, username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client)))
    russian(client)
    page = text(client.get("/diary"))
    assert "Ваши 30 дней в чашках" in page and "Пять недель в чашках" in page and "Ваш чайный профиль" in page
    assert "По 1 сессии в вашем дневнике." in page and "<span>Пн</span>" in page and "Новая сессия" in page
    assert "Your last 30 days" not in page and "Five weeks" not in page and "Shelf" not in page
    assert "ВАШ ЧАЙНЫЙ ГОД" in page and "1 сессия и 1 чай" in page
    day = client.get("/diary", params={"day": "2026-11-05"})
    assert "Сессии за 5 ноября 2026" in text(day) and "5 НОЯ" in text(day)
    assert "Цветочный" in page or "цветочный" in page.lower()
    assert f"/sessions/{sid}" in page


def test_new_session_form_in_russian(client):
    telegram_login(client)
    russian(client)
    page = text(client.get("/sessions/new"))
    assert "Новая сессия" in page and "ТАЙМЕР ПРОЛИВОВ" in page and "ПРОЛИВ 1 · ГОТОВ" in page and "Записать пролив" in page
    assert ">гайвань</option>" in page and 'value="gaiwan"' in page and "Лист промыт перед завариванием" in page
    assert 'aria-label="Меньше листа"' in page and "<span>Цветочный</span>" in page and 'value="floral"' in page
    assert "Сохранить сессию" in page and "КАК ЭТО УВИДЯТ ДРУГИЕ" in page
    assert "STEEP TIMER" not in page and "Save session" not in page and "Rinsed the leaf" not in page
    # the scripts get their words too
    assert '"Clear all logged steeps?"' in page and '"{n} photo ready to upload"' in page


def test_form_errors_and_messages_in_russian(client):
    telegram_login(client)
    russian(client)
    token = csrf_token(client)
    page = new_session(client, token, tea_name="", temp_c="300", brewed_at="2999-01-01", steep_seconds=["abc"])
    body = text(page)
    assert page.status_code == 400 and "Пока не сохранилось" in body and "Какой это был чай?" in body
    assert "Температура: от 30 до 100." in body and "Эта дата ещё не наступила." in body and "Время пролива — целое число секунд." in body
    saved = new_session(client, token, visibility="public")
    page = text(client.get(saved.headers["location"]))
    assert "Сессия сохранена в дневнике. Она публичная." in page


def test_session_page_in_russian(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client), visibility="public"))
    russian(client)
    page = text(client.get(f"/sessions/{sid}"))
    assert "Заварить так же" in page and "Пролив за проливом" in page and "Пролив 3 · 15 с" in page
    assert "7 г" in page and "110 мл" in page and "Гайвань" in page and "Промывка" in page and "Да" in page
    assert "3 ПРОЛИВА" in page and "0 комментариев" in page and "Сделать личной" in page
    assert "Brew it the same way" not in page and "Steep by steep" not in page and "Make private" not in page
    # another reader
    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    russian(client)
    page = text(client.get(f"/sessions/{sid}"))
    assert "Подписаться" in page and "1 публичная сессия" in page and "Добавить комментарий" in page


def test_compare_in_russian(client):
    telegram_login(client)
    token = csrf_token(client)
    first = session_id_from(new_session(client, token))
    second = session_id_from(new_session(client, token, grams="5", rinse="", note="other"))
    russian(client)
    page = text(client.get(f"/sessions/{first}/compare", params={"with": second}))
    assert "Сравнение сессий" in page and "Промывка" in page and ">Нет<" in page and "5 г" in page and "5 ноя 2026" in page
    assert "Compare sessions" not in page and "Rinse" not in page


def test_story_cards_follow_the_language(client, settings):
    telegram_login(client, username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client), visibility="public"))
    english = client.get(f"/sessions/{sid}/story.png")
    russian(client)
    russian_card = client.get(f"/sessions/{sid}/story.png")
    assert russian_card.status_code == 200 and russian_card.content != english.content
    assert client.get(f"/sessions/{sid}/story.png").content == russian_card.content  # cached per language
    with Image.open(BytesIO(russian_card.content)) as image:
        assert image.size == (1080, 1920)
    names = sorted(path.name for path in (Path(settings.media_dir) / "stories").glob("*.png"))
    assert len(names) == 2 and names[0].endswith("-v2-ru.png") and names[1].endswith("-v2.png")
    assert "Cookie" in russian_card.headers["vary"]
    month = client.get("/diary/month.png")
    assert month.status_code == 200 and month.headers["content-type"] == "image/png"


def test_story_text_in_russian(db, client):
    from prolyv.story import _session_kicker, render_month_story

    telegram_login(client, username="olena_tea")
    sid = session_id_from(new_session(client, csrf_token(client)))
    sess = db.get(TeaSession, uuid.UUID(sid))
    set_lang("ru")
    try:
        assert _session_kicker(sess) == "3 ПРОЛИВА"
        stats = {"sessions": 5, "favourite": {"key": "black", "label": "Чёрный", "count": 3}, "new_teas": 2, "avg_rating": 4.5, "month_notes": ["floral"]}
        assert render_month_story(sess.user, stats, "example.org")[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        set_lang("en")


def test_photo_errors_in_russian():
    from prolyv.uploads import PhotoError, save_session_photo

    set_lang("ru")
    try:
        save_session_photo("/nonexistent", uuid.uuid4(), b"not a picture")
        raise AssertionError("expected PhotoError")
    except PhotoError as exc:
        assert str(exc) == "Этот файл не похож на фото."
    finally:
        set_lang("en")
