"""The interface in English or Russian: picking the language, the switcher, and a translation for every marked string."""

from __future__ import annotations

import ast
import string
from datetime import date, datetime
from pathlib import Path

import pytest
from jinja2 import nodes

from prolyv.i18n import RU_JS, RU_PLURALS, RU_TEXT, _, _n, format_date, pick_lang, ru_plural, set_lang
from prolyv.web.templating import jinja_env
from tests.conftest import telegram_login

PACKAGE = Path(__file__).resolve().parent.parent / "prolyv"
TEMPLATES = PACKAGE / "templates"
STATIC = PACKAGE / "static"


@pytest.fixture(autouse=True)
def english_after():
    yield
    set_lang("en")


def template_strings() -> list[tuple[str, int, str, str | tuple]]:
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        tree = jinja_env.parse(path.read_text(encoding="utf-8"))
        for node in tree.find_all(nodes.Call):
            if isinstance(node.node, nodes.Name) and node.node.name in ("_", "_n"):
                args = [a.value if isinstance(a, nodes.Const) and isinstance(a.value, str) else None for a in node.args[:2]]
                if node.node.name == "_" and args[0] is None:
                    continue  # _(section.name): stored English, marked with N_() where it is defined
                message = args[0] if node.node.name == "_" else tuple(args)
                found.append((str(path.relative_to(PACKAGE)), node.lineno, node.node.name, message))
    return found


def python_strings() -> list[tuple[str, int, str, str | tuple]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "i18n" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_", "_n", "N_"):
                args = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None for a in node.args[:2]]
                if node.func.id == "_" and args[0] is None:
                    continue  # _(variable): a table marked with N_() where it is defined
                message = tuple(args) if node.func.id == "_n" else args[0]
                found.append((str(path.relative_to(PACKAGE)), node.lineno, "_" if node.func.id == "N_" else node.func.id, message))
    return found


def placeholders(text: str) -> set[str]:
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name is not None}


def test_every_marked_string_has_russian():
    missing = []
    for where, line, func, message in template_strings() + python_strings():
        if func == "_":
            if not isinstance(message, str):
                missing.append(f"{where}:{line} _() needs a literal string")
            elif message not in RU_TEXT:
                missing.append(f"{where}:{line} {message!r}")
        else:
            if not isinstance(message, tuple) or not all(isinstance(m, str) for m in message[:2]):
                missing.append(f"{where}:{line} _n() needs literal strings")
            elif message[0] not in RU_PLURALS:
                missing.append(f"{where}:{line} plural {message[0]!r}")
    assert not missing, "No Russian for:\n" + "\n".join(missing)


def test_translations_keep_their_placeholders():
    wrong = [k for k, v in RU_TEXT.items() if placeholders(k) != placeholders(v)]
    wrong += [k for k, forms in RU_PLURALS.items() for v in forms if placeholders(v) - placeholders(k) - {"n"}]
    wrong += [k for k, v in RU_JS.items() if isinstance(v, str) and placeholders(k) != placeholders(v)]
    assert not wrong, wrong


def test_scripts_ask_only_for_known_strings():
    import re

    asked = set()
    for path in STATIC.glob("*.js"):
        if path.name.endswith(".min.js"):
            continue
        asked |= set(re.findall(r'\btrn?\("((?:[^"\\]|\\.)*)"', path.read_text(encoding="utf-8")))
    assert not asked - set(RU_JS), sorted(asked - set(RU_JS))


def test_russian_plural_forms():
    assert [ru_plural(n) for n in (0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111)] == [2, 0, 1, 1, 2, 2, 2, 2, 0, 1, 2, 0, 2]
    set_lang("ru")
    assert _n("{n} day ago", "{n} days ago", 1) == "1 день назад"
    assert _n("{n} day ago", "{n} days ago", 3) == "3 дня назад"
    assert _n("{n} day ago", "{n} days ago", 6) == "6 дней назад"
    set_lang("en")
    assert _n("{n} day ago", "{n} days ago", 1) == "1 day ago" and _n("{n} day ago", "{n} days ago", 6) == "6 days ago"


def test_fallback_and_values():
    set_lang("ru")
    assert _("Log in") == "Войти"
    assert _("A sentence nobody translated, {x}", x="ok") == "A sentence nobody translated, ok"


def test_dates_in_russian():
    moment = datetime(2026, 11, 12, 9, 30)
    assert format_date(moment, "%b %-d, %Y") == "Nov 12, 2026"
    set_lang("ru")
    assert format_date(moment, "%-d %b %Y") == "12 ноя 2026"
    assert format_date(moment, "%b %-d, %Y") == "12 ноя 2026"
    assert format_date(moment, "%-d %B") == "12 ноября"
    assert format_date(date(2026, 5, 1), "%B %Y") == "май 2026"


def test_browser_language():
    assert pick_lang(None, None) == "en"
    assert pick_lang(None, "ru-RU,ru;q=0.9,en;q=0.8") == "ru"
    assert pick_lang(None, "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7") == "ru"
    assert pick_lang(None, "de-DE,de;q=0.9,en;q=0.8,ru;q=0.5") == "en"
    assert pick_lang(None, "en;q=0.5,ru;q=0.9") == "ru"
    assert pick_lang(None, "ru;q=0") == "en"
    assert pick_lang("en", "ru") == "en" and pick_lang("ru", "en") == "ru" and pick_lang("fr", "ru") == "ru"


def test_page_follows_the_browser(client):
    page = client.get("/about", headers={"accept-language": "ru-RU,ru;q=0.9"})
    assert '<html lang="ru">' in page.text and "Лента" in page.text and page.headers["content-language"] == "ru"
    assert "Accept-Language" in page.headers["vary"]
    page = client.get("/about")
    assert '<html lang="en">' in page.text and ">Feed<" in page.text and page.headers["content-language"] == "en"


def test_switcher_sets_cookie_and_goes_back(client):
    page = client.get("/teas?q=sencha")
    assert 'href="/lang/ru?next=/teas%3Fq%3Dsencha"' in page.text
    response = client.get("/lang/ru", params={"next": "/teas?q=sencha"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/teas?q=sencha"
    assert "lang=ru" in response.headers["set-cookie"] and "Max-Age=31536000" in response.headers["set-cookie"]
    page = client.get("/about", headers={"accept-language": "en"})
    assert '<html lang="ru">' in page.text and ">Лента<" in page.text
    assert 'href="/lang/en?next=/about"' in page.text
    client.get("/lang/en")
    assert '<html lang="en">' in client.get("/about", headers={"accept-language": "ru"}).text


def test_switcher_stays_on_this_site(client):
    for target in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
        response = client.get("/lang/ru", params={"next": target}, follow_redirects=False)
        assert response.headers["location"] == "/"
    response = client.get("/lang/ru", headers={"referer": "https://evil.example/x"}, follow_redirects=False)
    assert response.headers["location"] == "/"
    response = client.get("/lang/ru", headers={"referer": "http://testserver/blog?page=2"}, follow_redirects=False)
    assert response.headers["location"] == "/blog?page=2"
    assert client.get("/lang/fr", follow_redirects=False).status_code == 404


def test_error_pages_and_scripts_in_russian(client):
    client.cookies.set("lang", "ru")
    page = client.get("/no-such-page-at-all/really")
    assert page.status_code == 404 and '<html lang="ru">' in page.text
    assert 'id="ui-text"' in page.text and '"Link copied": "\\u0421' in page.text
    client.cookies.set("lang", "en")
    assert 'id="ui-text"' not in client.get("/about").text


def test_one_visitor_does_not_change_another(client, app):
    from starlette.testclient import TestClient

    russian = TestClient(app, cookies={"lang": "ru"})
    assert '<html lang="ru">' in russian.get("/about").text
    assert '<html lang="en">' in client.get("/about").text
    assert '<html lang="ru">' in russian.get("/teas").text


def test_signed_in_shell_in_russian(client):
    telegram_login(client)
    client.cookies.set("lang", "ru")
    page = client.get("/settings")
    assert ">Выйти<" in page.text and ">Мой дневник<" in page.text
