"""Handle (username) generation: lowercase [a-z0-9_], 3–24 chars, unique with a numeric suffix."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

HANDLE_RE = re.compile(r"^[a-z0-9_]{3,24}$")
MIN_LEN, MAX_LEN = 3, 24

# Routes and words that must never become a profile handle.
RESERVED = frozenset(
    {
        "admin", "administrator", "api", "auth", "blog", "diary", "discuss", "discussions", "feed", "forum",
        "healthz", "help", "login", "logout", "me", "mod", "moderator", "new", "prolyv", "root", "whatsinmygaiwan", "sessions",
        "settings", "static", "support", "system", "tea", "teas", "u", "user", "users", "www",
    }
)

# Enough transliteration for Ukrainian/Russian names to produce a readable ASCII handle.
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ё": "e", "ж": "zh",
    "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "iu", "я": "ia",
}


def slugify(text: str) -> str:
    """Lowercase ASCII slug of a name: 'Olena K.' -> 'olena_k', 'Олена' -> 'olena'. May be empty."""
    text = (text or "").strip().lower()
    text = "".join(_CYRILLIC.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def is_valid_handle(handle: str) -> bool:
    return bool(HANDLE_RE.fullmatch(handle)) and handle not in RESERVED


def generate_handle(preferred: str, is_taken: Callable[[str], bool]) -> str:
    """Turn a username or display name into a free handle. is_taken() consults the database."""
    base = slugify(preferred)
    if not base:
        base = "tea_friend"
    elif len(base) < MIN_LEN:
        base = f"{base}_tea"
    base = base[:MAX_LEN].rstrip("_")

    candidate = base
    suffix = 1
    while not is_valid_handle(candidate) or is_taken(candidate):
        suffix += 1
        tail = str(suffix)
        candidate = f"{base[: MAX_LEN - len(tail)].rstrip('_')}{tail}"
        if suffix > 10_000:  # pragma: no cover — defensive, never expected
            raise RuntimeError("could not find a free handle")
    return candidate
