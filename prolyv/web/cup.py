"""The Prolyv cup from the design canvas: porcelain rim, swirling liquor. Colours by tea kind or by the tea's liquor."""

from __future__ import annotations

import re

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

PALETTES = {
    "oolong": (("#FF8A1F", "#FF4B3E", "#FF4FD8", "#FFB347", "#FFC21A", "#FF8A1F"), "#FFC21A", "rgba(110,20,10,0.38)", "#FF4FD8"),
    "black": (("#FF4B3E", "#C4161C", "#FF4FD8", "#FF7A6B", "#FF8A1F", "#FF4B3E"), "#FF8A1F", "rgba(90,0,10,0.45)", "#FF4B3E"),
    "shu": (("#6A1FD0", "#3A0E8C", "#FF4FD8", "#A86BFF", "#2563FF", "#6A1FD0"), "#A86BFF", "rgba(20,0,40,0.55)", "#8A3FFC"),
    "sheng": (("#D6F25C", "#8FAE12", "#FFE066", "#22E3A6", "#B8E23A", "#D6F25C"), "#FFF1A8", "rgba(40,60,0,0.38)", "#B8E23A"),
    "green": (("#22E3A6", "#0E9E73", "#D6F25C", "#5CF2B8", "#22B8FF", "#22E3A6"), "#D6F25C", "rgba(0,60,40,0.35)", "#22E3A6"),
    "white": (("#F4EEFF", "#C7B8FF", "#FFD3F4", "#E9DFFF", "#FFE9B8", "#F4EEFF"), "#FFFFFF", "rgba(80,60,140,0.3)", "#C77DFF"),
    "yellow": (("#FFE066", "#E8A100", "#FFB347", "#FFF1A8", "#D6F25C", "#FFE066"), "#FFF1A8", "rgba(120,70,0,0.35)", "#FFC21A"),
    "heicha": (("#B5713A", "#5A3A22", "#FF8A1F", "#C28E5C", "#7A4A1E", "#B5713A"), "#C28E5C", "rgba(40,20,0,0.5)", "#C28E5C"),
    "tisane": (("#FF4FD8", "#C77DFF", "#22E3A6", "#FF9BEA", "#FFC21A", "#FF4FD8"), "#FFD3F4", "rgba(80,20,80,0.35)", "#FF4FD8"),
}


def _rgb(value: str) -> tuple[int, int, int]:
    n = int(value[1:], 16)
    return (n >> 16) & 255, (n >> 8) & 255, n & 255


def _hex(rgb) -> str:  # noqa: ANN001
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in rgb)


def _mix(value: str, target: tuple[int, int, int], t: float) -> str:
    return _hex(v + (w - v) * t for v, w in zip(_rgb(value), target, strict=True))


def palette_for(kind: str, liquor_hex: str | None = None):  # noqa: ANN201
    if liquor_hex and HEX_RE.match(liquor_hex):
        c = liquor_hex
        white, black = (255, 243, 220), (20, 8, 0)
        stops = (c, _mix(c, black, 0.3), _mix(c, white, 0.35), c, _mix(c, black, 0.15), c)
        return stops, _mix(c, white, 0.55), "rgba(30,10,0,0.35)", c
    return PALETTES.get(kind, PALETTES["oolong"])


def cup_styles(kind: str = "oolong", size: int = 72, liquor_hex: str | None = None, step: int = 2) -> dict:
    stops, core, edge, glow = palette_for(kind, liquor_hex)
    t = (min(8, max(1, step)) - 1) / 12
    pale = (255, 243, 220)
    swirl = ", ".join(_mix(s, pale, t) for s in stops)
    return {
        "size": size,
        "swirl": f"conic-gradient(from 140deg at 50% 50%, {swirl})",
        "core": f"radial-gradient(circle at 50% 50%, {_mix(core, pale, t)} 0, rgba(255,255,255,0) 46%)",
        "gloss": f"radial-gradient(circle at 36% 28%, rgba(255,255,255,0.7) 0, rgba(255,255,255,0) 16%), radial-gradient(circle at 50% 50%, rgba(0,0,0,0) 60%, {edge} 100%)",
        "shadow": f"0 {round(size * 0.09)}px {round(size * 0.18)}px -{round(size * 0.05)}px {glow}AA",
        "rim": f"inset 0 {max(2, round(size * 0.024))}px {max(4, round(size * 0.055))}px rgba(60,30,120,0.25)",
    }
