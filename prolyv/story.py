"""The 1080x1920 story card for a session: PNG drawn with Pillow, fonts from prolyv/fonts (SIL OFL)."""

from __future__ import annotations

import math
import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from prolyv.models import TeaSession
from prolyv.web.cup import palette_for

FONTS = Path(__file__).resolve().parent / "fonts"
WIDTH, HEIGHT = 1080, 1920
MARGIN = 90
# Latin + Cyrillic glyphs exist in both fonts; other scripts are left out rather than drawn as empty boxes
UNSUPPORTED = re.compile(r"[^ -ɏЀ-ӿ–—‘’“”…°·]")


def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)
    font.set_variation_by_axes([weight])
    return font


def _rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def _clean(text: str | None) -> str:
    return " ".join(UNSUPPORTED.sub("", text or "").split())


def _cup(size: int, stops: tuple[str, ...], core: str) -> Image.Image:
    """The brand cup: porcelain rim and a conic swirl of liquor colours. Drawn small and scaled, it is smooth anyway."""
    n = 320
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    px = image.load()
    radius, inner = n / 2, n / 2 * 0.74
    rgb_stops, core_rgb = [_rgb(s) for s in stops], _rgb(core)
    for y in range(n):
        for x in range(n):
            dx, dy = x + 0.5 - radius, y + 0.5 - radius
            d = math.hypot(dx, dy)
            if d > radius:
                continue
            if d > inner:
                shade = 1 - 0.18 * max(0, (dx + dy) / (2 * radius))
                px[x, y] = (int(248 * shade), int(244 * shade), int(255 * shade), 255)
                continue
            seg = (((math.atan2(dy, dx) + math.pi * 0.72) / (2 * math.pi)) % 1) * (len(stops) - 1)
            i = int(seg)
            c = [a + (b - a) * (seg - i) for a, b in zip(rgb_stops[i], rgb_stops[i + 1], strict=True)]
            mix = max(0, 1 - d / (inner * 0.46)) * 0.9
            c = [v + (w - v) * mix for v, w in zip(c, core_rgb, strict=True)]
            gloss = max(0, 1 - math.hypot(dx / inner + 0.28, dy / inner + 0.44) / 0.32) * 0.7
            c = [v + (255 - v) * gloss for v in c]
            px[x, y] = (int(c[0]), int(c[1]), int(c[2]), 255)
    return image.resize((size, size), Image.LANCZOS)


def _background(glow: str) -> Image.Image:
    base = Image.new("RGB", (WIDTH, HEIGHT), (7, 8, 26))
    layer = Image.new("RGB", (WIDTH // 8, HEIGHT // 8), (7, 8, 26))
    draw = ImageDraw.Draw(layer)
    for (cx, cy, r), color in zip(((120, 40, 90), (20, 190, 90), (120, 215, 70)), (_rgb(glow), (37, 99, 255), (255, 138, 31)), strict=True):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(int(v * 0.55) for v in color))
    layer = layer.filter(ImageFilter.GaussianBlur(26)).resize((WIDTH, HEIGHT), Image.BICUBIC)
    return Image.blend(base, layer, 0.8)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    words = text.split()
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(",.;: ") + "…"
    return lines


def _star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill: tuple) -> None:
    points = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.42
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=fill)


def render_story(sess: TeaSession, site: str) -> bytes:
    kind = sess.tea.category.value if sess.tea else "oolong"
    stops, core, _edge, glow = palette_for(kind, sess.tea.liquor_hex if sess.tea else None)
    image = _background(glow)
    draw = ImageDraw.Draw(image)
    width = WIDTH - 2 * MARGIN

    y = 270  # Instagram and TikTok cover the top and bottom of a story with their own interface
    draw.text((MARGIN, y), "PROLYV · TEA DIARY", font=_font("Manrope", 34, 800), fill=(220, 200, 255))
    y += 90
    cup_size = 560
    cup = _cup(cup_size, stops, core)
    image.paste(cup, ((WIDTH - cup_size) // 2, y), cup)
    y += cup_size + 70

    title_font = _font("Unbounded", 78, 800)
    title = _clean(sess.tea_name) or "Tea session"
    lines = _wrap(draw, title, title_font, width, 3)
    if len(lines) == 3:
        title_font = _font("Unbounded", 62, 800)
        lines = _wrap(draw, title, title_font, width, 3)
    for line in lines:
        draw.text((MARGIN, y), line, font=title_font, fill=(255, 255, 255))
        y += int(title_font.size * 1.18)
    y += 20

    if sess.rating:
        for i in range(5):
            _star(draw, MARGIN + 30 + i * 70, y + 30, 30, (255, 194, 26) if i < sess.rating else (70, 72, 110))
        y += 90

    params = _clean(sess.params_line)
    if params:
        for line in _wrap(draw, params, _font("Manrope", 40, 700), width, 2):
            draw.text((MARGIN, y), line, font=_font("Manrope", 40, 700), fill=(215, 217, 242))
            y += 54
        y += 12

    if sess.steeps:
        steeps = "Steeps: " + " · ".join(f"{s.seconds}s" for s in sess.steeps[:10])
        for line in _wrap(draw, steeps, _font("Manrope", 34, 600), width, 2):
            draw.text((MARGIN, y), line, font=_font("Manrope", 34, 600), fill=(201, 204, 235))
            y += 46
        y += 12

    room = max(0, (HEIGHT - 400 - y) // 46)
    note = _clean((sess.note or "").split("\n\n")[0])
    if note and room:
        for line in _wrap(draw, note, _font("Manrope", 34, 500), width, min(4, room)):
            draw.text((MARGIN, y), line, font=_font("Manrope", 34, 500), fill=(230, 231, 250))
            y += 46

    foot = HEIGHT - 340
    handle = "" if sess.user.deleted_at else f"@{sess.user.handle}"
    draw.text((MARGIN, foot), _clean(handle), font=_font("Manrope", 38, 800), fill=(255, 255, 255))
    domain = _font("Manrope", 34, 600)
    draw.text((WIDTH - MARGIN - draw.textlength(site, font=domain), foot + 4), site, font=domain, fill=(255, 179, 240))

    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
