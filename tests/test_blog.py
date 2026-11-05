import re

from sqlalchemy import select
from starlette.testclient import TestClient

from prolyv.markdown import render_markdown
from prolyv.models import Post, TeaSession, video_embed
from tests.conftest import build_app, csrf_token, make_settings, telegram_login
from tests.test_diary import new_session, session_id_from
from tests.test_teas import make_tea

ADMIN = "karyna"
BODY = "## Why the third steep goes bitter\n\nShu wants a **rinse** and short steeps.\n\n- 7 g\n- 100 ml\n\n<script>alert(1)</script> see https://prolyv.com"


def admin_client(tmp_path, google) -> TestClient:
    return TestClient(build_app(make_settings(tmp_path, admin_handles=ADMIN), google))


def admin_login(client):
    return telegram_login(client, tg_id="900", username=ADMIN, first_name="Karyna")


def create_post(client, token, **overrides):
    data = {"csrf_token": token, "title": "Why does shu turn bitter?", "summary": "The short answer: rinse, then short steeps.",
            "body_md": BODY, "video_url": "https://www.tiktok.com/@prolyv/video/7301234567890123456", "publish": "on"}
    data.update(overrides)
    return client.post("/admin/posts/new", data=data, follow_redirects=False)


def test_markdown_is_rendered_and_sanitised():
    html = render_markdown(BODY)
    assert "<h2>Why the third steep goes bitter</h2>" in html and "<strong>rinse</strong>" in html and "<li>7 g</li>" in html
    assert "<script>" not in html and "alert(1)" in html
    assert 'href="https://prolyv.com"' in html and 'rel="noopener nofollow"' in html


def test_video_embed_detection():
    assert video_embed("https://www.tiktok.com/@prolyv/video/7301234567890123456?lang=en")["provider"] == "tiktok"
    assert video_embed("https://www.instagram.com/reel/C1abcDEfGh/?utm_source=ig") == {"provider": "instagram", "url": "https://www.instagram.com/reel/C1abcDEfGh/", "id": "C1abcDEfGh"}
    assert video_embed("https://www.instagram.com/p/C1abcDEfGh/")["url"].endswith("/p/C1abcDEfGh/")
    assert video_embed("https://youtu.be/x")["provider"] == "link"
    assert video_embed(None) is None


def test_only_admins_write_posts(client):
    telegram_login(client, username="olena_tea")
    assert client.get("/admin/posts").status_code == 403
    assert client.get("/admin/posts/new").status_code == 403
    assert client.post("/admin/posts/new", data={"csrf_token": csrf_token(client), "title": "x", "body_md": "y" * 30}, follow_redirects=False).status_code == 403


def test_draft_publish_embed_and_feed(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    token = csrf_token(client)

    draft = create_post(client, token, publish="")
    assert draft.status_code == 303 and draft.headers["location"] == "/blog/why-does-shu-turn-bitter"
    page = client.get("/blog/why-does-shu-turn-bitter").text
    assert "Draft — only admins" in page and "Why the third steep goes bitter" in page
    assert "Why does shu turn bitter" not in client.get("/blog").text
    client.cookies.clear()
    assert client.get("/blog/why-does-shu-turn-bitter").status_code == 404

    admin_login(client)
    post = db.execute(select(Post)).scalar_one()
    edit = client.post(f"/admin/posts/{post.id}/edit", data={"csrf_token": csrf_token(client), "title": "Why does shu turn bitter?",
                                                             "body_md": BODY, "video_url": "https://www.tiktok.com/@prolyv/video/7301234567890123456",
                                                             "publish": "on", "summary": "Rinse, then short steeps."}, follow_redirects=False)
    assert edit.status_code == 303
    client.cookies.clear()
    page = client.get("/blog/why-does-shu-turn-bitter").text
    assert 'class="tiktok-embed"' in page and 'data-video-id="7301234567890123456"' in page
    assert "<strong>rinse</strong>" in page and "<script>alert" not in page
    assert 'property="og:title" content="Why does shu turn bitter? · Prolyv blog"' in page
    assert "Brew along" in page and "Nobody has brewed along yet" in page
    assert "Why does shu turn bitter?" in client.get("/blog").text
    assert "Why does shu turn bitter?" in client.get("/", params={"tab": "blog"}).text
    assert "Why does shu turn bitter?" in client.get("/").text


def test_slug_uniqueness_and_delete(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    token = csrf_token(client)
    create_post(client, token)
    second = create_post(client, token)
    assert second.headers["location"] == "/blog/why-does-shu-turn-bitter-2"
    post = db.execute(select(Post).where(Post.slug == "why-does-shu-turn-bitter-2")).scalar_one()
    assert client.post(f"/admin/posts/{post.id}/delete", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert client.get("/blog/why-does-shu-turn-bitter-2").status_code == 404


def test_brew_along_and_comments(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    tea = make_tea(db, "shu-puerh", "Shu pu-erh", category="shu", country="China", region="Yunnan",
                   brewing={"grams": 8, "water_ml": 100, "temp_c": 100, "steeps": 3, "seconds": [10, 8, 10]})
    admin_login(client)
    create_post(client, csrf_token(client), tea_slug="shu-puerh")
    client.cookies.clear()

    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    form_page = client.get("/sessions/new", params={"post": "why-does-shu-turn-bitter"}).text
    assert "Brewing along with" in form_page and 'value="Shu pu-erh"' in form_page and 'data-suggested="10,8,10"' in form_page
    post = db.execute(select(Post)).scalar_one()
    sid = session_id_from(new_session(client, token, tea_id=str(tea.id), tea_name="Shu pu-erh", post_id=str(post.id), visibility="public"))
    db.expire_all()
    assert db.get(TeaSession, __import__("uuid").UUID(sid)).post_id == post.id
    page = client.get("/blog/why-does-shu-turn-bitter").text
    assert "Brewed along · 1" in page and "@mike" in page

    assert client.post("/blog/why-does-shu-turn-bitter/comments", data={"csrf_token": token, "body": "Worked for me, thanks!"}, follow_redirects=False).status_code == 303
    page = client.get("/blog/why-does-shu-turn-bitter").text
    assert "Worked for me, thanks!" in page and "Comments · 1" in page
    comment_id = re.search(r"/comments/([0-9a-f-]{36})/delete", page).group(1)
    client.cookies.clear()
    telegram_login(client, tg_id="3", username="stranger")
    assert client.post(f"/blog/why-does-shu-turn-bitter/comments/{comment_id}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 403
    client.cookies.clear()
    admin_login(client)
    assert client.post(f"/blog/why-does-shu-turn-bitter/comments/{comment_id}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 303
    assert "Worked for me" not in client.get("/blog/why-does-shu-turn-bitter").text
