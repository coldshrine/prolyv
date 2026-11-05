from starlette.testclient import TestClient

from prolyv.ratelimit import RateLimiter
from tests.conftest import build_app, make_settings


def test_limiter_uses_a_sliding_window():
    limiter = RateLimiter(limit=3, window_seconds=60)
    assert all(limiter.allow("1.2.3.4", now=t) for t in (0, 1, 2))
    assert not limiter.allow("1.2.3.4", now=3)
    assert limiter.allow("5.6.7.8", now=3)  # other clients are unaffected
    assert not limiter.allow("1.2.3.4", now=59)
    assert limiter.allow("1.2.3.4", now=61)  # the hit at t=0 has aged out


def test_limiter_evicts_least_recent_keys():
    limiter = RateLimiter(limit=1, window_seconds=60, max_keys=2)
    for key in ("a", "b", "c"):
        limiter.allow(key)
    assert set(limiter._hits) == {"b", "c"}


def test_auth_routes_return_429_when_hammered(tmp_path, google):
    app = build_app(make_settings(tmp_path, auth_rate_limit=3, auth_rate_window=60), google)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/login").status_code == 200
        blocked = client.get("/login")
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "60"
        assert client.get("/auth/google/start", follow_redirects=False).status_code == 429
        assert client.get("/").status_code == 200  # only /login and /auth/* are limited
        assert client.get("/healthz").status_code == 200
