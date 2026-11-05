import re
import uuid

from sqlalchemy import select
from starlette.testclient import TestClient

from prolyv.models import Follow, Notification, Reply, Report, TeaSession, Topic, User, Visibility
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
    assert ">Following</button>" in page and "1</p><p class=\"stat-label\">follower<" in page.replace("\n", "")

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
    assert '<blockquote><a href="/u/olena_tea">@olena_tea</a>: From steep three it gets harsh.</blockquote>' in page
    assert 'data-quote-text="' in page


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
    profile = client.get("/u/olena_tea?tab=topics").text
    assert 'hx-select=".more-topics &gt; *"' in profile or 'hx-select=".more-topics > *"' in profile
    assert "/u/olena_tea?topics_page=2" in profile
    assert "Topic34x" in client.get("/u/olena_tea?topics_page=2").text

    notes = client.get("/notifications").text
    assert "Note44x" in notes and "Note10x" not in notes and "/notifications?page=2" in notes and 'class="unread"' in notes
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))) == 0
    assert "Note10x" in client.get("/notifications?page=2").text


# ---------------------------------------------------------------------------------------------- profile


def _user(db, handle):
    db.expire_all()
    return db.execute(select(User).where(User.handle == handle)).scalar_one()


def _diary(db, user, **kw):
    fields = {"tea_name": "Da Hong Pao", "visibility": Visibility.public, "rating": 4, "taste_tags": ["roast"]} | kw
    db.add(TeaSession(user_id=user.id, **fields))


def test_profile_counts_only_public_non_hidden_sessions(client, db):
    token = login(client, 1, "olena_tea")
    user = _user(db, "olena_tea")
    _diary(db, user, tea_name="Shared one", rating=5, taste_tags=["Cocoa", "dates"])
    _diary(db, user, tea_name="Shared one", rating=4, taste_tags=["cocoa"])
    _diary(db, user, tea_name="Secret tea", visibility=Visibility.private, taste_tags=["secretnote"])
    _diary(db, user, tea_name="Taken down", hidden=True, taste_tags=["hiddennote"])
    db.commit()

    client.cookies.clear()
    for tab in ("sessions", "shelf", "tea", "topics"):
        page = client.get(f"/u/olena_tea?tab={tab}").text
        for leak in ("Secret tea", "secretnote", "Secretnote", "Taken down", "hiddennote", "Hiddennote"):
            assert leak not in page
    page = client.get("/u/olena_tea").text.replace("\n", "")
    assert '2</p><p class="stat-label">sessions logged' in page and '1</p><p class="stat-label">tea on the shelf' in page
    assert "2 shared. Private sessions stay private." in page and "logged ·" not in page
    assert "Your diary could look like this" in page and "First steep" in page and "At least 1 public session" in page
    assert "Regular" not in page  # not earned, so not drawn

    shelf = client.get("/u/olena_tea?tab=shelf").text
    assert "2 sessions · average 4.5 of 5" in shelf
    tea = client.get("/u/olena_tea?tab=tea").text
    assert "Cocoa · 2" in tea and "Dates · 1" in tea

    # the owner sees the total, and no sign-up pitch
    login(client, 1, "olena_tea")
    mine = client.get("/u/olena_tea").text
    assert "4 logged · 2 shared." in mine and "Your diary could look like this" not in mine and token
    assert 'action="/logout"' in mine and 'href="/saved"' in mine and 'href="/settings"' in mine


def test_profile_lists_replies_and_shows_most_helpful(client, db):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    token = login(client, 2, "mike")
    for body in ("Less leaf helps.", "Try a shorter first steep."):
        client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": body}, follow_redirects=False)
    mike = _user(db, "mike")
    assert "Most helpful reply" not in client.get("/u/mike").text
    replies = db.execute(select(Reply).where(Reply.user_id == mike.id).order_by(Reply.body)).scalars().all()
    replies[1].helpful_count = 7
    replies[0].helpful_count = 2
    db.commit()

    page = client.get("/u/mike").text.replace("\n", "")
    assert "Most helpful reply" in page and "Try a shorter first steep." in page and f"#reply-{replies[1].id}" in page
    assert '2</p><p class="stat-label">helpful replies' in page
    topics = client.get("/u/mike?tab=topics").text
    assert "REPLY" in topics and "Less leaf helps." in topics and "7 helpful" in topics

    replies[1].hidden = True
    db.commit()
    page = client.get("/u/mike").text.replace("\n", "")
    assert "Try a shorter first steep." not in page and '1</p><p class="stat-label">helpful reply' in page
    assert "Try a shorter first steep." not in client.get("/u/mike?tab=topics").text


def test_badge_rules_live_in_one_place():
    from prolyv.social import earned_badges

    titles = lambda **numbers: [b["title"] for b in earned_badges(numbers)]  # noqa: E731
    assert titles() == []
    assert titles(sessions=1) == ["First steep"]
    assert titles(sessions=30, helpful=5) == ["First steep", "Regular", "Helpful"]
    assert titles(sessions=29, helpful=4, teas=10, brew_alongs=3) == ["First steep", "Explorer", "Brewed along"]


def test_followers_and_following_lists(client, db):
    login(client, 1, "olena_tea")
    token = login(client, 2, "mike")
    client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False)
    profile = client.get("/u/olena_tea").text
    assert 'href="/u/olena_tea/followers"' in profile and 'href="/u/olena_tea/following"' in profile

    client.cookies.clear()
    followers = client.get("/u/olena_tea/followers").text
    assert "@mike" in followers and "1 follower<" in followers
    assert "@olena_tea" in client.get("/u/mike/following").text
    assert "No followers yet" in client.get("/u/mike/followers").text
    assert client.get("/u/nobody_here/followers").status_code == 404

    token = login(client, 2, "mike")
    response = client.post("/u/olena_tea/follow", data={"csrf_token": token, "next": "/u/mike/following"}, follow_redirects=False)
    assert response.headers["location"] == "/u/mike/following"


def test_toggling_follow_notifies_once_and_counts_against_the_quota(tmp_path, google):
    with TestClient(build_app(make_settings(tmp_path, post_rate_limit=3), google)) as client:
        login(client, 1, "olena_tea")
        token = login(client, 2, "mike")
        codes = [client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False).status_code for _ in range(4)]
        assert codes == [303, 303, 303, 429]
        # reports don't share that quota
        assert client.post("/report", data={"csrf_token": token, "kind": "user", "ref_id": str(uuid.uuid4()), "reason": "spam"}, follow_redirects=False).status_code == 400
        login(client, 1, "olena_tea")
        assert client.get("/notifications").text.count("started following you") == 1


def test_follow_after_login_lands_on_the_profile(client):
    login(client, 1, "olena_tea")
    response = client.get("/u/olena_tea/follow", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/u/olena_tea"


def test_notifications_follow_the_actor_and_drop_dead_links(client, db):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    token = login(client, 2, "mike")
    client.post("/u/olena_tea/follow", data={"csrf_token": token}, follow_redirects=False)
    client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": "Less leaf."}, follow_redirects=False)
    client.post("/settings", data={"csrf_token": token, "display_name": "Mike", "handle": "mike_new", "bio": ""}, follow_redirects=False)

    login(client, 1, "olena_tea")
    assert 'class="bell-count"' in client.get("/").text
    first = client.get("/notifications").text
    assert 'class="bell-count"' not in first  # cleared on arrival
    assert 'href="/u/mike_new"' in first and "@mike_new started following you" in first and "@mike " not in first
    assert "ago" in first or "just now" in first

    topic = db.get(Topic, uuid.UUID(tid))
    topic.hidden = True
    db.commit()
    assert f'href="/discussions/{tid}' not in client.get("/notifications").text
    assert "no longer available" in client.get("/notifications").text

    token = login(client, 2, "mike_new")
    client.post("/settings/delete", data={"csrf_token": token, "confirm": "mike_new"}, follow_redirects=False)
    login(client, 1, "olena_tea")
    page = client.get("/notifications").text
    assert "mike_new" not in page and "Deleted account started following you" in page and "/u/deleted_" not in page


def test_saved_cards_have_a_remove_control(client, db):
    token = login(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    client.post("/saved/toggle", data={"csrf_token": token, "kind": "topic", "ref_id": tid, "next": "/saved"}, follow_redirects=False)
    page = client.get("/saved").text
    assert ">Remove</button>" in page and f'name="ref_id" value="{tid}"' in page
    client.post("/saved/toggle", data={"csrf_token": token, "kind": "topic", "ref_id": tid, "next": "/saved"}, follow_redirects=False)
    assert "Nothing saved yet" in client.get("/saved").text
