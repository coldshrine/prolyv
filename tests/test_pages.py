from pathlib import Path

from starlette.testclient import TestClient

from tests.conftest import build_app, make_settings, telegram_login

TEMPLATES = Path(__file__).resolve().parent.parent / "prolyv" / "templates"


def test_static_pages_and_footer(client):
    for path, text in (("/about", "A home for every steep"), ("/rules", "Community rules"), ("/privacy", "Privacy policy"), ("/terms", "Terms of use")):
        page = client.get(path)
        assert page.status_code == 200 and text in page.text
    assert 'href="/privacy"' in client.get("/").text and 'href="/terms"' in client.get("/").text


def test_browser_files(client, settings):
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/apple-touch-icon.png").headers["content-type"] == "image/png"
    manifest = client.get("/manifest.json")
    assert manifest.json()["name"] == "Prolyv" and client.get(manifest.json()["icons"][0]["src"]).status_code == 200
    robots = client.get("/robots.txt").text
    assert f"Sitemap: {settings.base_url}/sitemap.xml" in robots and "Disallow: /admin" in robots


def test_social_links_only_from_env(tmp_path, google, client):
    assert "TikTok" not in client.get("/about").text.split("site-footer")[1]
    app = build_app(make_settings(tmp_path, social_tiktok="https://www.tiktok.com/@prolyv"), google)
    with TestClient(app) as other:
        assert 'href="https://www.tiktok.com/@prolyv"' in other.get("/about").text


def test_security_headers(client):
    headers = client.get("/").headers
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in headers


def test_no_inline_scripts_or_handlers_in_templates():
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text()
        assert " onsubmit=" not in text and " onclick=" not in text, path
        for chunk in text.split("<script")[1:]:
            head = chunk.split(">", 1)[0]
            assert " src=" in head or "application/ld+json" in head, f"inline script in {path}"


def test_no_leftovers_in_templates():
    import re

    pattern = re.compile(r"coming soon|TODO|\[account\]|\[site address\]", re.IGNORECASE)
    for path in TEMPLATES.rglob("*.html"):
        assert not pattern.search(path.read_text()), path


def test_branded_404_and_500_keep_the_user_signed_in(client):
    telegram_login(client, username="olena_tea")
    missing = client.get("/no/such/page")
    assert missing.status_code == 404 and "Nothing steeping here" in missing.text and "/u/olena_tea" in missing.text

    @client.app.get("/x/boom")
    def boom():
        raise RuntimeError("kaboom")

    with TestClient(client.app, raise_server_exceptions=False) as quiet:
        page = quiet.get("/x/boom")
    assert page.status_code == 500 and "Something went wrong" in page.text and "kaboom" not in page.text


def test_unified_search_finds_everything_in_every_language(client, db):
    from prolyv.search import index_tea
    from tests.conftest import csrf_token
    from tests.test_diary import new_session
    from tests.test_forum import post_topic
    from tests.test_teas import make_tea

    tea = make_tea(db, "tie-guan-yin", "Tie Guan Yin", name_zh="铁观音", name_ru="Те Гуань Инь", name_romanized="Tiě Guānyīn", aliases=["Iron Goddess"])
    index_tea(db, tea)
    db.commit()
    telegram_login(client, username="olena_tea")
    token = csrf_token(client)
    post_topic(client, token, title="Clay pots for oolong", body="Which Yixing clay suits Tie Guan Yin best?")
    new_session(client, token, tea_name="Secret Yixing session", visibility="private")
    new_session(client, token, tea_name="Morning Yixing session", visibility="public", note="Lovely")
    client.cookies.clear()

    for query in ("铁观音", "观音", "те гуань", "гуань инь", "tie", "tiě guanyin", "iron"):
        assert "Tie Guan Yin" in client.get("/search", params={"q": query}).text, query
    page = client.get("/search", params={"q": "yixing"}).text
    assert "Clay pots for oolong" in page and "Morning Yixing session" in page and "Secret Yixing" not in page
    assert "Clay pots" not in client.get("/search", params={"q": "yixing", "type": "sessions"}).text
    assert "Nothing matches" in client.get("/search", params={"q": "zzzqqq"}).text
    assert client.get("/search", params={"q": '"(*'}).status_code == 200
    assert client.get("/search").status_code == 200


def test_seed_prod_is_idempotent_and_has_no_invented_people(app, db, monkeypatch):
    import importlib
    import sys
    from pathlib import Path as P

    from sqlalchemy import select

    from prolyv.markdown import render_markdown
    from prolyv.models import Post, Topic, User

    sys.path.insert(0, str(P(__file__).resolve().parent.parent / "scripts"))
    seed_prod = importlib.import_module("seed_prod")
    author = seed_prod.get_prolyv_user(db)
    for _ in range(2):
        seed_prod.seed_guides(db, author)
        seed_prod.seed_topics(db, author)
        db.commit()
    assert len(db.execute(select(Post)).scalars().all()) == len(seed_prod.GUIDES)
    assert len(db.execute(select(Topic)).scalars().all()) == len(seed_prod.TOPICS)
    assert [u.handle for u in db.execute(select(User)).scalars()] == ["prolyv"] and author.identities == []
    assert 3 <= len(seed_prod.GUIDES) <= 5
    for guide in seed_prod.GUIDES:
        assert "<h2>" in render_markdown(guide["body"])


def test_seed_demo_refuses_real_domains(monkeypatch):
    import importlib
    import sys
    from pathlib import Path as P

    from prolyv.config import Settings

    sys.path.insert(0, str(P(__file__).resolve().parent.parent / "scripts"))
    seed_demo = importlib.import_module("seed_demo")
    monkeypatch.setattr(seed_demo, "get_settings", lambda: Settings(_env_file=None, secret_key="x" * 40, base_url="https://prolyv.app"))
    assert seed_demo.main() == 1


def test_error_pages_say_what_happened_and_send_people_home(client):
    from tests.conftest import csrf_token

    telegram_login(client, username="olena_tea")
    denied = client.get("/admin")
    assert denied.status_code == 403 and "have access to this page" in denied.text and "Back to login" not in denied.text and "form has expired" not in denied.text
    expired = client.post("/logout", data={"csrf_token": "wrong"})
    assert expired.status_code == 403 and "form has expired" in expired.text
    assert csrf_token(client)
    missing = client.get("/nonexistent")
    assert missing.status_code == 404 and 'href="/"' in missing.text and "Log in</a>" not in missing.text.split("</main>")[0]
    assert client.get("/sessions/notauuid").status_code == 404
    assert client.get("/discussions/abc").status_code == 404
    bad_page = client.get("/teas?page=abc", follow_redirects=False)
    assert bad_page.status_code == 303 and bad_page.headers["location"] == "/teas"


def test_signed_in_pages_are_not_cached_and_public_ones_may_be(client):
    assert "no-store" not in client.get("/teas").headers.get("cache-control", "")
    telegram_login(client, username="olena_tea")
    assert client.get("/diary").headers["cache-control"] == "no-store"
    assert client.get("/settings").headers["cache-control"] == "no-store"


def test_account_menu_reaches_saved_settings_and_log_out(client):
    telegram_login(client, username="olena_tea")
    page = client.get("/").text
    for target in ('href="/saved"', 'href="/settings"', 'action="/logout"'):
        assert target in page.split("</header>")[0]


def test_tea_photos_are_served_as_webp(tmp_path):
    settings = make_settings(tmp_path)
    folder = Path(settings.media_dir) / "teas" / "sencha"
    folder.mkdir(parents=True)
    (folder / "a-1200.webp").write_bytes(b"RIFF0000WEBP")
    with TestClient(build_app(settings)) as client:
        assert client.get("/media/teas/sencha/a-1200.webp").headers["content-type"] == "image/webp"
