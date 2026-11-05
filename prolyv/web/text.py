"""Small text helpers for templates."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import unquote, urlsplit

from markupsafe import Markup, escape

from prolyv.models import utcnow

LINK = re.compile(r"(https?://[^\s<>\"']+)|(?<![\w@/])@([a-z0-9_]{3,24})\b", re.IGNORECASE)


def linkify(text: str | None) -> Markup:
    """Escapes user text, then makes pasted links and @mentions clickable. Nothing else in the text becomes HTML."""
    out: list[str] = []
    last = 0
    raw = text or ""
    for match in LINK.finditer(raw):
        out.append(escape(raw[last:match.start()]))
        if match.group(1):
            url = match.group(1).rstrip(".,;:!?)")
            trailing = match.group(1)[len(url):]
            out.append(Markup('<a href="{0}" rel="nofollow ugc noopener" target="_blank">{0}</a>').format(url))
            out.append(escape(trailing))
        else:
            handle = match.group(2).lower()
            out.append(Markup('<a href="/u/{0}">@{1}</a>').format(handle, match.group(2)))
        last = match.end()
    out.append(escape(raw[last:]))
    return Markup("").join(out)


def source_label(url: str | None) -> str:
    """'https://zh.wikipedia.org/wiki/%E5%A4%A7...' -> 'Wikipedia (zh): 大红袍'; anything else is just its domain."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").removeprefix("www.")
    if host.endswith(".wikipedia.org") and parts.path.startswith("/wiki/"):
        title = unquote(parts.path.removeprefix("/wiki/")).replace("_", " ")
        return f"Wikipedia ({host.split('.')[0]}): {title}"
    return host or (url or "")


def time_ago(moment: datetime | None) -> str:
    if moment is None:
        return ""
    seconds = int((utcnow() - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 86400 * 7:
        days = seconds // 86400
        return f"{days} day{'' if days == 1 else 's'} ago"
    return moment.strftime("%-d %b") if moment.year == utcnow().year else moment.strftime("%-d %b %Y")


MENTION = re.compile(r"(?<![\w@/])@([a-z0-9_]{3,24})\b", re.IGNORECASE)


def mentions(text: str | None, limit: int = 5) -> list[str]:
    return list(dict.fromkeys(handle.lower() for handle in MENTION.findall(text or "")))[:limit]
