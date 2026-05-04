"""Quote → bbox matcher.

The vision model emits a ``quote_or_value`` string for every
extracted fact (e.g. ``"HDL Cholesterol 52 mg/dL"`` for a lab
result, ``"Farrah Rolle, DOB 06/14/1972"`` for an intake
demographics block). This module finds the smallest contiguous
word span on the claimed page whose concatenated text best matches
the quote, and returns a :class:`BBox` over the span plus a
confidence score in ``[0, 1]``.

**Algorithm: positional sliding window.**

1. Normalize the quote: lowercase, replace runs of non-alphanum
   with a single space, drop empty tokens. Apply the same
   normalization to each pdfplumber word — and split the result
   on whitespace, so a merged Word like ``"mg/dL"`` becomes two
   normalized tokens ``["mg", "dl"]`` (both back-referencing the
   same Word for bbox purposes).
2. Slide a window of size ``len(quote_tokens)`` across the page's
   normalized tokens. For each window starting at offset ``start``,
   score = (number of positional matches) / (quote token count).
   ``window[i] == quote_tokens[i]``, NOT just ``window[i] in quote_tokens``.
3. Return the highest-scoring window if its score ≥ ``threshold``
   (default 0.6).

Why positional, not set-based: a set-based score lets a window
match even when the order is wrong, which can pull tokens from two
different rows on the same page. Example: a lab report with both
LDL and HDL rows has ``"cholesterol"``, ``"mg"``, ``"dl"`` in both;
a set-based matcher would sometimes pick a window straddling the
two rows. Positional matching naturally rejects that — the tokens
are in the same order on the page only within one row.

Why a sliding window of fixed size = ``len(quote_tokens)``:
simplest correct rule. It does mean the page must have at least
as many normalized tokens as the quote — but in practice the
vision model and pdfplumber tokenize PDFs almost identically (both
operate on visual whitespace), so this matches.

The threshold (0.6) accommodates the realistic case where one
or two tokens are mis-tokenized between the two normalizers.
A perfect 1.0 match is the common case on clean PDFs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from copilot.extraction.pdf import Word
from copilot.schemas import BBox

DEFAULT_MATCH_THRESHOLD = 0.6
"""Token-overlap fraction below which we declare "no match" rather
than emit a low-confidence bbox. Tuned conservatively: too high and
clean PDFs miss matches because of tokenization quirks; too low and
the matcher attaches bboxes to nearby-but-wrong rows."""

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class MatchResult:
    """Outcome of a single quote→bbox lookup.

    ``confidence`` is the positional-overlap score in ``[0, 1]``.
    A successful match has ``bbox`` populated; a failed match
    leaves it ``None`` and the score is the best-attempt score
    (useful for telemetry — we want to know whether matching is
    consistently bad on a particular doc type).
    """

    bbox: BBox | None
    confidence: float
    matched_text: str


def match_quote(
    quote: str,
    page_words: Sequence[Word],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> MatchResult:
    """Best-match `quote` against `page_words`.

    `page_words` is expected to be already filtered to a single
    page (use :func:`copilot.extraction.pdf.words_on_page`). The
    matcher does not implicitly cross pages — a span across pages
    raises rather than returning a meaningless bbox.
    """

    quote_tokens = _normalize(quote).split()
    if not quote_tokens:
        return MatchResult(bbox=None, confidence=0.0, matched_text="")

    if not page_words:
        return MatchResult(bbox=None, confidence=0.0, matched_text="")

    # Pre-normalize the page word list once. A pdfplumber Word like
    # "52mg/dL" yields two normalized tokens, both back-referencing
    # the same Word — the bbox we return is the union of unique
    # Words, which still correctly covers the visual span.
    norm_tokens: list[tuple[str, Word]] = []
    for w in page_words:
        for tok in _normalize(w.text).split():
            norm_tokens.append((tok, w))

    n = len(norm_tokens)
    target_n = len(quote_tokens)
    if n < target_n:
        # Page has fewer tokens than the quote. Score the partial
        # overlap (positional, against the prefix of quote_tokens)
        # for telemetry; never emit a bbox.
        matched = sum(
            1 for (tok, _), q in zip(norm_tokens, quote_tokens) if tok == q
        )
        return MatchResult(
            bbox=None,
            confidence=matched / target_n,
            matched_text=" ".join(t for t, _ in norm_tokens),
        )

    best_score = 0.0
    best_window: list[tuple[str, Word]] = []

    for start in range(n - target_n + 1):
        window = norm_tokens[start:start + target_n]
        matched = sum(
            1 for (tok, _), q in zip(window, quote_tokens) if tok == q
        )
        score = matched / target_n
        if score > best_score:
            best_score = score
            best_window = window
            if score >= 1.0:
                break

    if best_score < threshold or not best_window:
        return MatchResult(
            bbox=None,
            confidence=best_score,
            matched_text=" ".join(tok for tok, _ in best_window),
        )

    # De-duplicate Words: a single pdfplumber Word may have
    # contributed multiple normalized tokens to the window.
    seen: set[int] = set()
    span_words: list[Word] = []
    for _, w in best_window:
        if id(w) in seen:
            continue
        seen.add(id(w))
        span_words.append(w)

    return MatchResult(
        bbox=_bbox_union(span_words),
        confidence=best_score,
        matched_text=" ".join(tok for tok, _ in best_window),
    )


# ─── helpers ────────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase, replace any run of non-alphanum with a single space,
    strip. Used on both quote and word text so the comparison is
    apples-to-apples."""
    return _PUNCT_RE.sub(" ", s.lower()).strip()


def _bbox_union(words: Sequence[Word]) -> BBox:
    if not words:
        raise ValueError("cannot union an empty word list")
    page = words[0].page
    if any(w.page != page for w in words):
        # Caller pre-filtered wrong, or the matcher accidentally
        # spanned pages. Either way the bbox would be meaningless.
        raise ValueError(
            f"_bbox_union called across multiple pages: "
            f"{sorted({w.page for w in words})}"
        )
    return BBox(
        page=page,
        x0=min(w.x0 for w in words),
        y0=min(w.top for w in words),
        x1=max(w.x1 for w in words),
        y1=max(w.bottom for w in words),
    )
