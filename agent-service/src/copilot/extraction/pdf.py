"""pdfplumber wrapper.

Returns a list of :class:`Word`\\ s — a normalized shape that:

- Carries a 1-indexed page number (pdfplumber's ``Page.page_number``
  is already 1-indexed; we surface that).
- Uses pdfplumber's native top/bottom Y axis (origin top-left, Y
  increases downward, in PDF user-space points). pdf.js, which we
  use for the frontend overlay, also takes top-down PDF-point
  coordinates, so no axis flip is needed.
- Filters out empty / whitespace-only "words" that pdfplumber
  occasionally emits for invisible glyphs.

The matcher (:mod:`copilot.extraction.matcher`) consumes this list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Word:
    """One word on a PDF page, in PDF points (1/72 inch).

    Coordinates are top-down (origin top-left, Y increases downward)
    to match pdfplumber and pdf.js. The :class:`copilot.schemas.BBox`
    model uses the same convention; the post-pass copies these
    coordinates verbatim.
    """

    page: int       # 1-indexed
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


def extract_words(pdf_bytes: bytes) -> list[Word]:
    """Render every page's text into a flat :class:`Word` list.

    pdfplumber is import-time expensive (~200 ms cold). We import it
    lazily inside this function so the unit tests for the matcher
    (which never need a real PDF) don't pay that cost.
    """

    import io
    import pdfplumber  # type: ignore[import-not-found]

    out: list[Word] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for w in page.extract_words(use_text_flow=True):
                text = (w.get("text") or "").strip()
                if not text:
                    continue
                out.append(Word(
                    page=page.page_number,
                    text=text,
                    x0=float(w["x0"]),
                    top=float(w["top"]),
                    x1=float(w["x1"]),
                    bottom=float(w["bottom"]),
                ))
    return out


def page_count(pdf_bytes: bytes) -> int:
    """Page count, used to bounds-check ``page_or_section`` claims
    from the vision model before we run the matcher."""

    import io
    import pdfplumber  # type: ignore[import-not-found]

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return len(pdf.pages)


def words_on_page(words: Iterable[Word], page: int) -> Sequence[Word]:
    """Subset helper for the matcher's page-hint shortcut."""
    return [w for w in words if w.page == page]
