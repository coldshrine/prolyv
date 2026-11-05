import re
import uuid

from sqlalchemy import select
from starlette.testclient import TestClient

from prolyv.models import Follow, Notification, Report, Topic, User
from tests.conftest import build_app, csrf_token, make_settings, telegram_login
from tests.test_diary import new_session, session_id_from
from tests.test_forum import post_topic, topic_id_from
from tests.test_teas import make_tea

ADMIN = "karyna"


def login(client, tg_id, username):
    client.cookies.clear()
    telegram_login(client, tg_id=str(tg_id), username=username)
    return csrf_token(client)


def test_follow_feed_and_notifications(client, db):
    login(client, 1, "olena_tea")
    token = login(client, 2, "mike")
    assert client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    page = client.get("/u/olena_tea").text
    assert "Following ✓" in page and "1</p><p class=\"stat-label\">followers" in page.replace("\n", "")

    token = login(client, 1, "olena_tea")
    assert 'class="bell-count">1<' in client.get("/").text
    notices = client.get("/notifications").text
    assert "@mike started following you" in notices
    assert "bell-count" not in client.get("/").text  # read once viewed

    sid = session_id_from(new_session(client, token, tea_name="Followed Oolong", visibility="public"))
    post_topic(client, token, title="Followed topic about clay")
    client.post(f"/sessions/{sid}/visibility", data={"csrf_token": token, "visibility": "private"}, follow_redirects=False)
    client.post(f"/sessions/{sid}/visibility", data={"csrf_token": token, "visibility": "public"}, follow_redirects=False)

    login(client, 2, "mike")
    notices = client.get("/notifications").text
    assert notices.count("logged Followed Oolong") == 1  # announced once, despite the toggle
    assert "started a topic: Followed topic about clay" in notices
    feed = client.get("/", params={"tab": "following"}).text
    assert "Followed Oolong" in feed and "Followed topic about clay" in feed

    token = csrf_token(client)
    client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False)
    db.expire_all()
    assert db.execute(select(Follow)).first() is None
    assert "Followed Oolong" not in client.get("/", params={"tab": "following"}).text


def test_cannot_follow_yourself_and_anon_goes_to_login(client, db):
    token = login(client, 1, "olena_tea")
    client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False)
    assert db.execute(select(Follow)).first() is None
    client.cookies.clear()
    assert "/login?next=/u/olena_tea" in client.get("/u/olena_tea").text


def test_replies_and_comments_notify_the_author(client):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    sid = session_id_from(new_session(client, token, visibility="public", tea_name="Commented Tea"))
    token = login(client, 2, "mike")
    client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "Try less leaf."}, follow_redirects=False)
    client.post(f"/sessions/{sid}/comments", data={"csrf_token": token, "body": "Lovely."}, follow_redirects=False)
    login(client, 1, "olena_tea")
    notices = client.get("/notifications").text
    assert "@mike replied to" in notices and "commented on your Commented Tea session" in notices


def test_saved_items(client, db):
    tea = make_tea(db, "sencha", "Sencha", category="green", country="Japan", region="Shizuoka")
    token = login(client, 1, "olena_tea")
    assert "Nothing saved yet" in client.get("/saved").text
    tid = topic_id_from(post_topic(client, token))
    client.post("/saved/toggle", data={"csrf_token": token, "kind": "tea", "ref_id": str(tea.id), "next": "/teas/sencha"}, follow_redirects=False)
    client.post("/saved/toggle", data={"csrf_token": token, "kind": "topic", "ref_id": tid, "next": "/saved"}, follow_redirects=False)
    assert "Saved ✓" in client.get("/teas/sencha").text
    page = client.get("/saved").text
    assert "Sencha" in page and "Why does my shu" in page
    client.post("/saved/toggle", data={"csrf_token": token, "kind": "tea", "ref_id": str(tea.id)}, follow_redirects=False)
    assert "Sencha" not in client.get("/saved").text
    assert client.post("/saved/toggle", data={"csrf_token": token, "kind": "nope", "ref_id": str(tea.id)}, follow_redirects=False).status_code == 400


def test_report_hide_and_admin_tools(tmp_path, google):
    with TestClient(build_app(make_settings(tmp_path, admin_handles=ADMIN), google)) as client:
        db = client.app.state.session_factory()
        token = login(client, 1, "olena_tea")
        tid = topic_id_from(post_topic(client, token, title="Buy my magic tea cures everything"))
        token = login(client, 2, "mike")
        assert client.post("/report", data={"csrf_token": token, "kind": "topic", "ref_id": tid, "reason": "spam", "next": "/discussions"}, follow_redirects=False).status_code == 303
        client.post("/report", data={"csrf_token": token, "kind": "topic", "ref_id": tid, "reason": "spam"}, follow_redirects=False)
        assert len(db.execute(select(Report)).scalars().all()) == 1
        assert client.get("/admin").status_code == 403 and client.get("/admin/reports").status_code == 403

        admin_token = login(client, 900, ADMIN)
        assert "1 open" in client.get("/admin").text
        queue = client.get("/admin/reports").text
        assert "Spam or advertising" in queue and "Buy my magic tea" in queue
        client.post("/admin/hide", data={"csrf_token": admin_token, "kind": "topic", "ref_id": tid, "hidden": "1"}, follow_redirects=False)
        assert client.get(f"/discussions/{tid}").status_code == 200  # admins still see it

        login(client, 2, "mike")
        assert client.get(f"/discussions/{tid}").status_code == 404
        assert "Buy my magic tea" not in client.get("/discussions").text and "Buy my magic tea" not in client.get("/").text

        admin_token = login(client, 900, ADMIN)
        report = db.execute(select(Report)).scalar_one()
        client.post(f"/admin/reports/{report.id}/resolve", data={"csrf_token": admin_token}, follow_redirects=False)
        assert "0 open" in client.get("/admin").text
        assert "@mike" in client.get("/admin/users", params={"q": "mik"}).text

        assert client.post("/admin/users/mike/ban", data={"csrf_token": admin_token, "banned": "1"}, follow_redirects=False).status_code == 303
        client.post(f"/admin/users/{ADMIN}/ban", data={"csrf_token": admin_token, "banned": "1"}, follow_redirects=False)
        db.expire_all()
        assert db.execute(select(User).where(User.handle == ADMIN)).scalar_one().banned_at is None
        client.cookies.clear()
        response = telegram_login(client, tg_id="2", username="mike")
        assert response.status_code == 403 and "restricted" in response.text


def test_quote_button_and_quote_rendering(client):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    token = login(client, 2, "mike")
    client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "> @olena_tea: From steep three it gets harsh.\n\nUse less leaf."}, follow_redirects=False)
    page = client.get(f"/discussions/{tid}").text
    assert "<blockquote>@olena_tea: From steep three it gets harsh.</blockquote>" in page
    assert 'data-quote="' in page


def test_post_rate_limit(tmp_path, google):
    with TestClient(build_app(make_settings(tmp_path, post_rate_limit=2), google)) as client:
        token = login(client, 1, "olena_tea")
        tid = topic_id_from(post_topic(client, token))
        assert client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "one"}, follow_redirects=False).status_code == 303
        blocked = client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "two"}, follow_redirects=False)
        assert blocked.status_code == 429 and "Slow down" in blocked.text


def test_soft_delete_erases_diary_and_shows_deleted_account(client, db):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    sid = session_id_from(new_session(client, token, visibility="public"))
    client.post("/settings/delete", data={"csrf_token": token, "confirm": "olena_tea"}, follow_redirects=False)
    assert client.get(f"/sessions/{sid}").status_code == 404
    page = client.get(f"/discussions/{tid}").text
    assert "Deleted account" in page and "/u/deleted_" not in page
    db.expire_all()
    assert db.get(Topic, uuid.UUID(tid)) is not None
    assert db.execute(select(Notification)).first() is None
    assert not re.search(r"/u/deleted_", client.get("/discussions").text)


def test_long_lists_continue_with_show_more(client, db):
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from prolyv.models import ForumSection, Notification, Post, TeaSession, Topic, User, Visibility
    from tests.conftest import telegram_login

    telegram_login(client, username="olena_tea")
    user = db.execute(select(User).where(User.handle == "olena_tea")).scalar_one()
    section = db.execute(select(ForumSection)).scalars().first()
    start = datetime(2026, 1, 1)
    for n in range(45):
        at = start + timedelta(hours=n)
        db.add(TeaSession(user_id=user.id, tea_name=f"Brew{n:02}x", brewed_at=at, visibility=Visibility.public))
        db.add(Topic(section_id=section.id, user_id=user.id, title=f"Topic{n:02}x", body="Body", created_at=at))
        db.add(Post(slug=f"post-{n}", title=f"Post{n:02}x", author_id=user.id, published_at=at))
        db.add(Notification(user_id=user.id, text=f"Note{n:02}x", url="/", created_at=at))
    db.commit()

    def walk(url, pattern, selector=r'hx-get="([^"]+)"'):
        names = []
        for _ in range(60):
            page = client.get(url.replace("&amp;", "&"))
            assert page.status_code == 200, url
            names += re.findall(pattern, page.text)
            more = re.search(selector, page.text.split("show-more", 1)[1]) if "show-more" in page.text else None
            if not more:
                return names
            url = more.group(1)
        raise AssertionError("did not end")

    assert len(set(walk("/?tab=sessions", r"Brew\d\dx"))) == 45
    assert len(set(walk("/?tab=discussions", r"Topic\d\dx"))) == 45
    assert len(set(walk("/?tab=blog", r"Post\d\dx"))) == 45
    everything = walk("/", r"(?:Brew|Topic|Post)\d\dx")
    assert len(set(everything)) == 135
    assert len(set(walk("/discussions", r"Topic\d\dx"))) == 45
    assert len(set(walk("/blog", r"Post\d\dx"))) == 45
    assert len(set(walk("/u/olena_tea", r"Brew\d\dx", r'hx-get="(/u/olena_tea\?page=\d+)"'))) == 45

    # topics on a profile have their own button, which pulls only topics
    profile = client.get("/u/olena_tea").text
    assert 'hx-select=".more-topics &gt; *"' in profile or 'hx-select=".more-topics > *"' in profile
    assert "/u/olena_tea?topics_page=2" in profile
    assert "Topic34x" in client.get("/u/olena_tea?topics_page=2").text

    notes = client.get("/notifications").text
    assert "Note44x" in notes and "Note10x" not in notes and "/notifications?page=2" in notes and 'class="unread"' in notes
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))) == 0
    assert "Note10x" in client.get("/notifications?page=2").text
