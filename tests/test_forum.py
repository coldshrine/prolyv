import re
import uuid

from sqlalchemy import select

from prolyv.models import Reply, Topic
from prolyv.search import fts_query
from tests.conftest import build_app, csrf_token, make_settings, telegram_login
from tests.test_diary import new_session, session_id_from

ADMIN = "karyna"


def post_topic(client, token, **overrides):
    data = {"csrf_token": token, "section": "pu-erh", "title": "Why does my shu turn bitter after the third steep?",
            "body": "8 g in a 100 ml gaiwan, boiling water, 10 s steeps.\n\nFrom steep three it gets harsh. What am I doing wrong?"}
    data.update(overrides)
    return client.post("/discussions/new", data=data, follow_redirects=False)


def topic_id_from(response) -> str:
    match = re.search(r"/discussions/([0-9a-f-]{36})", response.headers["location"])
    assert match, response.headers.get("location")
    return match.group(1)


def test_index_lists_sections_and_requires_login_to_post(client):
    page = client.get("/discussions").text
    for name in ("Oolong", "Pu-erh", "Black &amp; white", "Teaware", "Beginners", "Where to buy"):
        assert name in page
    assert "No topics yet" in page
    assert client.get("/discussions/new", follow_redirects=False).status_code == 303
    assert client.get("/discussions/s/pu-erh").status_code == 200
    assert client.get("/discussions/s/nope").status_code == 404


def test_create_topic_and_search(client, db):
    telegram_login(client, username="olena_tea")
    response = post_topic(client, csrf_token(client))
    assert response.status_code == 303
    tid = topic_id_from(response)

    page = client.get(f"/discussions/{tid}").text
    assert "Why does my shu turn bitter" in page and "/u/olena_tea" in page and "What am I doing wrong?" in page
    assert "0 replies" in page and "Topic posted" in page

    index = client.get("/discussions").text
    assert "Why does my shu turn bitter" in index and "Pu-erh" in index
    assert "Why does my shu" in client.get("/discussions/s/pu-erh").text
    assert "Why does my shu" not in client.get("/discussions/s/oolong").text

    assert "Why does my shu" in client.get("/discussions", params={"q": "bitter"}).text
    assert "Why does my shu" in client.get("/discussions", params={"q": "gaiwan harsh"}).text  # body words, prefix+AND
    assert "Nothing matches" in client.get("/discussions", params={"q": "sencha"}).text
    assert client.get("/discussions", params={"q": 'weird "quotes" AND (stuff) *'}).status_code == 200


def test_fts_query_is_sanitised():
    assert fts_query('why "bitter" (shu)?') == '"why"* "bitter"* "shu"*'
    assert fts_query("   ") == ""
    assert fts_query("Улун чай") == '"улун"* "чай"*'
    assert fts_query("铁观音 tea") == '"铁 观 音" "tea"*'


def test_topic_validation(client):
    telegram_login(client)
    response = post_topic(client, csrf_token(client), title="Hi", body="short", section="nope")
    assert response.status_code == 400
    assert "at least 5 characters" in response.text and "at least 10 characters" in response.text and "Pick a section" in response.text


def test_replies_votes_and_marking_helpful(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    author_token = csrf_token(client)
    tid = topic_id_from(post_topic(client, author_token))
    client.cookies.clear()

    telegram_login(client, tg_id="2", username="mike")
    mike_token = csrf_token(client)
    response = client.post(f"/discussions/{tid}/replies", data={"csrf_token": mike_token, "body": "Rinse once and go 7 g. Keep steeps at 5 s until four."}, follow_redirects=False)
    assert response.status_code == 303 and "#reply-" in response.headers["location"]
    reply = db.execute(select(Reply)).scalar_one()
    topic = db.get(Topic, uuid.UUID(tid))
    assert topic.reply_count == 1 and topic.last_reply_at is not None
    assert "Why does my shu turn bitter" in client.get("/discussions", params={"q": "rinse"}).text  # reply text finds its topic

    # the reply author cannot vote on their own reply
    client.post(f"/discussions/{tid}/replies/{reply.id}/helpful", data={"csrf_token": mike_token}, follow_redirects=False)
    db.expire_all()
    assert db.get(Reply, reply.id).helpful_count == 0

    client.cookies.clear()
    telegram_login(client, tg_id="3", username="stranger")
    stranger_token = csrf_token(client)
    client.post(f"/discussions/{tid}/replies/{reply.id}/helpful", data={"csrf_token": stranger_token}, follow_redirects=False)
    db.expire_all()
    assert db.get(Reply, reply.id).helpful_count == 1
    assert "vote-on" in client.get(f"/discussions/{tid}").text
    client.post(f"/discussions/{tid}/replies/{reply.id}/helpful", data={"csrf_token": stranger_token}, follow_redirects=False)  # toggle off
    db.expire_all()
    assert db.get(Reply, reply.id).helpful_count == 0
    assert client.post(f"/discussions/{tid}/replies/{reply.id}/mark", data={"csrf_token": stranger_token}, follow_redirects=False).status_code == 403

    client.cookies.clear()
    telegram_login(client, tg_id="1", username="olena_tea")
    author_token = csrf_token(client)
    client.post(f"/discussions/{tid}/replies/{reply.id}/mark", data={"csrf_token": author_token}, follow_redirects=False)
    db.expire_all()
    assert db.get(Reply, reply.id).marked_helpful_by_author and db.get(Topic, uuid.UUID(tid)).solved
    page = client.get(f"/discussions/{tid}").text
    assert "Marked helpful by the author" in page and "Solved" in page
    assert "SOLVED" in client.get("/discussions").text
    client.cookies.clear()
    assert "1</p><p class=\"stat-label\">helpful replies" in client.get("/u/mike").text.replace("\n", "")

    telegram_login(client, tg_id="2", username="mike")
    mike_token = csrf_token(client)
    assert client.post(f"/discussions/{tid}/replies/{reply.id}/delete", data={"csrf_token": mike_token}, follow_redirects=False).status_code == 303
    db.expire_all()
    topic = db.get(Topic, uuid.UUID(tid))
    assert topic.reply_count == 0 and not topic.solved
    assert "Nothing matches" in client.get("/discussions", params={"q": "rinse"}).text


def test_attach_only_own_public_sessions(client, db):
    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    public_id = session_id_from(new_session(client, token, visibility="public", tea_name="Public Shu"))
    private_id = session_id_from(new_session(client, token, visibility="private", tea_name="Secret Shu"))
    assert "Public Shu" in client.get("/discussions/new").text and "Secret Shu" not in client.get("/discussions/new").text

    tid = topic_id_from(post_topic(client, token, attached_session_id=private_id))
    assert "Secret Shu" not in client.get(f"/discussions/{tid}").text
    tid2 = topic_id_from(post_topic(client, token, attached_session_id=public_id, title="Attached session test"))
    page = client.get(f"/discussions/{tid2}").text
    assert "Attached session" in page and "Public Shu" in page


def test_admin_pin_and_author_delete(tmp_path, google):
    app = build_app(make_settings(tmp_path, admin_handles=ADMIN), google)
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        telegram_login(client, tg_id="1", username="olena_tea")
        author_token = csrf_token(client)
        tid = topic_id_from(post_topic(client, author_token))
        assert client.post(f"/discussions/{tid}/pin", data={"csrf_token": author_token}, follow_redirects=False).status_code == 403
        client.cookies.clear()
        telegram_login(client, tg_id="900", username=ADMIN)
        admin_token = csrf_token(client)
        client.post(f"/discussions/{tid}/pin", data={"csrf_token": admin_token}, follow_redirects=False)
        assert "PINNED" in client.get("/discussions").text
        client.cookies.clear()
        telegram_login(client, tg_id="2", username="mike")
        assert client.post(f"/discussions/{tid}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 403
        client.cookies.clear()
        telegram_login(client, tg_id="1", username="olena_tea")
        assert client.post(f"/discussions/{tid}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).headers["location"] == "/discussions"
        assert client.get(f"/discussions/{tid}").status_code == 404


def test_home_feed_merges_public_sessions_and_topics(client):
    telegram_login(client, tg_id="1", username="olena_tea")
    token = csrf_token(client)
    new_session(client, token, visibility="public", tea_name="Feed Oolong")
    new_session(client, token, visibility="private", tea_name="Hidden Oolong")
    post_topic(client, token, title="Feed topic about storage")

    home = client.get("/").text
    assert "Hi, Olena" in home and "Feed Oolong" in home and "Feed topic about storage" in home and "Hidden Oolong" not in home
    assert "Feed topic" not in client.get("/", params={"tab": "sessions"}).text
    assert "Feed Oolong" not in client.get("/", params={"tab": "discussions"}).text
    client.cookies.clear()
    guest = client.get("/").text
    assert "Log your tea. Talk through every steep." in guest and "How it works" in guest and "Feed Oolong" in guest
    assert client.get("/feed", follow_redirects=False).headers["location"] == "/"
    assert client.get("/discuss", follow_redirects=False).headers["location"] == "/discussions"


def test_a_mention_reaches_the_person_and_the_ask_button_prefills_it(client, app):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from prolyv.models import Notification, User
    from tests.conftest import DESKTOP_UA

    telegram_login(client, tg_id="1", username="olena_tea")
    asker = TestClient(app, headers={"user-agent": DESKTOP_UA})
    telegram_login(asker, tg_id="2", username="tea_mike")
    form = asker.get("/discussions/new", params={"ask": "olena_tea"}).text
    assert "@olena_tea " in form and "they get a notification" in form
    section = form.split('name="section"')[1].split('<option value="')[2].split('"')[0]
    asker.post("/discussions/new", data={"csrf_token": csrf_token(asker), "section": section, "title": "A question for you",
                                         "body": "@olena_tea how long do you steep it?"}, follow_redirects=False)
    db = app.state.session_factory()
    olena = db.execute(select(User).where(User.handle == "olena_tea")).scalar_one()
    notes = db.execute(select(Notification).where(Notification.user_id == olena.id)).scalars().all()
    assert any("mentioned you" in n.text for n in notes)
    db.close()


def test_links_in_posts_are_clickable_and_nothing_else_becomes_html(client):
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    form = client.get("/discussions/new").text
    section = form.split('name="section"')[1].split('<option value="')[2].split('"')[0]
    response = client.post("/discussions/new", data={"csrf_token": token, "section": section, "title": "Links please",
                                                     "body": "See https://example.com/a?b=1&c=2. <script>alert(1)</script>"})
    page = response.text
    assert '<a href="https://example.com/a?b=1&amp;c=2" rel="nofollow ugc noopener"' in page and "<script>alert(1)" not in page
