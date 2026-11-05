import re
import uuid

from datetime import timedelta

from sqlalchemy import select

from prolyv.models import HelpfulVote, Notification, Post, Reply, ReplyVote, Topic, TopicFollow, User, utcnow
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
    assert "No topics here yet" in page
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
    assert "MARKED HELPFUL BY AUTHOR" in page and "SOLVED" in page
    assert "SOLVED" in client.get("/discussions").text
    client.cookies.clear()
    assert "1</p><p class=\"stat-label\">helpful reply" in client.get("/u/mike").text.replace("\n", "")

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


def as_user(client, tg_id, username):
    client.cookies.clear()
    telegram_login(client, tg_id=str(tg_id), username=username)
    return csrf_token(client)


def post_reply(client, token, tid, body="This worked for me, try it.", **extra):
    return client.post(f"/discussions/{tid}/replies", data={"csrf_token": token, "body": body, **extra}, follow_redirects=False)


def notes_for(db, handle):
    db.expire_all()
    user = db.execute(select(User).where(User.handle == handle)).scalar_one()
    return db.execute(select(Notification).where(Notification.user_id == user.id)).scalars().all()


def test_index_has_the_rail_and_real_blocks(client, db):
    page = client.get("/discussions").text
    assert "Ask a better question" in page and "New topic with a session" in page and "House rules" in page
    assert "Attach a session when asking about taste or brewing." in page
    assert "Most helpful this month" not in page  # nothing real to show yet
    assert "views" not in page

    token = as_user(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, token))
    reply_token = as_user(client, 2, "mike")
    post_reply(client, reply_token, tid)
    reply = db.execute(select(Reply)).scalar_one()
    voter = as_user(client, 3, "voter")
    client.post(f"/discussions/{tid}/replies/{reply.id}/helpful", data={"csrf_token": voter}, follow_redirects=False)
    page = client.get("/discussions").text
    assert "Most helpful this month" in page and "/u/mike" in page and "1 helpful" in page

    # a vote older than 30 days does not count
    db.expire_all()
    db.execute(select(ReplyVote)).scalar_one().created_at = utcnow() - timedelta(days=31)
    db.commit()
    assert "Most helpful this month" not in client.get("/discussions").text


def test_sort_filters_and_keeps_section_and_search(client, db):
    token = as_user(client, 1, "olena_tea")
    old = topic_id_from(post_topic(client, token, title="Older topic with a reply"))
    new = topic_id_from(post_topic(client, token, title="Newer topic without a reply"))
    other = as_user(client, 2, "mike")
    post_reply(client, other, old)

    def titles(html):
        return re.findall(r'class="f-row-title" href="/discussions/([0-9a-f-]{36})"', html)

    assert titles(client.get("/discussions").text)[0] == old  # active: latest reply first
    assert titles(client.get("/discussions", params={"sort": "newest"}).text)[0] == new
    assert titles(client.get("/discussions", params={"sort": "unanswered"}).text) == [new]
    assert client.get("/discussions", params={"sort": "nonsense"}).status_code == 200
    assert titles(client.get("/discussions/s/pu-erh", params={"sort": "unanswered"}).text) == [new]
    assert titles(client.get("/discussions/s/oolong", params={"sort": "unanswered"}).text) == []

    # most helpful counts votes on replies and on the opening post
    voter = as_user(client, 3, "voter")
    client.post(f"/discussions/{new}/helpful", data={"csrf_token": voter}, follow_redirects=False)
    assert titles(client.get("/discussions", params={"sort": "helpful"}).text)[0] == new

    # search can be sorted, and its empty state does not say the forum is empty
    page = client.get("/discussions", params={"q": "topic", "sort": "newest"}).text
    assert "Search: “topic”" in page and titles(page)[0] == new and 'name="q" value="topic"' in page
    page = client.get("/discussions/s/oolong", params={"q": "zzzqqq"}).text
    assert "Nothing matches" in page and "No topics here yet" not in page and "first one" not in page and "/discussions/new?section=oolong" in page
    assert "Clear search" not in client.get("/discussions").text
    # a section link keeps the query and the sort
    assert 'href="/discussions/s/oolong?q=topic&amp;sort=newest"' in client.get("/discussions", params={"q": "topic", "sort": "newest"}).text


def test_show_more_keeps_sort_and_section(client, db):
    token = as_user(client, 1, "olena_tea")
    for n in range(21):
        post_topic(client, token, title=f"Numbered topic {n:02d}")
    page = client.get("/discussions/s/pu-erh", params={"sort": "newest"}).text
    assert 'href="/discussions/s/pu-erh?sort=newest&amp;page=2"' in page
    second = client.get("/discussions/s/pu-erh?sort=newest&page=2").text
    assert "Numbered topic 00" in second and "Numbered topic 20" not in second
    assert client.get("/discussions", params={"page": "abc"}).status_code == 200
    assert client.get("/discussions/abc").status_code == 404


def test_hidden_replies_do_not_count_or_match_search(tmp_path, google):
    from starlette.testclient import TestClient

    with TestClient(build_app(make_settings(tmp_path, admin_handles=ADMIN), google)) as client:
        token = as_user(client, 1, "olena_tea")
        tid = topic_id_from(post_topic(client, token))
        reply_token = as_user(client, 2, "mike")
        post_reply(client, reply_token, tid, body="Only zanzibar water works here.")
        reply_id = re.search(r'id="reply-([0-9a-f-]{36})"', client.get(f"/discussions/{tid}").text).group(1)
        assert "<b>1</b> reply" in client.get("/discussions").text
        assert "Why does my shu" in client.get("/discussions", params={"q": "zanzibar"}).text

        admin = as_user(client, 900, ADMIN)
        client.post("/admin/hide", data={"csrf_token": admin, "kind": "reply", "ref_id": reply_id, "hidden": "1", "next": f"/discussions/{tid}"}, follow_redirects=False)
        client.cookies.clear()
        page = client.get("/discussions").text
        assert "<b>0</b> replies" in page and "last reply" not in page
        assert "Nothing matches" in client.get("/discussions", params={"q": "zanzibar"}).text
        assert "zanzibar" not in client.get(f"/discussions/{tid}").text and "0 replies" in client.get(f"/discussions/{tid}").text

        admin = as_user(client, 900, ADMIN)
        client.post("/admin/hide", data={"csrf_token": admin, "kind": "reply", "ref_id": reply_id, "hidden": "0", "next": "/"}, follow_redirects=False)
        client.cookies.clear()
        assert "<b>1</b> reply" in client.get("/discussions").text
        assert "Why does my shu" in client.get("/discussions", params={"q": "zanzibar"}).text


def test_follow_topic_notifies_once_and_never_yourself(client, db):
    author = as_user(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, author))

    assert f"/login?next=%2Fdiscussions%2F{tid}" in (client.cookies.clear() or client.get(f"/discussions/{tid}").text)
    follower = as_user(client, 2, "mike")
    assert "Follow topic" in client.get(f"/discussions/{tid}").text
    assert client.post(f"/discussions/{tid}/follow", data={"csrf_token": follower}, follow_redirects=False).status_code == 303
    assert "Following" in client.get(f"/discussions/{tid}").text

    # following twice is a toggle, there is never a second row
    client.post(f"/discussions/{tid}/follow", data={"csrf_token": follower}, follow_redirects=False)
    db.expire_all()
    assert db.execute(select(TopicFollow)).first() is None
    client.post(f"/discussions/{tid}/follow", data={"csrf_token": follower}, follow_redirects=False)

    # the follower's own reply tells the author, not the follower
    post_reply(client, follower, tid)
    assert len(notes_for(db, "mike")) == 0 and len(notes_for(db, "olena_tea")) == 1

    # someone else's reply reaches the follower, once even when it also mentions them; the author once too
    third = as_user(client, 3, "third")
    post_reply(client, third, tid, body="@mike and @olena_tea, here is what I found.")
    assert len(notes_for(db, "mike")) == 1 and len(notes_for(db, "olena_tea")) == 2
    assert "?reply=" in notes_for(db, "mike")[0].url

    # the author is told anyway, so the button is not offered to them
    as_user(client, 1, "olena_tea")
    assert "Follow topic" not in client.get(f"/discussions/{tid}").text


def test_opening_post_helpful(client, db):
    author = as_user(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, author))

    page = client.get(f"/discussions/{tid}").text
    assert "Helpful · 0" in page and "f-act-static" in page  # your own: visibly static
    client.post(f"/discussions/{tid}/helpful", data={"csrf_token": author}, follow_redirects=False)
    assert db.execute(select(HelpfulVote)).first() is None

    client.cookies.clear()
    page = client.get(f"/discussions/{tid}").text
    assert f'class="f-act" href="/login?next=%2Fdiscussions%2F{tid}"' in page and f'action="/discussions/{tid}/helpful"' not in page

    voter = as_user(client, 2, "mike")
    client.post(f"/discussions/{tid}/helpful", data={"csrf_token": voter}, follow_redirects=False)
    assert "Helpful · 1" in client.get(f"/discussions/{tid}").text
    client.post(f"/discussions/{tid}/helpful", data={"csrf_token": voter}, follow_redirects=False)
    assert "Helpful · 0" in client.get(f"/discussions/{tid}").text


def test_author_cannot_mark_their_own_reply_and_delete_confirms(client, db):
    author = as_user(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, author))
    post_reply(client, author, tid, body="Answering myself, it was the water.")
    reply = db.execute(select(Reply)).scalar_one()
    assert client.post(f"/discussions/{tid}/replies/{reply.id}/mark", data={"csrf_token": author}, follow_redirects=False).status_code == 403
    db.expire_all()
    assert not db.get(Reply, reply.id).marked_helpful_by_author and not db.get(Topic, uuid.UUID(tid)).solved

    page = client.get(f"/discussions/{tid}").text
    assert "Mark as helpful" not in page and 'data-confirm="Delete this reply?"' in page
    assert ">Quote<" in page and ">Post reply<" in page and 'id="reply"' in page
    response = client.post(f"/discussions/{tid}/replies/{reply.id}/delete", data={"csrf_token": author})
    assert "Reply deleted." in response.text and response.status_code == 200


def test_report_is_not_under_the_post_quota_and_reply_text_survives_it(tmp_path, google):
    from starlette.testclient import TestClient

    with TestClient(build_app(make_settings(tmp_path, post_rate_limit=1), google)) as client:
        token = as_user(client, 1, "olena_tea")
        tid = topic_id_from(post_topic(client, token))
        blocked = post_reply(client, token, tid, body="A long considered answer I do not want to lose.")
        assert blocked.status_code == 429 and "Slow down" in blocked.text
        assert "A long considered answer I do not want to lose." in blocked.text and f'action="/discussions/{tid}/replies"' in blocked.text
        other = as_user(client, 2, "mike")
        post_topic(client, other, title="Uses up the quota of this person")
        assert post_topic(client, other, title="One more than allowed here").status_code == 429
        assert "One more than allowed here" in post_topic(client, other, title="One more than allowed here").text
        report = client.post("/report", data={"csrf_token": other, "kind": "topic", "ref_id": tid, "reason": "spam"}, follow_redirects=False)
        assert report.status_code == 303


def test_long_thread_sorts_and_shows_more(client, db):
    author = as_user(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, author))
    writer = as_user(client, 2, "mike")
    for n in range(25):
        post_reply(client, writer, tid, body=f"Reply number {n:02d} here.")
    last = db.execute(select(Reply).where(Reply.body == "Reply number 24 here.")).scalar_one()
    voter = as_user(client, 3, "voter")
    client.post(f"/discussions/{tid}/replies/{last.id}/helpful", data={"csrf_token": voter}, follow_redirects=False)

    page = client.get(f"/discussions/{tid}").text
    assert "25 replies" in page and "Show 10 more replies" in page
    assert page.index("Reply number 24") < page.index("Reply number 00")  # most helpful first, then oldest
    assert "Reply number 12" not in page

    newest = client.get(f"/discussions/{tid}", params={"sort": "newest"}).text
    assert newest.index("Reply number 24") < newest.index("Reply number 20") and "Reply number 05" not in newest
    assert f"/discussions/{tid}?sort=newest&amp;offset=10" in newest
    more = client.get(f"/discussions/{tid}", params={"sort": "newest", "offset": "20"}).text
    assert "Reply number 00" in more and "more replies" not in more
    assert "Show 5 more replies" in client.get(f"/discussions/{tid}", params={"sort": "newest", "offset": "10"}).text

    # a link to one reply far down opens the thread far enough to contain it
    deep = db.execute(select(Reply).where(Reply.body == "Reply number 20 here.")).scalar_one()
    assert f'id="reply-{deep.id}"' not in client.get(f"/discussions/{tid}").text
    assert f'id="reply-{deep.id}"' in client.get(f"/discussions/{tid}", params={"reply": str(deep.id)}).text
    assert "?reply=" in post_reply(client, voter, tid).headers["location"]


def test_topic_side_blocks_are_real(client, db):
    author = as_user(client, 1, "olena_tea")
    first = session_id_from(new_session(client, author, visibility="public", tea_name="Shared Shu", steep_seconds=["10", "12"], steep_note=["", ""]))
    tid = topic_id_from(post_topic(client, author, attached_session_id=first))
    page = client.get(f"/discussions/{tid}").text
    assert "Sessions in this thread" in page and "(author)" in page and "/sessions/compare" not in page  # one session: nothing to compare
    assert "10 s" in page and "12 s" in page and "1 session logged" in page
    assert "Related topics" not in page
    assert "FROM THE BLOG" not in page  # no published post yet

    other = as_user(client, 2, "mike")
    second = session_id_from(new_session(client, other, visibility="public", tea_name="Shared Shu"))
    post_reply(client, other, tid, attached_session_id=second)
    related = topic_id_from(post_topic(client, other, title="A neighbour in the same section"))
    other_section = topic_id_from(post_topic(client, other, section="oolong", title="Someone else entirely here"))
    page = client.get(f"/discussions/{tid}").text
    assert f"/sessions/{first}/compare?with={second}" in page and "Same tea, 2 ways." in page and "Brew it the same way" in page
    assert "Related topics" in page and f"/discussions/{related}" in page and f"/discussions/{other_section}" not in page

    # a session turned private leaves the block and the comparison
    client.post(f"/sessions/{second}/delete", data={"csrf_token": other}, follow_redirects=False)
    assert "/sessions/compare" not in client.get(f"/discussions/{tid}").text

    me = db.execute(select(User).where(User.handle == "mike")).scalar_one()
    db.add(Post(slug="draft-one", title="An unpublished draft", author=me))
    db.add(Post(slug="out-now", title="A published guide", author=me, published_at=utcnow()))
    db.commit()
    page = client.get(f"/discussions/{tid}").text
    assert "FROM THE BLOG" in page and "A published guide" in page and "An unpublished draft" not in page


def test_deleting_a_topic_clears_what_pointed_at_it(client, db):
    author = as_user(client, 1, "olena_tea")
    tid = topic_id_from(post_topic(client, author))
    other = as_user(client, 2, "mike")
    post_reply(client, other, tid)
    client.post(f"/discussions/{tid}/helpful", data={"csrf_token": other}, follow_redirects=False)
    assert len(notes_for(db, "olena_tea")) == 1
    author = as_user(client, 1, "olena_tea")
    client.post(f"/discussions/{tid}/delete", data={"csrf_token": author}, follow_redirects=False)
    assert notes_for(db, "olena_tea") == []
    assert db.execute(select(HelpfulVote)).first() is None
