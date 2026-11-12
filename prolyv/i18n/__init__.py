"""The site's interface in English or Russian.

English text is the key: `_("Log in")` gives "Войти" to a Russian visitor and "Log in" to everyone else, and a string
with no translation yet falls back to English. Values go in after the translation, so the word order can change:
`_("Welcome back, {name}.", name=user.display_name)`. Counts take `_n`, because Russian has three plural forms:
`_n("{n} session", "{n} sessions", count)`.

The visitor's language is set once per request by LanguageMiddleware (prolyv/web/lang.py): the `lang` cookie from the
switcher, or else the first of the browser's languages the site speaks, or else English. It lives in a context variable,
so templates, macros, filters and route code all see it without passing it around.

The Russian text is in the ru_*.py modules of this package, one per part of the site; each has TEXT (English -> Russian),
PLURALS (English singular -> the three Russian forms) and JS (strings the page scripts show, sent to the page as JSON
and read with tr() in app.js). People write their posts, sessions and comments in whatever
language they like, and those are never translated. Tea cards carry their own translations (Tea.i18n).
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from contextvars import ContextVar
from datetime import date, datetime

from markupsafe import Markup, escape

LANGS = ("en", "ru")
DEFAULT_LANG = "en"
LANG_NAMES = {"en": "English", "ru": "Русский"}

_current: ContextVar[str] = ContextVar("ui_lang", default=DEFAULT_LANG)


def _load() -> tuple[dict[str, str], dict[str, tuple[str, str, str]], dict[str, str]]:
    text: dict[str, str] = {}
    plurals: dict[str, tuple[str, str, str]] = {}
    scripts: dict[str, str] = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if not info.name.startswith("ru_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for source, target in (("TEXT", text), ("PLURALS", plurals), ("JS", scripts)):
            for key, value in getattr(module, source, {}).items():
                if key in target and target[key] != value:
                    raise ValueError(f"{info.name}.{source}: {key!r} is already translated differently: {target[key]!r} vs {value!r}")
                target[key] = value
    for key, forms in plurals.items():
        if len(forms) != 3:
            raise ValueError(f"PLURALS[{key!r}] needs three Russian forms (1, 2-4, 5+), got {forms!r}")
    return text, plurals, scripts


RU_TEXT, RU_PLURALS, RU_JS = _load()


def get_lang() -> str:
    return _current.get()


def set_lang(lang: str | None) -> str:
    lang = lang if lang in LANGS else DEFAULT_LANG
    _current.set(lang)
    return lang


def _fill(text: str, values: dict) -> str:
    return text.format(**values) if values else text


def _(text: str, **values) -> str:  # noqa: ANN003
    """Interface text in the visitor's language, with {placeholders} filled after translating."""
    if _current.get() == "ru":
        text = RU_TEXT.get(text, text)
    return _fill(text, values)


def N_(text: str) -> str:  # noqa: N802 - the gettext name
    """Marks text for translation where it is defined (a module-level table); the code that shows it calls _() on it."""
    return text


def ru_plural(n: int) -> int:
    """0 for 1, 21, 101; 1 for 2-4, 22-24; 2 for 0, 5-20, 25-30 and so on."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return 0
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 1
    return 2


def _n(singular: str, plural: str, n: int, **values) -> str:  # noqa: ANN003
    """A count with its noun: _n("{n} reply", "{n} replies", 3) -> "3 replies" / "3 ответа"."""
    values = {"n": n, **values}
    if _current.get() == "ru" and singular in RU_PLURALS:
        return _fill(RU_PLURALS[singular][ru_plural(n)], values)
    return _fill(singular if n == 1 else plural, values)


# In templates the result is Markup: the translation is escaped, and so is every value unless it is Markup itself,
# so a template can pass a bold name in a {% set %} block: {% set who %}<b>{{ name }}</b>{% endset %}.
def template_gettext(text: str, **values) -> Markup:  # noqa: ANN003
    translated = RU_TEXT.get(text, text) if _current.get() == "ru" else text
    return Markup(escape(translated)).format(**values) if values else Markup(escape(translated))


def template_ngettext(singular: str, plural: str, n: int, **values) -> Markup:  # noqa: ANN003
    values = {"n": n, **values}
    if _current.get() == "ru" and singular in RU_PLURALS:
        chosen = RU_PLURALS[singular][ru_plural(n)]
    else:
        chosen = singular if n == 1 else plural
    return Markup(escape(chosen)).format(**values)


def js_text() -> dict[str, str]:
    """What the page scripts need in the visitor's language; empty for English, where tr() returns its argument."""
    return RU_JS if _current.get() == "ru" else {}


def pick_lang(cookie: str | None, accept_language: str | None) -> str:
    """The switcher's choice, else the first browser language the site speaks (uk, ru, en -> ru), else English."""
    if cookie in LANGS:
        return cookie
    ranked: list[tuple[float, int, str]] = []
    for i, part in enumerate((accept_language or "").split(",")[:20]):
        tag, _sep, params = part.strip().partition(";")
        q = 1.0
        match = re.search(r"q=([0-9.]+)", params)
        if match:
            try:
                q = float(match.group(1))
            except ValueError:
                q = 0.0
        ranked.append((-q, i, tag.strip().lower().split("-")[0]))
    for neg_q, _i, base in sorted(ranked):
        if neg_q < 0 and base in LANGS:
            return base
    return DEFAULT_LANG


# ------------------------------------------------------------------------------------------- dates

MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
MONTHS_RU_ALONE = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
MONTHS_RU_SHORT = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
WEEKDAYS_RU_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
# Russian puts the day first: "Nov 12, 2026" reads "12 ноя 2026"
RU_ORDER = (("%b %-d, %Y", "%-d %b %Y"), ("%B %-d, %Y", "%-d %B %Y"), ("%b %-d", "%-d %b"), ("%B %-d", "%-d %B"))


def format_date(moment: date | datetime | None, fmt: str = "%-d %b %Y") -> str:
    """strftime with month and weekday names in the visitor's language. Use it for every date a person reads."""
    if moment is None:
        return ""
    if _current.get() != "ru":
        return moment.strftime(fmt)
    for english, russian in RU_ORDER:
        fmt = fmt.replace(english, russian)
    with_day = "%d" in fmt or "%-d" in fmt
    month = moment.month - 1
    names = {
        "%B": (MONTHS_RU if with_day else MONTHS_RU_ALONE)[month], "%b": MONTHS_RU_SHORT[month],
        "%A": WEEKDAYS_RU[moment.weekday()], "%a": WEEKDAYS_RU_SHORT[moment.weekday()],
    }
    # the names may hold no % of their own, so swap them in around strftime's work
    parts = re.split(r"(%[BbAa])", fmt)
    return "".join(names[p] if p in names else (moment.strftime(p) if p else "") for p in parts)
