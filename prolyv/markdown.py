"""Markdown for blog posts: CommonMark + tables, raw HTML dropped, output sanitised with nh3."""

from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True}).enable(
    ["table", "strikethrough", "linkify", "replacements", "smartquotes"]
)

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "s", "del", "a", "ul", "ol", "li", "blockquote", "code", "pre", "h2", "h3", "h4",
    "hr", "img", "table", "thead", "tbody", "tr", "th", "td", "figure", "figcaption",
}
ALLOWED_ATTRS = {"a": {"href", "title", "target"}, "img": {"src", "alt", "title", "loading"}, "th": {"style"}, "td": {"style"}}


def insecure_images(text: str) -> list[str]:
    """Image addresses in the Markdown that the page's content security policy would block."""
    found = []
    for token in _md.parse(text or ""):
        for child in token.children or []:
            if child.type == "image" and str(child.attrGet("src") or "").lower().startswith("http://"):
                found.append(str(child.attrGet("src")))
    return found


def _drop_insecure_image(tag: str, attribute: str, value: str) -> str | None:
    return None if tag == "img" and attribute == "src" and value.lower().startswith("http://") else value


def render_markdown(text: str) -> str:
    html = _md.render(text or "")
    html = html.replace("<h1>", "<h2>").replace("</h1>", "</h2>")  # the page already has the title as h1
    html = html.replace('<a href="http', '<a target="_blank" href="http')
    return nh3.clean(
        html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, url_schemes={"http", "https", "mailto"}, link_rel="noopener nofollow",
        filter_style_properties={"text-align"}, attribute_filter=_drop_insecure_image,
    )
