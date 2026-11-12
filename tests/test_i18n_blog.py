"""The blog and the admin post pages in Russian."""

from __future__ import annotations

from sqlalchemy import select

from prolyv.models import Post
from tests.conftest import csrf_token, telegram_login
from tests.test_blog import admin_client, admin_login, create_post
from tests.test_diary import new_session
from tests.test_teas import make_tea


def russian(client):
    client.cookies.set("lang", "ru")
    return client


def test_blog_index_and_post_in_russian(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    tea = make_tea(db, "shu-puerh", "Shu pu-erh", category="shu", country="China", region="Yunnan",
                   brewing={"grams": 8, "water_ml": 100, "temp_c": 100, "steeps": 3, "seconds": [10, 8, 12]})
    admin_login(client)
    token = csrf_token(client)
    create_post(client, token, tea_slug="shu-puerh")
    create_post(client, token, title="A guide with no tea", video_url="")
    create_post(client, token, title="Draft about oolong", video_url="", publish="")
    client.cookies.clear()

    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    post = db.execute(select(Post).where(Post.slug == "why-does-shu-turn-bitter")).scalar_one()
    new_session(client, token, tea_id=str(tea.id), tea_name="Shu pu-erh", post_id=str(post.id), visibility="public", rating="4")
    for body in ("Worked for me, thanks!", "Second one", "Third one"):
        client.post("/blog/why-does-shu-turn-bitter/comments", data={"csrf_token": token, "body": body})
    russian(client)

    index = client.get("/blog").text
    assert '<html lang="ru">' in index and "ДОМ НАШИХ КОРОТКИХ ВИДЕО" in index and "Все посты" in index
    assert "Видеоразборы" in index and "Гайды" in index and "Свежий пост" in index and "Читать и обсуждать →" in index
    assert "3 комментария" in index and "1 сессия прикреплена" in index and "Чаще всего заваривали вместе" in index
    assert "МИН ЧТЕНИЯ" in index and "comment" not in index.split("<main")[1].split("</main>")[0]

    page = client.get("/blog/why-does-shu-turn-bitter").text
    assert "ВИДЕОРАЗБОР" in page and "Сессия из видео" in page and "Скопировать в дневник" in page and "8 г" in page and "100 мл" in page
    assert "Пролив за проливом" in page and "Пролив 3 · 12 с" in page and "ПРОЛИВ 1" in page
    assert "1 человек заварил вместе с нами" in page and "Средняя оценка" in page
    assert "3 комментария" in page and "Присоединяйтесь к обсуждению" in page and ">Отправить<" in page
    shown = page.split('<script type="application/json" id="ui-text">')[0]  # the scripts' dictionary has English keys
    assert "Обсудить на форуме" in page and "Steep" not in shown and "Helpful" not in shown

    refused = client.post("/blog/why-does-shu-turn-bitter/comments", data={"csrf_token": token, "body": " x "})
    assert refused.status_code == 400 and "Сначала напишите комментарий." in refused.text

    client.cookies.clear()
    russian(client)
    assert "Полезно" in client.get("/blog/why-does-shu-turn-bitter").text  # a guest sees the vote button on others' comments
    guest = client.get("/blog/a-guide-with-no-tea").text
    assert "ГАЙД" in guest and "Войти</a>, чтобы комментировать." in guest and "Заведите свой дневник" in guest and "Начать бесплатно" in guest


def test_admin_post_pages_in_russian(tmp_path, google):
    client = admin_client(tmp_path, google)
    admin_login(client)
    token = csrf_token(client)
    create_post(client, token)
    create_post(client, token, title="Draft about oolong", video_url="", publish="")
    russian(client)

    hub = client.get("/admin").text
    assert "Управление сайтом" in hub and "Жалобы" in hub and "постов: 2" in hub and "Посты блога" in hub

    posts = client.get("/admin/posts").text
    assert "Новый пост" in posts and "Черновик" in posts and "Опубликован " in posts and "Удалить этот пост?" in posts and ">Изменить<" in posts

    form = client.get("/admin/posts/new").text
    assert "Заголовок" in form and "Текст (Markdown)" in form and "## Почему это работает" in form and "Создать пост" in form

    refused = client.post("/admin/posts/new", data={"csrf_token": token, "title": "x", "body_md": "short", "cover_url": "http://x"})
    assert refused.status_code == 400 and "Дайте посту заголовок." in refused.text and "Посту нужен текст (Markdown)." in refused.text
    assert "Нужна полная ссылка с https://." in refused.text

    saved = create_post(client, token, title="Another post")
    assert saved.status_code == 303
    page = client.get(saved.headers["location"]).text
    assert "Опубликовано." in page
