"""Markdown for blog posts: CommonMark + tables, raw HTML dropped, output sanitised with bleach."""

from __future__ import annotations

import bleach
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True}).enable("table").enable("strikethrough")

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "s", "del", "a", "ul", "ol", "li", "blockquote", "code", "pre", "h2", "h3", "h4",
    "hr", "img", "table", "thead", "tbody", "tr", "th", "td", "figure", "figcaption",
}
ALLOWED_ATTRS = {"a": ["href", "title", "rel", "target"], "img": ["src", "alt", "title", "loading"], "th": ["align"], "td": ["align"]}


def render_markdown(text: str) -> str:
    html = _md.render(text or "")
    html = html.replace("<h1>", "<h2>").replace("</h1>", "</h2>")  # the page already has the title as h1
    cleaned = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=["http", "https", "mailto"], strip=True)
    return bleach.linkify(cleaned, callbacks=[_external_links], skip_tags=["pre", "code"])


def _external_links(attrs, new=False):  # noqa: ANN001
    href = attrs.get((None, "href"), "")
    if href.startswith(("http://", "https://")):
        attrs[(None, "rel")] = "noopener"
        attrs[(None, "target")] = "_blank"
    return attrs
