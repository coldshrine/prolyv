"""The feed, the forum, notifications, follows, saved items and reports in Russian."""

from __future__ import annotations

from starlette.testclient import TestClient

from prolyv.i18n import set_lang
from prolyv.social import notice_text
from tests.conftest import build_app, csrf_token, make_settings, telegram_login
from tests.test_diary import new_session, session_id_from
from tests.test_forum import post_topic, topic_id_from


def login(client, tg_id, username, lang="ru"):
    client.cookies.clear()
    telegram_login(client, tg_id=str(tg_id), username=username)
    client.cookies.set("lang", lang)
    return csrf_token(client)


def test_home_and_feed_in_russian(client):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token, section="oolong", title="Почему мой улун кислит"))
    new_session(client, token, tea_name="Da Hong Pao", visibility="public")
    client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "Меньше листа."}, follow_redirects=False)
    page = client.get("/").text
    assert "Привет, Olena" in page and "Горячие темы" in page and "Разделы" in page and ">Улун<" in page
    assert "1 ответ" in page and "Все обсуждения →" in page and "Заварить так же" in page
    assert ">Подписки<" in page and ">Темы<" in page  # the feed tabs: nouns, short enough for a phone
    assert "Hot topics" not in page and " replies" not in page and "Show more" not in page

    client.cookies.clear()
    client.cookies.set("lang", "ru")
    page = client.get("/").text
    assert "Записывайте чай. Обсуждайте каждый пролив." in page and "заваривает Da Hong Pao" in page
    assert "Начать дневник" in page and "СЕССИЯ" in page and "Заварить так же" in page


def test_forum_list_section_and_search_in_russian(client):
    token = login(client, 1, "olena_tea")
    for n in range(3):
        post_topic(client, token, title=f"Шу горчит после третьего пролива {n}")
    page = client.get("/discussions").text
    assert '<html lang="ru">' in page and "Все темы" in page and "Правила" in page and "Как задать хороший вопрос" in page
    assert "3 темы" in page and "Все разделы" in page and "Активные" in page and ">ПУЭР<" in page
    section = client.get("/discussions/s/pu-erh").text
    assert "Шэн и шу, хранение, выдержка, регионы." in section and "<title>Пуэр · Обсуждения" in section
    found = client.get("/discussions/s/pu-erh", params={"q": "горчит"}).text
    assert "Поиск: «горчит»" in found and "3 темы в разделе «Пуэр»" in found and "Сбросить поиск" in found
    none = client.get("/discussions", params={"q": "сенча"}).text
    assert "Ни одна тема не содержит этих слов." in none and "задайте вопрос в новой теме</a>" in none
    form = client.get("/discussions/new").text
    assert "Новая тема" in form and "Опубликовать тему" in form and "Почему мой шу горчит после третьего пролива?" in form
    bad = client.post("/discussions/new", data={"csrf_token": token, "section": "", "title": "x", "body": "y"})
    assert "Дайте теме заголовок" in bad.text and "Выберите раздел." in bad.text


def test_topic_with_replies_in_russian(client):
    token = login(client, 1, "olena_tea")
    sid = session_id_from(new_session(client, token, tea_name="Tie Guan Yin", visibility="public"))
    tid = topic_id_from(post_topic(client, token, attached_session_id=sid))
    assert "Тема опубликована." in client.get(f"/discussions/{tid}").text
    token = login(client, 2, "mike")
    client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "Попробуйте 90 °C."}, follow_redirects=False)
    page = client.get(f"/discussions/{tid}").text
    assert "1 ответ" in page and "ПРИКРЕПЛЁННАЯ СЕССИЯ" in page and "с промывкой" in page and "7 г" in page and "110 мл" in page
    assert "Следить за темой" in page and "Цитировать" in page and "Полезно · 0" in page and ">Отправить<" in page
    assert "Пожаловаться" in page and "Спам или реклама" in page and "1 сессия в дневнике" in page
    assert not any(english in page for english in ("Follow topic", "Post reply", "ATTACHED SESSION", ">Quote<", "Helpful ·", "Send report"))
    response = client.post(f"/discussions/{tid}/follow", data={"csrf_token": token}, follow_redirects=True)
    assert "Вы следите за темой." in response.text and "Вы подписаны" in response.text


def test_notifications_follow_the_readers_language(client):
    token = login(client, 1, "olena_tea", lang="en")
    tid = topic_id_from(post_topic(client, token))
    token = login(client, 2, "mike", lang="en")  # the actor writes in English…
    client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False)
    client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "@olena_tea try less leaf."}, follow_redirects=False)
    login(client, 1, "olena_tea")  # …and the reader reads in Russian
    notices = client.get("/notifications").text
    assert "Новый подписчик: @mike" in notices and "@mike упоминает вас в «Why does my shu" in notices
    assert "started following you" not in notices and "mentioned you" not in notices
    client.cookies.set("lang", "en")
    assert "@mike started following you" in client.get("/notifications").text


def test_every_stored_notification_shape_translates():
    stored = [
        ("@mike started following you", "Новый подписчик: @mike"),
        ("@mike replied to “Clay”", "@mike отвечает в теме «Clay»"),
        ("@mike mentioned you in a comment on the Sencha session", "@mike упоминает вас в комментарии к сессии Sencha"),
        ("@mike mentioned you in a comment on “Post”", "@mike упоминает вас в комментарии к «Post»"),
        ("@mike mentioned you in “Clay”", "@mike упоминает вас в «Clay»"),
        ("@mike started a topic: Clay", "@mike открывает тему: Clay"),
        ("@mike commented on your Sencha session", "@mike комментирует вашу сессию Sencha"),
        ("@mike commented on “Post”", "@mike комментирует «Post»"),
        ("@mike logged Sencha 2024", "@mike делится сессией: Sencha 2024"),
        ("Your reply in “Clay” was marked helpful", "Ваш ответ в теме «Clay» отмечен как полезный"),
        ("Deleted account replied to “Clay”", "Удалённый аккаунт отвечает в теме «Clay»"),
        ("Something nobody wrote a shape for", "Something nobody wrote a shape for"),
    ]
    set_lang("ru")
    try:
        assert [notice_text(text) for text, _ru in stored] == [ru for _text, ru in stored]
    finally:
        set_lang("en")
    assert notice_text("@mike replied to “Clay”") == "@mike replied to “Clay”"


def test_people_and_saved_in_russian(client):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    client.post("/saved/toggle", data={"csrf_token": token, "kind": "topic", "ref_id": tid, "next": "/saved"}, follow_redirects=False)
    saved = client.get("/saved").text
    assert "Ваши закладки" in saved and "<h2 class=\"h2 saved-title\">Темы</h2>" in saved and "ПУЭР" in saved
    token = login(client, 2, "mike")
    client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False)
    followers = client.get("/u/olena_tea/followers").text
    assert "<h1 class=\"h1\">Подписчики</h1>" in followers and "1 подписчик" in followers and "0 подписок" in followers
    following = client.get("/u/mike/following").text
    assert "<h1 class=\"h1\">Подписки</h1>" in following and "Вы подписаны" in following and "1 подписка" in following
    assert "Пока ничего не сохранено" in client.get("/saved").text


def test_reports_queue_in_russian(tmp_path, google):
    with TestClient(build_app(make_settings(tmp_path, admin_handles="karyna"), google)) as client:
        token = login(client, 1, "olena_tea")
        tid = topic_id_from(post_topic(client, token))
        token = login(client, 2, "mike")
        response = client.post("/report", data={"csrf_token": token, "kind": "topic", "ref_id": tid, "reason": "rude", "next": "/discussions"},
                               follow_redirects=True)
        assert "Спасибо. Администратор посмотрит." in response.text
        login(client, 900, "karyna")
        queue = client.get("/admin/reports").text
        assert "Грубость или травля · тема · от @mike" in queue and "Отметить рассмотренной" in queue and "<h1 class=\"h1\">Жалобы</h1>" in queue
