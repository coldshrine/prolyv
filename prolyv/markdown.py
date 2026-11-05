"""Markdown for blog posts: CommonMark + tables, raw HTML dropped, output sanitised with nh3."""

from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True}).enable(["table", "strikethrough", "linkify"])

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "s", "del", "a", "ul", "ol", "li", "blockquote", "code", "pre", "h2", "h3", "h4",
    "hr", "img", "table", "thead", "tbody", "tr", "th", "td", "figure", "figcaption",
}
ALLOWED_ATTRS = {"a": {"href", "title", "target"}, "img": {"src", "alt", "title", "loading"}, "th": {"align"}, "td": {"align"}}


def render_markdown(text: str) -> str:
    html = _md.render(text or "")
    html = html.replace("<h1>", "<h2>").replace("</h1>", "</h2>")  # the page already has the title as h1
    html = html.replace('<a href="http', '<a target="_blank" href="http')
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, url_schemes={"http", "https", "mailto"}, link_rel="noopener nofollow")
