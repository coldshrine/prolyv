import pytest

from prolyv.web.useragent import is_in_app_browser
from tests.conftest import DESKTOP_UA, INSTAGRAM_UA, TIKTOK_UA

ANDROID_WEBVIEW = "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/UP1A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0 Mobile Safari/537.36"
FACEBOOK_IOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/450.0.0.38.108;FBBV/563;FBDV/iPhone15,2]"
TIKTOK_ANDROID = "Mozilla/5.0 (Linux; Android 13; SM-S918B Build/TP1A) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0 Mobile Safari/537.36 trill_2023 JsSdk/1.0 NetType/WIFI Channel/googleplay AppName/musical_ly app_version/33.4.0 ByteLocale/en Region/UA BytedanceWebview/d8a21c6"
IOS_SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
ANDROID_CHROME = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"


@pytest.mark.parametrize("ua", [INSTAGRAM_UA, TIKTOK_UA, TIKTOK_ANDROID, FACEBOOK_IOS, ANDROID_WEBVIEW])
def test_in_app_browsers_are_detected(ua):
    assert is_in_app_browser(ua)


@pytest.mark.parametrize("ua", [DESKTOP_UA, IOS_SAFARI, ANDROID_CHROME, "", None])
def test_regular_browsers_are_not(ua):
    assert not is_in_app_browser(ua)


def test_login_hides_google_inside_instagram(client):
    page = client.get("/login", headers={"user-agent": INSTAGRAM_UA}).text
    assert "Open in your browser to use Google, or continue with Telegram." in page
    assert "/auth/google/start" not in page
    assert "Continue with Telegram" in page


def test_login_hides_google_inside_tiktok(client):
    page = client.get("/login", headers={"user-agent": TIKTOK_UA}).text
    assert "/auth/google/start" not in page
    assert "Open in your browser to use Google" in page


def test_login_shows_google_in_a_regular_browser(client):
    page = client.get("/login").text
    assert "/auth/google/start" in page
    assert "Open in your browser to use Google" not in page
    assert "Read the feed without an account" in page


def test_head_requests_are_answered_like_get_without_a_body(client):
    response = client.head("/")
    assert response.status_code == 200
    assert response.content == b""
    assert client.head("/healthz").status_code == 200
    assert client.head("/no-such-page").status_code == 404
