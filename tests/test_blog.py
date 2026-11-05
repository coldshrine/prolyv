import re
from datetime import datetime

from sqlalchemy import event, func, select
from starlette.testclient import TestClient

from prolyv.markdown import insecure_images, render_markdown
from prolyv.models import ForumSection, HelpfulVote, Notification, Post, PostComment, TeaSession, Topic, User, Visibility, video_embed
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
    assert "Draft: only admins" in page and "Why the third steep goes bitter" in page
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
    assert 'data-embed-src="https://www.tiktok.com/player/v1/7301234567890123456"' in page and "Open on TikTok" in page
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
    assert "1 person brewed along" in page and "/sessions/" + sid in page

    assert client.post("/blog/why-does-shu-turn-bitter/comments", data={"csrf_token": token, "body": "Worked for me, thanks!"}, follow_redirects=False).status_code == 303
    page = client.get("/blog/why-does-shu-turn-bitter").text
    assert "Worked for me, thanks!" in page and "1 comment" in page
    comment_id = re.search(r"/comments/([0-9a-f-]{36})/delete", page).group(1)
    client.cookies.clear()
    telegram_login(client, tg_id="3", username="stranger")
    assert client.post(f"/blog/why-does-shu-turn-bitter/comments/{comment_id}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 403
    client.cookies.clear()
    admin_login(client)
    assert client.post(f"/blog/why-does-shu-turn-bitter/comments/{comment_id}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 303
    assert "Worked for me" not in client.get("/blog/why-does-shu-turn-bitter").text


def post_page(client, slug="why-does-shu-turn-bitter", **params):
    return client.get(f"/blog/{slug}", params=params)


def make_comment(db, post, user, body, **kw):
    comment = PostComment(post=post, user=user, body=body, **kw)
    db.add(comment)
    return comment


def test_markdown_typographer_alignment_and_insecure_images():
    html = render_markdown('"smart" -- dash\n\n| a | b |\n|:-:|--:|\n| 1 | 2 |\n\n![x](http://placehold.co/1.png) ![y](https://placehold.co/2.png)')
    assert "“smart” – dash" in html
    assert 'style="text-align:center"' in html and 'style="text-align:right"' in html
    assert 'src="https://placehold.co/2.png"' in html and "http://placehold.co/1.png" not in html
    assert insecure_images("![x](http://a.b/c.png) ![y](https://a.b/d.png)") == ["http://a.b/c.png"]
    assert "color:red" not in render_markdown('| a |\n|:-:|\n| b |') and "<td style" not in render_markdown('<td style="color:red">')


def test_post_form_rejects_what_would_break(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    token = csrf_token(client)

    for overrides, needle in (
        ({"cover_url": "http://placehold.co/1.png"}, "Use a full https:// link."),
        ({"video_url": "http://www.tiktok.com/@a/video/1"}, "Use a full https:// link."),
        ({"body_md": "A long enough body. ![x](http://placehold.co/1.png)"}, "Images need an https:// link"),
        ({"tea_slug": "no-such-tea"}, "No published tea with this slug"),
        ({"video_url": "https://vm.tiktok.com/ZMabc123/"}, "TikTok short links"),
        ({"video_url": "https://www.tiktok.com/t/ZTabc/"}, "TikTok short links"),
        ({"video_url": "https://m.tiktok.com/v/7123456789012345678.html"}, "This TikTok link"),
        ({"video_url": "https://www.instagram.com/prolyv/"}, "This Instagram link"),
    ):
        response = create_post(client, token, **overrides)
        assert response.status_code == 400 and needle in response.text, overrides
    assert db.scalar(select(func.count()).select_from(Post)) == 0

    assert create_post(client, token, video_url="https://www.instagram.com/prolyv/reel/CxAbCdEfGhI/").status_code == 303
    post = db.execute(select(Post)).scalar_one()
    assert post.video_url == "https://www.instagram.com/reel/CxAbCdEfGhI/" and post.embed["provider"] == "instagram"
    assert 'data-embed-src="https://www.instagram.com/reel/CxAbCdEfGhI/embed/"' in post_page(client).text
    assert create_post(client, token, title="A YouTube one", video_url="https://youtu.be/abc").status_code == 303


def test_index_tabs_latest_counts_and_most_brewed(tmp_path, google, monkeypatch):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    assert "The first brewing breakdown is coming" in client.get("/blog").text
    admin_login(client)
    token = csrf_token(client)
    create_post(client, token, title="Video one", video_url="https://www.tiktok.com/@prolyv/video/1")
    create_post(client, token, title="Guide one", video_url="")
    create_post(client, token, title="Video two", video_url="https://www.tiktok.com/@prolyv/video/2")
    posts = {p.title: p for p in db.execute(select(Post)).scalars()}
    for n, title in enumerate(("Video one", "Guide one", "Video two")):
        posts[title].published_at = datetime(2026, 1, n + 1)
    me = db.execute(select(User)).scalar_one()
    make_comment(db, posts["Video one"], me, "first")
    make_comment(db, posts["Video one"], me, "second")
    make_comment(db, posts["Video one"], me, "hidden one", hidden=True)
    db.add(TeaSession(user=me, tea_name="x", post_id=posts["Guide one"].id, visibility=Visibility.public))
    db.add(TeaSession(user=me, tea_name="x", post_id=posts["Video two"].id, visibility=Visibility.private))
    db.add(TeaSession(user=me, tea_name="x", post_id=posts["Video two"].id, visibility=Visibility.public, hidden=True))
    db.commit()
    client.cookies.clear()

    page = client.get("/blog").text
    assert "THE HOME OF OUR SHORT VIDEOS" in page and "LATEST" in page and "tiktok.com" not in page.split("blog-hero")[1].split("</section>")[0]
    assert page.count('class="blog-latest"') == 1 and page.count('class="blog-card"') == 2  # the newest is not repeated in the list
    assert "2 comments" in page and "3 comments" not in page
    assert "Most brewed along" in page and "1 session attached" in page
    assert page.split("Most brewed along")[1].split("</ol>")[0].count("<li>") == 1

    video = client.get("/blog", params={"tab": "video"}).text
    assert 'class="blog-latest"' not in video and video.count('class="blog-card"') == 2 and "Guide one" not in video.split("Most brewed")[0]
    guide = client.get("/blog", params={"tab": "guide"}).text
    assert guide.count('class="blog-card"') == 1 and "Video one" not in guide
    assert 'aria-current="true">Guides' in guide
    assert "New breakdowns by email" not in page and "Series" not in page


def test_index_social_pills_only_when_set(tmp_path, google):
    settings = make_settings(tmp_path, social_tiktok="https://www.tiktok.com/@prolyvtea", social_instagram="https://www.instagram.com/prolyv.tea/")
    client = TestClient(build_app(settings, google))
    page = client.get("/blog").text
    assert "TikTok @prolyvtea" in page and "Instagram @prolyv.tea" in page


def test_post_blocks_come_from_real_data(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    tea = make_tea(db, "shu-puerh", "Shu pu-erh", category="shu", country="China", region="Yunnan",
                   brewing={"grams": 8, "water_ml": 100, "temp_c": 100, "steeps": 3, "seconds": [10, 8, 12]})
    admin_login(client)
    token = csrf_token(client)
    create_post(client, token, tea_slug="shu-puerh")
    create_post(client, token, title="A guide with no tea", video_url="")
    page = post_page(client).text
    assert "The session from the video" in page and "8 g" in page and "100 °C" in page
    assert 'href="/sessions/new?post=why-does-shu-turn-bitter"' in page and "Copy to my diary" in page
    assert "Steep by step" in page and "Steep 3 · 12 s" in page and "12 seconds" in page
    assert "Discuss in the forum" in page and "FORUM THREAD" not in page
    plain = post_page(client, "a-guide-with-no-tea").text
    assert "The session" not in plain and "Steep by step" not in plain and "Copy to my diary" not in plain and "Play the video" not in plain

    post = db.execute(select(Post).where(Post.slug == "why-does-shu-turn-bitter")).scalar_one()
    me = db.execute(select(User)).scalar_one()
    friend = User(handle="olena", display_name="Olena K.")
    db.add(friend)
    db.flush()
    for rating, tags, owner in ((5, ["lilac", "cream"], me), (4, ["lilac"], friend), (None, ["cream"], friend)):
        db.add(TeaSession(user=owner, tea=tea, tea_name="Shu", post_id=post.id, visibility=Visibility.public, rating=rating, taste_tags=tags))
    db.add(TeaSession(user=friend, tea_name="Hidden", post_id=post.id, visibility=Visibility.public, hidden=True, rating=1, taste_tags=["ash"]))
    db.add(TeaSession(user=friend, tea_name="Private", post_id=post.id, visibility=Visibility.private, rating=1, taste_tags=["ash"]))
    db.add(Topic(section=ForumSection(slug="s", name="S", description="", sort=1), user=me, title="Links a longer slug", body="see /blog/why-does-shu-turn-bitter-2"))
    db.add(Topic(section=ForumSection(slug="t", name="T", description="", sort=2), user=me, title="Hidden thread", body="/blog/why-does-shu-turn-bitter", hidden=True))
    db.commit()
    page = post_page(client).text
    assert "2 people brewed along" in page and "<b>4.5</b>" in page and "<b>cream</b>" in page  # tie on lilac and cream: alphabetical
    assert "ash" not in page and "Olena K." in page
    assert "FORUM THREAD" not in page

    db.add(Topic(section=db.execute(select(ForumSection).where(ForumSection.slug == "s")).scalar_one(), user=me, title="Our thread", reply_count=1, body="Saw https://x/blog/why-does-shu-turn-bitter."))
    db.commit()
    page = post_page(client).text
    assert "FORUM THREAD" in page and "Our thread" in page and "1 reply" in page and "Discuss in the forum" not in page

    client.cookies.clear()
    assert "Start your own diary" in post_page(client).text


def test_brew_along_pages_past_twelve(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    create_post(client, csrf_token(client))
    post = db.execute(select(Post)).scalar_one()
    me = db.execute(select(User)).scalar_one()
    for _ in range(14):
        db.add(TeaSession(user=me, tea_name="x", post_id=post.id, visibility=Visibility.public))
    db.commit()
    first = post_page(client).text
    assert first.count('class="bp-along-row"') == 12 and "?along=2" in first
    second = post_page(client, along=2).text
    assert second.count('class="bp-along-row"') == 2 and "?along=3" not in second


def test_comment_error_keeps_text_and_quota_keeps_text(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    token = csrf_token(client)
    create_post(client, token)
    post_url = "/blog/why-does-shu-turn-bitter"
    refused = client.post(f"{post_url}/comments", data={"csrf_token": token, "body": " x "})
    assert refused.status_code == 400 and "Write a comment first." in refused.text and ">x </textarea>" in refused.text.replace("> x ", ">x ")
    assert 'action="/blog/why-does-shu-turn-bitter/comments#comment-form"' in refused.text
    assert 'value="/blog/why-does-shu-turn-bitter"' in refused.text  # Save goes back to the post, not to the POST address

    client.app.state.post_limiter.allow = lambda key: False
    limited = client.post(f"{post_url}/comments", data={"csrf_token": token, "body": "Something long I typed"})
    assert limited.status_code == 429 and "Something long I typed</textarea>" in limited.text
    assert db.scalar(select(func.count()).select_from(PostComment)) == 0


def test_comments_page_show_author_links_and_mentions(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    token = csrf_token(client)
    create_post(client, token)
    post = db.execute(select(Post)).scalar_one()
    me = db.execute(select(User)).scalar_one()
    for n in range(13):
        make_comment(db, post, me, f"Comment number {n}", created_at=datetime(2026, 1, 1, 0, n))
    db.commit()
    first = post_page(client).text
    assert first.count('class="bp-comment ') == 10 and "Show 3 more comments" in first and "Comment number 12" in first and "Comment number 2<" not in first
    assert " · author" in first and "Join the discussion" in first and "13 comments" in first
    second = post_page(client, cpage=2).text
    assert second.count('class="bp-comment ') == 3 and "Comment number 0" in second and "more comment" not in second
    assert 'value="/blog/why-does-shu-turn-bitter#comments"' in first  # hide and report come back to the comments

    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    client.post("/blog/why-does-shu-turn-bitter/comments", data={"csrf_token": csrf_token(client), "body": "line one\n\nsee https://example.com @karyna"})
    page = post_page(client).text
    assert 'href="https://example.com"' in page and "/u/karyna" in page
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.text.contains("mentioned you"))) == 1


def test_helpful_on_comments(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    create_post(client, csrf_token(client))
    client.post("/blog/why-does-shu-turn-bitter/comments", data={"csrf_token": csrf_token(client), "body": "My own comment"})
    comment = db.execute(select(PostComment)).scalar_one()
    url = f"/blog/why-does-shu-turn-bitter/comments/{comment.id}/helpful"

    assert client.post(url, data={"csrf_token": csrf_token(client)}, follow_redirects=False).status_code == 303
    assert db.scalar(select(func.count()).select_from(HelpfulVote)) == 0  # not for your own comment

    client.cookies.clear()
    telegram_login(client, tg_id="2", username="mike")
    token = csrf_token(client)
    vote = client.post(url, data={"csrf_token": token}, headers={"HX-Request": "true"})
    assert vote.status_code == 200 and 'aria-pressed="true"' in vote.text and "Helpful · 1" in vote.text and "<html" not in vote.text
    assert "Helpful · 1" in post_page(client).text
    assert client.post(url, data={"csrf_token": token}, follow_redirects=False).headers["location"].endswith(f"#comment-{comment.id}")
    assert db.scalar(select(func.count()).select_from(HelpfulVote)) == 0

    client.post(url, data={"csrf_token": token}, follow_redirects=False)
    client.cookies.clear()
    admin_login(client)
    client.post(f"/blog/why-does-shu-turn-bitter/comments/{comment.id}/delete", data={"csrf_token": csrf_token(client)})
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(HelpfulVote)) == 0


def test_post_page_query_count_stays_flat(tmp_path, google):
    client = admin_client(tmp_path, google)
    db = client.app.state.session_factory()
    admin_login(client)
    create_post(client, csrf_token(client))
    post = db.execute(select(Post)).scalar_one()
    me = db.execute(select(User)).scalar_one()
    client.cookies.clear()

    def queries(path):
        engine = client.app.state.session_factory.kw["bind"]
        seen = []
        listener = lambda *args: seen.append(args[2])
        event.listen(engine, "before_cursor_execute", listener)
        assert client.get(path).status_code == 200
        event.remove(engine, "before_cursor_execute", listener)
        return len(seen)

    def add(names):
        for n in names:
            person = User(handle=f"guest{n}", display_name=f"Guest {n}")
            db.add(person)
            db.flush()
            make_comment(db, post, person, "A comment")
            db.add(TeaSession(user=person, tea_name="x", post_id=post.id, visibility=Visibility.public))
            db.add(Post(slug=f"more-{n}", title=f"More {n}", author=me, published_at=datetime(2026, 1, 1)))
        db.commit()

    add(range(1))
    before = queries("/blog/why-does-shu-turn-bitter"), queries("/blog")
    add(range(1, 7))
    assert (queries("/blog/why-does-shu-turn-bitter"), queries("/blog")) == before
