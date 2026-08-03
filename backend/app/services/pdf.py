"""Server-side storybook PDF rendering.

Replaces the client-side html2pdf path, which rasterised the web page into
JPEG screenshots: text was pixels at ~100 DPI, and a CSS sizing bug meant two
blank sheets followed every content page — a 5-page story exported as 15
pages, 10 of them empty, with a blank cover. Every test was green, because
no test ever opened the artifact.

fpdf2 rather than WeasyPrint, deliberately: pure Python, so the exact code
path runs on the Windows dev box, in CI, and in the Linux image alike.
WeasyPrint's native Pango stack cannot be imported on the dev machine, and a
renderer that cannot be run locally is how the last PDF bug survived.

Nepali is a first-class language, so Devanagari runs through real text
shaping (uharfbuzz); without shaping, vowel signs and conjuncts render in
the wrong places. Fonts are resolved from a per-platform search list: Noto
in the container and CI, the system's Devanagari-capable fonts on Windows.
Nothing is bundled and no download happens at render time.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import MethodReturnValue
from PIL import Image

from ..config import get_settings

logger = logging.getLogger(__name__)

# The subsetter narrates every dropped glyph table at INFO — ~200 lines per
# export with system Devanagari fonts. Real problems still surface as WARNING.
logging.getLogger("fontTools.subset").setLevel(logging.WARNING)

PAGE_W, PAGE_H = 8.5, 11.0  # letter, inches
MARGIN = 0.85
TEXT_W = 6.4
CREAM = (251, 243, 228)
SEPIA = (112, 66, 20)
INK = (45, 42, 38)


class PdfUnavailableError(RuntimeError):
    """No usable font on this host. The endpoint turns this into a 503."""


@dataclass
class PdfPage:
    text: str
    image: bytes | None = None


# Preference order: serif reads more like a printed book, sans is a fine
# fallback, and the Windows names let the dev box render without installing
# anything (used at runtime only — never redistributed).
_LATIN = ("NotoSerif-Regular.ttf", "NotoSans-Regular.ttf", "georgia.ttf", "times.ttf")
_LATIN_BOLD = ("NotoSerif-Bold.ttf", "NotoSans-Bold.ttf", "georgiab.ttf", "timesbd.ttf")
# Nirmala ships as a .ttc collection on many Windows builds, which fpdf2
# cannot load — Mangal is the plain-TTF Devanagari face that is always there.
_DEVA = ("NotoSerifDevanagari-Regular.ttf", "NotoSansDevanagari-Regular.ttf", "Nirmala.ttf", "mangal.ttf")
_DEVA_BOLD = ("NotoSerifDevanagari-Bold.ttf", "NotoSansDevanagari-Bold.ttf", "NirmalaB.ttf", "mangalb.ttf")


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    for entry in get_settings().pdf_font_dirs.split(","):
        if entry.strip():
            dirs.append(Path(entry.strip()))
    backend = Path(__file__).resolve().parents[2]
    dirs.append(backend / "assets" / "fonts")  # vendored fonts, if ever added
    dirs.append(Path("/usr/share/fonts/truetype/noto"))  # Docker image + CI
    dirs.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")  # dev box
    return dirs


def _find_font(candidates: tuple[str, ...]) -> Path | None:
    for directory in _font_dirs():
        for name in candidates:
            path = directory / name
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
    return None


def devanagari_font_available() -> bool:
    """Lets tests skip Nepali rendering on hosts with no Devanagari font."""
    return _find_font(_DEVA) is not None


def _register_fonts(pdf: FPDF, language: str) -> str:
    """Register fonts and return the primary family for this language."""
    latin = _find_font(_LATIN)
    if latin is None:
        raise PdfUnavailableError(
            "No usable font found. Install fonts-noto-core (Linux) or set PDF_FONT_DIRS."
        )
    pdf.add_font("latin", "", str(latin))
    pdf.add_font("latin", "B", str(_find_font(_LATIN_BOLD) or latin))

    deva = _find_font(_DEVA)
    if deva is not None:
        pdf.add_font("deva", "", str(deva))
        pdf.add_font("deva", "B", str(_find_font(_DEVA_BOLD) or deva))

    if language == "ne":
        if deva is None:
            raise PdfUnavailableError("Nepali story but no Devanagari-capable font on this host.")
        primary = "deva"
        pdf.set_fallback_fonts(["latin"])  # brand strings stay Latin
    else:
        primary = "latin"
        if deva is not None:
            pdf.set_fallback_fonts(["deva"])  # a Nepali word inside an English story

    try:
        # Shaping places matras and builds conjuncts. Harmless for Latin,
        # essential for Devanagari.
        pdf.set_text_shaping(True)
    except Exception as e:  # uharfbuzz missing or broken: degrade, don't die
        logger.warning("Text shaping unavailable (%s); Devanagari may render degraded", e)
    return primary


def _image_size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(BytesIO(data)) as im:
            return im.size
    except Exception:
        return None


def _cream_page(pdf: FPDF) -> None:
    """Background and double frame shared by the cover and back cover."""
    pdf.add_page()
    pdf.set_fill_color(*CREAM)
    pdf.rect(0, 0, PAGE_W, PAGE_H, style="F")
    pdf.set_draw_color(*SEPIA)
    pdf.set_line_width(0.016)
    pdf.rect(0.42, 0.42, PAGE_W - 0.84, PAGE_H - 0.84)
    pdf.set_line_width(0.008)
    pdf.rect(0.5, 0.5, PAGE_W - 1.0, PAGE_H - 1.0)


def _fitted(size: tuple[int, int] | None, max_w: float, max_h: float) -> tuple[float, float]:
    if size is None or size[0] <= 0 or size[1] <= 0:
        return max_w, max_w * 2 / 3
    w = max_w
    h = w * size[1] / size[0]
    if h > max_h:
        h = max_h
        w = h * size[0] / size[1]
    return w, h


def _framed_image(pdf: FPDF, data: bytes, *, y: float, max_w: float, max_h: float) -> float | None:
    """Draw a centered image with a thin frame; return the y below it.

    Returns None when the bytes cannot be drawn — a truncated file from an
    interrupted write, or a zero-byte blob the provider marked "ok". One bad
    image must degrade one page, never fail the book: the whole endpoint
    500-ing over a corrupt illustration would break a share link that was
    already handed out. The size probe alone is not enough — a truncated PNG
    parses its header fine and only fails at draw time — so the draw itself
    is guarded too.
    """
    size = _image_size(data)
    if size is None:
        return None
    w, h = _fitted(size, max_w, max_h)
    x = (PAGE_W - w) / 2
    try:
        pdf.image(BytesIO(data), x=x, y=y, w=w, h=h)
    except Exception as e:
        logger.warning("Illustration could not be drawn, degrading the page: %s", e)
        return None
    pdf.set_draw_color(*SEPIA)
    pdf.set_line_width(0.012)
    pdf.rect(x - 0.06, y - 0.06, w + 0.12, h + 0.12)
    return y + h


def _fit_text(pdf: FPDF, family: str, text: str, language: str, avail_h: float) -> tuple[float, float]:
    """Pick the largest size at which the paragraph fits the remaining sheet.

    A story page is physically one sheet — that is the n+2 property the tests
    pin. The model is asked for 60-110 words but nothing enforces it, and at
    ~150 words the text used to spill onto a continuation sheet that then
    carried the page number: the old exporter's blank-sheet bug, reborn.
    """
    base = 13.5
    base_leading = 0.34 if language == "ne" else 0.31
    for size in (13.5, 12.5, 11.5, 10.5, 9.5):
        leading = base_leading * (size / base)
        pdf.set_font(family, "", size)
        height = pdf.multi_cell(
            TEXT_W, leading, text, align="L", dry_run=True, output=MethodReturnValue.HEIGHT
        )
        if height <= avail_h:
            return size, leading
    logger.warning("Story text does not fit even at 9.5pt; the tail will be clipped")
    return 9.5, base_leading * (9.5 / base)


def _page_furniture(pdf: FPDF, draw) -> None:
    """Write near the bottom edge with page breaking OFF.

    Footer text sits below the auto-break trigger; writing it with breaking on
    inserts a fresh page first. That mechanism — furniture triggering breaks —
    is the family of bug that made the old exporter emit blank sheets.
    """
    pdf.set_auto_page_break(False)
    draw()
    pdf.set_auto_page_break(True, margin=MARGIN)


def _cover(
    pdf: FPDF, primary: str, *, title: str, hero_name: str, language: str, image: bytes | None
) -> None:
    _cream_page(pdf)
    # A cover is a fixed layout; nothing on it may ever paginate. With auto
    # break on, a long enough title would push the brand line past the break
    # trigger and mint a blank sheet — the old exporter's signature failure.
    pdf.set_auto_page_break(False)
    pdf.set_text_color(*SEPIA)
    title_size = 30.0
    pdf.set_font(primary, "B", title_size)
    title_h = pdf.multi_cell(
        PAGE_W - 1.9, 0.52, title, align="C", dry_run=True, output=MethodReturnValue.HEIGHT
    )
    if title_h > 3.0:  # a prompt-length title wraps to many lines at 30pt
        title_size = 22.0
        pdf.set_font(primary, "B", title_size)
    pdf.set_xy(0.95, 1.3)
    pdf.multi_cell(PAGE_W - 1.9, 0.52 * title_size / 30.0, title, align="C")

    if hero_name:
        pdf.set_font(primary, "", 13)
        pdf.set_xy(0.95, pdf.get_y() + 0.18)
        label = f"मुख्य पात्र: {hero_name}" if language == "ne" else f"starring {hero_name}"
        pdf.multi_cell(PAGE_W - 1.9, 0.3, label, align="C")

    if image is not None:
        y = max(pdf.get_y() + 0.5, 3.4)
        # Only the space actually left above the brand line; a tall title
        # shrinks the picture rather than pushing it off the sheet.
        available = (PAGE_H - 1.35) - y
        if available >= 1.6:
            _framed_image(pdf, image, y=y, max_w=4.9, max_h=min(4.6, available))

    pdf.set_font("latin", "", 11)
    pdf.set_text_color(*SEPIA)
    pdf.set_xy(0, PAGE_H - 1.12)
    pdf.multi_cell(PAGE_W, 0.26, "a KathaSajha storybook", align="C")
    pdf.set_auto_page_break(True, margin=MARGIN)


def _story_page(pdf: FPDF, primary: str, *, index: int, page: PdfPage, language: str) -> None:
    pdf.add_page()
    top = 0.8
    text_y = None
    if page.image is not None:
        image_bottom = _framed_image(pdf, page.image, y=top, max_w=6.3, max_h=5.0)
        if image_bottom is not None:
            text_y = image_bottom + 0.45
    if text_y is None:
        # A missing illustration degrades the page; it never loses the story.
        x = (PAGE_W - 6.3) / 2
        pdf.set_fill_color(*CREAM)
        pdf.rect(x, top, 6.3, 2.0, style="F")
        pdf.set_text_color(*SEPIA)
        pdf.set_font(primary, "", 11)
        pdf.set_xy(x, top + 0.85)
        note = (
            "यस पृष्ठको चित्र बनाउन सकिएन।"
            if language == "ne"
            else "The illustration for this page could not be created."
        )
        pdf.multi_cell(6.3, 0.26, note, align="C")
        text_y = top + 2.45

    # One scene, one sheet: size the text to the space that is left, and keep
    # page breaking OFF while writing it so a measurement miss clips rather
    # than minting a continuation sheet with the page number on it.
    available = (PAGE_H - 0.75) - text_y
    size, leading = _fit_text(pdf, primary, page.text, language, available)
    pdf.set_text_color(*INK)
    pdf.set_font(primary, "", size)
    pdf.set_xy((PAGE_W - TEXT_W) / 2, text_y)
    pdf.set_auto_page_break(False)
    pdf.multi_cell(TEXT_W, leading, page.text, align="L")
    pdf.set_auto_page_break(True, margin=MARGIN)

    def _number():
        pdf.set_text_color(*SEPIA)
        pdf.set_font("latin", "", 10)
        pdf.set_xy(0, PAGE_H - 0.6)
        pdf.cell(PAGE_W, 0.25, str(index), align="C")

    _page_furniture(pdf, _number)


def _back_cover(
    pdf: FPDF, primary: str, *, language: str, hero_name: str, created_at: datetime | None
) -> None:
    _cream_page(pdf)
    pdf.set_text_color(*SEPIA)
    pdf.set_font(primary, "B", 24)
    pdf.set_xy(0.95, 4.5)
    pdf.multi_cell(PAGE_W - 1.9, 0.5, "समाप्त" if language == "ne" else "The End", align="C")

    def _colophon():
        pdf.set_font("latin", "", 10.5)
        pdf.set_text_color(*SEPIA)
        made = f"Made for {hero_name} with KathaSajha" if hero_name else "Made with KathaSajha"
        when = (created_at or datetime.now(UTC)).strftime("%B %Y")
        pdf.set_xy(0.95, PAGE_H - 1.55)
        pdf.multi_cell(PAGE_W - 1.9, 0.24, f"{made}\n{when}", align="C")

    _page_furniture(pdf, _colophon)


def build_story_pdf(
    *,
    title: str,
    language: str,
    hero_name: str,
    pages: list[PdfPage],
    created_at: datetime | None = None,
) -> bytes:
    """Render a story as a book: cover, one page per scene, back cover.

    Page count is exactly len(pages) + 2 — the property the old exporter
    violated, and the one the tests pin.
    """
    pdf = FPDF(unit="in", format="letter")
    pdf.set_margins(1.05, MARGIN)
    pdf.set_auto_page_break(True, margin=MARGIN)
    primary = _register_fonts(pdf, language)

    # The first image that actually decodes; a corrupt first page must not
    # cost the cover its picture (or worse, the render).
    cover_image = next(
        (p.image for p in pages if p.image is not None and _image_size(p.image) is not None), None
    )
    _cover(pdf, primary, title=title, hero_name=hero_name, language=language, image=cover_image)
    for i, page in enumerate(pages, start=1):
        _story_page(pdf, primary, index=i, page=page, language=language)
    _back_cover(pdf, primary, language=language, hero_name=hero_name, created_at=created_at)
    return bytes(pdf.output())
