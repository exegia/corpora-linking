"""Render a recorded Rich console into the project's terminal-shot SVG.

Type, spacing and chrome are the Sketch source of truth (`exegia-ui` → page
"Corpora" → frame `corpora-help`): Google Sans Code 15 px on a 9 × 18 grid,
38 px gutters, and a 13 px-radius ``#262626`` window under a macOS title bar.

Glyphs are emitted as outlines rather than ``<text>``. GitHub renders README
images in an ``<img>`` sandbox that blocks web fonts, so a font referenced by
name falls back to whatever the viewer happens to have; outlines render
identically everywhere, at the cost of the shots no longer being selectable
text. Every distinct glyph is written once into ``<defs>`` and re-used.

Colour emoji have no outlines to take — they come from the platform's bitmap
emoji font (Apple Color Emoji, at the smallest strike that survives a 2×
render) embedded as PNG, which is what the terminal being photographed
draws. Where that font is missing (any non-macOS machine) the character
degrades to a ``<text>`` element so the shot still renders.
"""

from __future__ import annotations

import base64
import os
from functools import cache, lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTCollection, TTFont
from rich.cells import cell_len
from rich.console import Console
from rich.segment import Segment
from rich.style import Style
from rich.terminal_theme import TerminalTheme

# ── the design ───────────────────────────────────────────────────────────────

FONT_SIZE = 15
ADVANCE = 9.0  # Google Sans Code is a 0.6-em monospace: 15 × 0.6
LINE_HEIGHT = 18
BASELINE = 14  # from the cell's top edge

PAD_LEFT = 38
PAD_RIGHT = 38
PAD_TOP = 65  # window top → first cell top (the title bar lives in here)
PAD_BOTTOM = 39

MARGIN = 2  # room for the border, which straddles the window edge
RADIUS = 13
BACKGROUND = "#262626"
BORDER = "#ABABAB"
BORDER_WIDTH = 3

TITLE_SIZE = 14
TITLE_BASELINE = 30
TITLE_OPACITY = 0.65
LIGHTS = (("#FF5F57", 27), ("#FEBC2E", 49), ("#28C840", 71))
LIGHT_RADIUS = 7
LIGHT_CY = 25

DIM_OPACITY = 0.6  # the design's treatment of secondary text

# White on near-black, with the semantic colours the CLI actually prints.
THEME = TerminalTheme(
    (0x26, 0x26, 0x26),
    (0xFF, 0xFF, 0xFF),
    [
        (0x3A, 0x3A, 0x3A),
        (0xE9, 0x56, 0x56),
        (0x5B, 0xC7, 0x78),
        (0xE7, 0xB4, 0x4A),
        (0x5B, 0x97, 0xDE),
        (0xBA, 0x7E, 0xD6),
        (0x5E, 0xBE, 0xC9),
        (0xFF, 0xFF, 0xFF),
    ],
    [
        (0x73, 0x73, 0x73),
        (0xFF, 0x78, 0x78),
        (0x7E, 0xE0, 0x96),
        (0xFF, 0xD0, 0x6E),
        (0x82, 0xB4, 0xFF),
        (0xD4, 0xA0, 0xEB),
        (0x82, 0xD7, 0xE1),
        (0xFF, 0xFF, 0xFF),
    ],
)

_FONT_FILES = {
    "regular": "GoogleSansCode-Regular.ttf",
    "medium": "GoogleSansCode-Medium.ttf",
}
_FONT_DIRS = (
    Path(os.environ["CORPORA_DOCS_FONT_DIR"]) if os.environ.get("CORPORA_DOCS_FONT_DIR") else None,
    Path.home() / "Library" / "Fonts",
    Path("/Library/Fonts"),
    Path("/usr/share/fonts/truetype/google-sans-code"),
    Path("/usr/local/share/fonts"),
)
_EMOJI_FONT = Path("/System/Library/Fonts/Apple Color Emoji.ttc")


def _font_path(filename: str) -> Path:
    for directory in _FONT_DIRS:
        if directory and (directory / filename).is_file():
            return directory / filename
    searched = ", ".join(str(d) for d in _FONT_DIRS if d)
    raise SystemExit(
        f"error: {filename} not found (looked in {searched}).\n"
        "Install Google Sans Code (https://fonts.google.com/specimen/Google+Sans+Code) "
        "or point CORPORA_DOCS_FONT_DIR at the directory holding it."
    )


class _Face:
    """One weight of the shot font, as scaled SVG outlines."""

    def __init__(self, filename: str):
        self.font = TTFont(_font_path(filename))
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        self.scale = FONT_SIZE / self.font["head"].unitsPerEm

    @lru_cache(maxsize=512)  # noqa: B019 - one long-lived instance per weight
    def outline(self, char: str) -> str | None:
        """Path data for one character, baseline at y=0. None when blank."""
        name = self.cmap.get(ord(char))
        if name is None:
            return None
        # Two decimals is well under a pixel at this size and keeps the
        # outline data (which ships in every shot) compact.
        pen = SVGPathPen(self.glyphs, ntos=lambda value: f"{value:.2f}".rstrip("0").rstrip("."))
        # Font units are y-up, SVG is y-down.
        self.glyphs[name].draw(TransformPen(pen, Transform(self.scale, 0, 0, -self.scale, 0, 0)))
        return pen.getCommands() or None


@cache
def _faces() -> dict[str, _Face]:
    return {weight: _Face(filename) for weight, filename in _FONT_FILES.items()}


@lru_cache(maxsize=256)
def _emoji_png(char: str) -> str | None:
    """Base64 PNG for an emoji, from the platform bitmap font."""
    if not _EMOJI_FONT.is_file():
        return None
    font = _emoji_font()
    if font is None:
        return None
    cmap, sbix = font
    # Strip the variation selector: the strikes are keyed by the base glyph.
    name = cmap.get(ord(char.replace("️", "")[:1]))
    if name is None:
        return None
    # The smallest strike that still beats a 2× render of an 18 px cell —
    # 160 px bitmaps would quadruple the size of a shot for nothing.
    size = min((s for s in sbix.strikes if s >= 48), default=max(sbix.strikes))
    strike = sbix.strikes[size]
    glyph = strike.glyphs.get(name)
    if glyph is None or glyph.graphicType.strip() != "png":
        return None
    return base64.b64encode(glyph.imageData).decode("ascii")


@lru_cache(maxsize=1)
def _emoji_font():
    try:
        font = TTCollection(str(_EMOJI_FONT), lazy=True).fonts[0]
        return font.getBestCmap(), font["sbix"]
    except Exception:  # a missing or unreadable font is not worth failing over
        return None


# ── rendering ────────────────────────────────────────────────────────────────


def _lines(console: Console) -> list[list[tuple[str, Style]]]:
    """The recorded output as lines of (text, style) runs.

    `_record_buffer` is Rich's own recording seam — `export_svg` reads it the
    same way — and there is no public accessor for the segments.
    """
    with console._record_buffer_lock:
        segments = list(Segment.filter_control(console._record_buffer))
        console._record_buffer.clear()
    rendered = [
        [(text, style or Style()) for text, style, _control in line]
        for line in Segment.split_and_crop_lines(
            segments, length=console.width, include_new_lines=False
        )
    ]
    while rendered and not "".join(text for text, _ in rendered[-1]).strip():
        rendered.pop()
    return rendered


def _fill(style: Style) -> str:
    color = style.color
    triplet = THEME.foreground_color if color is None else color.get_truecolor(THEME)
    return triplet.hex


def render(console: Console, title: str = "Terminal", *, columns: int | None = None) -> str:
    """Draw a recorded console. Every shot is the terminal's full width, so a
    set of them lines up at one size instead of each hugging its own content.
    """
    lines = _lines(console)
    width = PAD_LEFT + round((columns or console.width) * ADVANCE) + PAD_RIGHT
    height = PAD_TOP + len(lines) * LINE_HEIGHT + PAD_BOTTOM

    faces = _faces()
    defs: dict[tuple[str, str], str] = {}  # (weight, char) → glyph id
    glyph_defs: list[str] = []
    body: list[str] = []
    emoji: dict[str, str] = {}  # char → image id

    for row, line in enumerate(lines):
        top = PAD_TOP + row * LINE_HEIGHT
        baseline = top + BASELINE
        column = 0
        for text, style in line:
            weight = "medium" if style.bold else "regular"
            fill = _fill(style)
            opacity = DIM_OPACITY if style.dim else 1
            bgcolor = style.bgcolor
            if bgcolor is not None and not bgcolor.is_default:
                body.append(
                    f'<rect x="{PAD_LEFT + column * ADVANCE:g}" y="{top}" '
                    f'width="{cell_len(text) * ADVANCE:g}" height="{LINE_HEIGHT}" '
                    f'fill="{bgcolor.get_truecolor(THEME).hex}"/>'
                )
            for char in text:
                cells = cell_len(char)
                x = PAD_LEFT + column * ADVANCE
                column += cells
                if not char.strip():
                    continue
                if cells == 2 and faces[weight].cmap.get(ord(char)) is None:
                    body.append(_emoji(char, x, top, emoji, glyph_defs))
                    continue
                key = (weight, char)
                if key not in defs:
                    outline = faces[weight].outline(char)
                    if outline is None:
                        continue
                    defs[key] = f"g{len(defs)}"
                    glyph_defs.append(f'<path id="{defs[key]}" d="{outline}"/>')
                if key not in defs:
                    continue
                alpha = f' fill-opacity="{opacity}"' if opacity != 1 else ""
                body.append(
                    f'<use href="#{defs[key]}" x="{x:g}" y="{baseline}" fill="{fill}"{alpha}/>'
                )

    return _document(width, height, title, glyph_defs, body)


def _emoji(char: str, x: float, top: int, seen: dict[str, str], defs: list[str]) -> str:
    """A colour emoji: the platform bitmap, or a text fallback."""
    box = 2 * ADVANCE
    if char not in seen:
        data = _emoji_png(char)
        if data is None:
            return (
                f'<text x="{x:g}" y="{top + BASELINE}" font-size="{FONT_SIZE}" '
                f'font-family="monospace">{escape(char)}</text>'
            )
        seen[char] = f"e{len(seen)}"
        uri = f"data:image/png;base64,{data}"
        defs.append(
            f'<image id="{seen[char]}" width="{box:g}" height="{box:g}" '
            f'href="{uri}" xlink:href="{uri}"/>'
        )
    return f'<use href="#{seen[char]}" x="{x:g}" y="{top + 1:g}"/>'


def _document(width: int, height: int, title: str, glyphs: list[str], body: list[str]) -> str:
    lights = "".join(
        f'<circle cx="{cx}" cy="{LIGHT_CY}" r="{LIGHT_RADIUS}" fill="{color}"/>'
        for color, cx in LIGHTS
    )
    title_glyphs = _title(title, width)
    newline = "\n"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" \
width="{width + 2 * MARGIN}" height="{height + 2 * MARGIN}" \
viewBox="0 0 {width + 2 * MARGIN} {height + 2 * MARGIN}">
<defs>
{newline.join(glyphs)}
</defs>
<g transform="translate({MARGIN}, {MARGIN})">
<rect x="0" y="0" width="{width}" height="{height}" rx="{RADIUS}" ry="{RADIUS}" \
fill="{BACKGROUND}" stroke="{BORDER}" stroke-width="{BORDER_WIDTH}"/>
{lights}
{title_glyphs}
{newline.join(body)}
</g>
</svg>
"""


def _title(title: str, width: int) -> str:
    if not title:
        return ""
    face = _faces()["medium"]
    scale = TITLE_SIZE / FONT_SIZE
    advance = ADVANCE * scale
    lights_end = LIGHTS[-1][1] + LIGHT_RADIUS + 12
    x = max((width - len(title) * advance) / 2, lights_end)
    if x + len(title) * advance > width - 12:
        return ""  # too narrow for a label; the lights carry the bar alone
    parts = []
    for index, char in enumerate(title):
        if not char.strip():
            continue
        outline = face.outline(char)
        if outline is None:
            continue
        parts.append(
            f'<path d="{outline}" fill="#FFFFFF" fill-opacity="{TITLE_OPACITY}" '
            f'transform="translate({x + index * advance:g}, {TITLE_BASELINE}) scale({scale:g})"/>'
        )
    return "".join(parts)


def save(console: Console, path: Path, title: str = "Terminal") -> Path:
    path.write_text(render(console, title=title))
    return path
