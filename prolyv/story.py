"""The 1080x1920 story cards: PNGs drawn with Pillow, fonts from prolyv/fonts (SIL OFL). Same layout as the Story canvas, at twice its size."""

from __future__ import annotations

import math
import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from prolyv.brand import SITE_NAME
from prolyv.models import CATEGORY_LABELS, TeaSession, User
from prolyv.web.cup import palette_for

FONTS = Path(__file__).resolve().parent / "fonts"
WIDTH, HEIGHT = 1080, 1920
MARGIN = 90
TOP = 200  # Instagram and TikTok cover the top and bottom of a story with their own interface
FOOT = HEIGHT - 300
# Latin + Cyrillic glyphs exist in both fonts; other scripts are left out rather than drawn as empty boxes
UNSUPPORTED = re.compile(r"[^ -ɏЀ-ӿ–—‘’“”…°·]")
PARAGRAPH = re.compile(r"\n\s*\n")
PILLS = (((255, 79, 216), 0.22, (255, 211, 244)), ((138, 63, 252), 0.3, (228, 214, 255)), ((255, 194, 26), 0.22, (255, 231, 168)),
         ((34, 227, 166), 0.18, (157, 245, 211)), ((255, 138, 31), 0.2, (255, 201, 138)))
KICKER_NAMES = {"Black": "BLACK TEA"}
MARK_STOPS = ("#FF4FD8", "#8A3FFC", "#2563FF", "#22E3A6", "#FFC21A", "#FF8A1F", "#FF4FD8")


def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)
    font.set_variation_by_axes([weight])
    return font


def _rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def _clean(text: str | None) -> str:
    """One line of drawable text: every run of whitespace, line breaks included, becomes a single space."""
    spaced = " ".join((text or "").split())  # first, or the line breaks would vanish and join the words around them
    return " ".join(UNSUPPORTED.sub("", spaced).split())


def first_paragraph(note: str | None) -> str:
    plain = (note or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return _clean(PARAGRAPH.split(plain, maxsplit=1)[0])


def _disc(size: int, share: float = 1.0) -> Image.Image:
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    pad = big * (1 - share) / 2
    ImageDraw.Draw(mask).ellipse((pad, pad, big - pad, big - pad), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def _cup(size: int, stops: tuple[str, ...], core: str) -> Image.Image:
    """The brand cup: porcelain rim and a conic swirl of liquor colours, with smooth anti-aliased edges."""
    n = 240
    swirl = Image.new("RGB", (n, n))
    px = swirl.load()
    centre, inner = n / 2, n / 2
    rgb_stops, core_rgb = [_rgb(s) for s in stops], _rgb(core)
    for y in range(n):
        for x in range(n):
            dx, dy = x + 0.5 - centre, y + 0.5 - centre
            d = math.hypot(dx, dy)
            seg = (((math.atan2(dy, dx) + math.pi * 0.72) / (2 * math.pi)) % 1) * (len(stops) - 1)
            i = int(seg)
            c = [a + (b - a) * (seg - i) for a, b in zip(rgb_stops[i], rgb_stops[i + 1], strict=True)]
            mix = max(0, 1 - d / (inner * 0.46)) * 0.9
            c = [v + (w - v) * mix for v, w in zip(c, core_rgb, strict=True)]
            gloss = max(0, 1 - math.hypot(dx / inner + 0.28, dy / inner + 0.44) / 0.32) * 0.7
            c = [v + (255 - v) * gloss for v in c]
            edge = max(0, (d / inner - 0.6) / 0.4) * 0.28
            px[x, y] = tuple(int(v * (1 - edge)) for v in c)
    rim = Image.linear_gradient("L").rotate(45, expand=False).resize((size, size), Image.BICUBIC)
    rim = Image.merge("RGB", [rim.point(lambda v, top=t: int(top - (255 - v) * 0.16)) for t in (255, 248, 255)])
    cup = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cup.paste(rim, (0, 0), _disc(size, 1.0))
    cup.paste(swirl.resize((size, size), Image.BICUBIC), (0, 0), _disc(size, 0.74))
    return cup


def _mark(size: int) -> Image.Image:
    """The logo: a conic rainbow disc with a soft highlight."""
    n = size * 2
    art = Image.new("RGB", (n, n))
    px = art.load()
    stops = [_rgb(s) for s in MARK_STOPS]
    for y in range(n):
        for x in range(n):
            dx, dy = x + 0.5 - n / 2, y + 0.5 - n / 2
            turn = ((math.atan2(dx, -dy) - math.radians(200)) / (2 * math.pi)) % 1  # clockwise from the top, as CSS conic-gradient
            seg = turn * (len(stops) - 1)
            i = min(int(seg), len(stops) - 2)
            c = [a + (b - a) * (seg - i) for a, b in zip(stops[i], stops[i + 1], strict=True)]
            shine = max(0, 1 - math.hypot(x / n - 0.3, y / n - 0.28) / 0.28) * 0.9
            px[x, y] = tuple(int(v + (255 - v) * shine) for v in c)
    mark = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mark.paste(art.resize((size, size), Image.LANCZOS), (0, 0), _disc(size))
    return mark


def _background() -> Image.Image:
    """linear-gradient(170deg, #2A1478, #070A2A 70%) under pink, orange and blue glows, drawn small and enlarged smoothly."""
    w, h = WIDTH // 8, HEIGHT // 8
    glows = (((0.5, 0.34), (0.7, 0.4), (255, 79, 216), 0.5), ((1.0, 1.0), (0.6, 0.3), (255, 138, 31), 0.5), ((0.0, 0.0), (0.6, 0.3), (37, 99, 255), 0.5))
    top, bottom = (42, 20, 120), (7, 10, 42)
    art = Image.new("RGB", (w, h))
    px = art.load()
    for y in range(h):
        fy = y / h
        for x in range(w):
            fx = x / w
            t = min(1.0, (fy + (fx - 0.5) * 0.1) / 0.7)
            c = [a + (b - a) * max(0.0, t) for a, b in zip(top, bottom, strict=True)]
            for (cx, cy), (rx, ry), color, alpha in glows:
                reach = max(0.0, 1 - math.hypot((fx - cx) / rx, (fy - cy) / ry) / 0.7) * alpha
                c = [v + (g - v) * reach for v, g in zip(c, color, strict=True)]
            px[x, y] = tuple(int(v) for v in c)
    return art.resize((WIDTH, HEIGHT), Image.BICUBIC)


def _hard_break(draw: ImageDraw.ImageDraw, word: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """A word wider than the card is cut into pieces that fit."""
    pieces: list[str] = []
    piece = ""
    for char in word:
        if piece and draw.textlength(piece + char, font=font) > width:
            pieces.append(piece)
            piece = char
        else:
            piece += char
    if piece:
        pieces.append(piece)
    return pieces


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
        if draw.textlength(word, font=font) > width:
            pieces = _hard_break(draw, word, font, width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1].rstrip(",.;: ")
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _fill_shape(image: Image.Image, box: tuple[int, int, int, int], paint, color: tuple, alpha: float = 1.0) -> None:  # noqa: ANN001
    """Paints a smooth shape: drawn four times larger on a mask, then scaled down."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w * 4, h * 4), 0)
    paint(ImageDraw.Draw(mask), w * 4, h * 4)
    mask = mask.resize((w, h), Image.LANCZOS).point(lambda v: int(v * alpha))
    image.paste(color, (x0, y0), mask)


def _pill(image: Image.Image, box: tuple[int, int, int, int], color: tuple, alpha: float) -> None:
    _fill_shape(image, box, lambda d, w, h: d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=255), color, alpha)


def _outline_pill(image: Image.Image, box: tuple[int, int, int, int], color: tuple, alpha: float, line: int = 2) -> None:
    def paint(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, outline=255, width=line * 4)

    _fill_shape(image, box, paint, color, alpha)


def _star(image: Image.Image, cx: int, cy: int, r: int, color: tuple) -> None:
    def paint(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
        points = []
        for i in range(10):
            radius = (w / 2) if i % 2 == 0 else (w / 2) * 0.42
            angle = -math.pi / 2 + i * math.pi / 5
            points.append((w / 2 + radius * math.cos(angle), h / 2 + radius * math.sin(angle)))
        draw.polygon(points, fill=255)

    _fill_shape(image, (cx - r, cy - r, cx + r, cy + r), paint, color)


def _centered(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.FreeTypeFont, fill: tuple) -> None:
    draw.text(((WIDTH - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)


def _title_lines(draw: ImageDraw.ImageDraw, title: str, width: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for size, most in ((76, 2), (60, 3)):
        font = _font("Unbounded", size, 800)
        lines = _wrap(draw, title, font, width, 99)
        if len(lines) <= most:
            return font, lines
    return font, _wrap(draw, title, font, width, 3)


def _fitting_pills(draw: ImageDraw.ImageDraw, tags: list[str], font: ImageFont.FreeTypeFont, width: int) -> list[tuple[str, int]]:
    """As many tags as fit on one centred row: (label, pill width)."""
    row: list[tuple[str, int]] = []
    used = 0
    for tag in tags[:5]:
        label = _clean(tag).capitalize()
        pill = int(draw.textlength(label, font=font)) + 56
        if used + pill > width:
            break
        row.append((label, pill))
        used += pill + 16
    return row


def _compose(*, badge: str, kind: str, liquor_hex: str | None, kicker: str, title: str, byline: str, rating: int | None,
             tags: list[str], quote: str, foot_left: str, site: str) -> bytes:
    stops, core, _edge, _glow = palette_for(kind, liquor_hex)
    image = _background()
    draw = ImageDraw.Draw(image)
    width = WIDTH - 2 * MARGIN

    kicker_font = _font("Manrope", 28, 800)
    by_font = _font("Manrope", 34, 500)
    pill_font = _font("Manrope", 28, 800)
    quote_font = _font("Manrope", 36, 500)
    title_font, title_lines = _title_lines(draw, title, width)
    title_height = int(title_font.size * 1.18) * len(title_lines)
    quote_lines = _wrap(draw, f"“{quote}”", quote_font, width, 3) if quote else []
    if quote_lines and not quote_lines[-1].endswith("”"):
        quote_lines[-1] += "”"
    pills = _fitting_pills(draw, tags, pill_font, width)

    # Everything but the cup has a known height, so the cup takes what is left, up to the canvas's size.
    text_height = 44 + title_height + (52 if byline else 0) + (84 if rating else 0) + (80 if pills else 0) + 52 * len(quote_lines)
    gaps = 28 + 28 + 36
    cup_size = max(280, min(600, FOOT - TOP - 76 - text_height - gaps))

    # logo and badge
    y = TOP
    image.paste(mark := _mark(56), (MARGIN, y + 6), mark)
    badge_font = _font("Manrope", 26, 800)
    badge_w = int(draw.textlength(badge, font=badge_font)) + 56
    badge_box = (WIDTH - MARGIN - badge_w, y + 4, WIDTH - MARGIN, y + 64)
    room = badge_box[0] - 24 - (MARGIN + 76)
    size = 40
    while size > 22 and draw.textlength(SITE_NAME, font=_font("Unbounded", size, 700)) > room:
        size -= 2
    draw.text((MARGIN + 76, y + 6 + (40 - size) // 2), SITE_NAME, font=_font("Unbounded", size, 700), fill=(255, 255, 255))
    _outline_pill(image, badge_box, (255, 255, 255), 0.32)
    draw.text((badge_box[0] + 28, y + 17), badge, font=badge_font, fill=(255, 255, 255))
    y += 76 + 28

    cup = _cup(cup_size, stops, core)
    image.paste(cup, ((WIDTH - cup_size) // 2, y), cup)
    y += cup_size + 28

    if kicker:
        _centered(draw, y, kicker, kicker_font, (255, 201, 138))
    y += 44
    for line in title_lines:
        _centered(draw, y, line, title_font, (255, 255, 255))
        y += int(title_font.size * 1.18)
    if byline:
        y += 8
        _centered(draw, y, byline, by_font, (215, 217, 242))
        y += 44
    if rating:
        y += 22
        for i in range(5):
            cx = WIDTH // 2 + (i - 2) * 72
            _star(image, cx, y + 26, 26, (255, 194, 26) if i < rating else (86, 84, 130))
        y += 62
    if pills:
        y += 20
        x = (WIDTH - (sum(p for _, p in pills) + 16 * (len(pills) - 1))) // 2
        for i, (label, pill) in enumerate(pills):
            color, alpha, fg = PILLS[i % len(PILLS)]
            _pill(image, (x, y, x + pill, y + 56), color, alpha)
            draw.text((x + 28, y + 11), label, font=pill_font, fill=fg)
            x += pill + 16
        y += 60
    if quote_lines:
        y += 28
        for line in quote_lines:
            _centered(draw, y, line, quote_font, (237, 235, 255))
            y += 52

    # footer
    draw.line((MARGIN, FOOT, WIDTH - MARGIN, FOOT), fill=(92, 84, 138), width=2)
    site_font = _font("Manrope", 28, 800)
    site_w = int(draw.textlength(site, font=site_font))
    draw.text((WIDTH - MARGIN - site_w, FOOT + 28), site, font=site_font, fill=(255, 255, 255))
    left = _wrap(draw, foot_left, _font("Manrope", 28, 500), width - site_w - 32, 1) if foot_left else []
    if left:
        draw.text((MARGIN, FOOT + 28), left[0], font=_font("Manrope", 28, 500), fill=(201, 204, 235))

    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _session_foot(sess: TeaSession) -> str:
    bits = []
    if sess.grams:
        bits.append(f"{sess.grams:g} g")
    if sess.temp_c:
        bits.append(f"{sess.temp_c} °C")
    if sess.vessel:
        bits.append(sess.vessel)
    return _clean(" · ".join(bits))


def _session_kicker(sess: TeaSession) -> str:
    bits = []
    if sess.tea:
        label = CATEGORY_LABELS[sess.tea.category]
        bits.append(KICKER_NAMES.get(label, label.upper()))
    count = len(sess.steeps)
    if count:
        bits.append(f"{count} STEEP{'' if count == 1 else 'S'}")
    return " · ".join(bits)


def render_story(sess: TeaSession, site: str) -> bytes:
    return _compose(
        badge="my tea of the day", kind=sess.category_key, liquor_hex=sess.tea.liquor_hex if sess.tea else None, kicker=_session_kicker(sess),
        title=_clean(sess.tea_name) or "Tea session", byline="" if sess.user.deleted_at else _clean(f"by @{sess.user.handle}"), rating=sess.rating,
        tags=list(sess.taste_tags or []), quote=first_paragraph(sess.note), foot_left=_session_foot(sess), site=site,
    )


def render_month_story(user: User, stats: dict, site: str) -> bytes:
    """The last 30 days as a card, from the same numbers the diary hero shows."""
    count = stats["sessions"]
    favourite = stats["favourite"]
    foot = [f"{stats['new_teas']} new tea{'' if stats['new_teas'] == 1 else 's'}"]
    if stats["avg_rating"]:
        foot.append(f"average {stats['avg_rating']} of 5")
    return _compose(
        badge="my last 30 days", kind=favourite["key"] if favourite else "oolong", liquor_hex=None,
        kicker=f"FAVOURITE: {KICKER_NAMES.get(favourite['label'], favourite['label'].upper())}" if favourite else "",
        title=f"{count} cup{'' if count == 1 else 's'} in 30 days", byline=_clean(f"by @{user.handle}"), rating=None,
        tags=stats["month_notes"], quote="", foot_left=" · ".join(foot), site=site,
    )
