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
