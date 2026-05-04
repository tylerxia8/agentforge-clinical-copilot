"""Unit tests for the quote→bbox matcher.

These tests construct synthetic word lists (no real PDF, no
pdfplumber) so the matcher's correctness is locked in independently
of the PDF parsing layer. The W2 ``factually_consistent`` eval
rubric depends on the matcher being conservative — it must NOT
attach a bbox to a near-but-wrong row, because doing so would let
the click-through overlay highlight the wrong span and make a
fabricated extraction look verified.
"""

from __future__ import annotations

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENEMR_BASE_URL", "http://localhost:8080")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_ID", "test")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_SECRET", "test")
os.environ.setdefault("OPENEMR_SERVICE_USERNAME", "test")
os.environ.setdefault("OPENEMR_SERVICE_PASSWORD", "test")
os.environ.setdefault("AGENT_SHARED_SECRET", "test-secret-test-secret")

from copilot.extraction.matcher import match_quote
from copilot.extraction.pdf import Word


# ─── helpers ────────────────────────────────────────────────────────────

def _row(page: int, y: float, *, words_with_x: list[tuple[str, float, float]]) -> list[Word]:
    """Build a horizontal row of Words at vertical band y..(y+12)."""
    return [
        Word(page=page, text=t, x0=x0, top=y, x1=x1, bottom=y + 12)
        for (t, x0, x1) in words_with_x
    ]


# ─── basic happy path ───────────────────────────────────────────────────


def test_exact_match_single_word():
    words = _row(1, 100, words_with_x=[("Cholesterol", 100, 180)])
    r = match_quote("Cholesterol", words)
    assert r.bbox is not None
    assert r.confidence == 1.0
    assert r.bbox.page == 1
    assert (r.bbox.x0, r.bbox.x1) == (100, 180)


def test_exact_match_multiword_row():
    words = _row(1, 100, words_with_x=[
        ("HDL", 50, 70),
        ("Cholesterol", 75, 165),
        ("52", 170, 190),
        ("mg/dL", 195, 230),
        ("N", 235, 245),
    ])
    r = match_quote("HDL Cholesterol 52 mg/dL", words)
    assert r.bbox is not None
    assert r.confidence == 1.0
    # bbox should cover only the matched span, not the trailing "N" flag
    assert r.bbox.x0 == 50
    assert r.bbox.x1 == 230


def test_match_picks_correct_row_among_distractors():
    """Two lab rows on the same page; the matcher must pick the
    correct one — not the one that happens to share some tokens."""
    words = _row(1, 100, words_with_x=[
        ("LDL", 50, 70),
        ("Cholesterol", 75, 165),
        ("130", 170, 195),
        ("mg/dL", 200, 235),
    ])
    words += _row(1, 130, words_with_x=[
        ("HDL", 50, 70),
        ("Cholesterol", 75, 165),
        ("52", 170, 190),
        ("mg/dL", 195, 230),
    ])
    r = match_quote("HDL Cholesterol 52 mg/dL", words)
    assert r.bbox is not None
    assert r.confidence == 1.0
    # The HDL row is at y=130, not 100
    assert r.bbox.y0 == 130


# ─── tokenization quirks ────────────────────────────────────────────────


def test_match_handles_pdfplumber_merged_word():
    """pdfplumber sometimes glues adjacent text into one Word when
    the source has no whitespace between glyphs (common on tightly-
    printed lab reports). The vision model is reading the same
    visual unit, so it'll emit the same merged token in its quote.
    Both normalize to the same tokens, so the match is exact."""
    words = _row(1, 100, words_with_x=[
        ("HDL", 50, 70),
        ("Cholesterol", 75, 165),
        # Single Word that pdfplumber will normalize to two tokens
        ("52mg/dL", 170, 230),
    ])
    r = match_quote("HDL Cholesterol 52mg/dL", words)
    assert r.bbox is not None
    assert r.confidence == 1.0
    # bbox covers all 3 Words (the merged Word de-dupes to one)
    assert r.bbox.x0 == 50
    assert r.bbox.x1 == 230


def test_match_normalizes_punctuation():
    """The vision model's quote may include punctuation that
    pdfplumber strips (or vice versa)."""
    words = _row(1, 100, words_with_x=[
        ("Patient:", 50, 110),
        ("Farrah", 115, 165),
        ("Rolle", 170, 215),
    ])
    r = match_quote("Patient Farrah Rolle", words)
    assert r.bbox is not None
    assert r.confidence == 1.0


def test_match_is_case_insensitive():
    words = _row(1, 100, words_with_x=[("HEMOGLOBIN", 50, 130)])
    r = match_quote("hemoglobin", words)
    assert r.bbox is not None
    assert r.confidence == 1.0


# ─── conservatism: refuse a wrong match ─────────────────────────────────


def test_no_match_when_quote_absent():
    words = _row(1, 100, words_with_x=[
        ("HDL", 50, 70),
        ("Cholesterol", 75, 165),
    ])
    r = match_quote("Hemoglobin A1c 7.2 percent", words)
    assert r.bbox is None
    assert r.confidence < 0.6


def test_no_match_on_empty_word_list():
    r = match_quote("anything", [])
    assert r.bbox is None
    assert r.confidence == 0.0


def test_no_match_on_empty_quote():
    words = _row(1, 100, words_with_x=[("HDL", 50, 70)])
    r = match_quote("", words)
    assert r.bbox is None
    assert r.confidence == 0.0


def test_no_match_when_only_one_token_overlaps():
    """One shared token out of four shouldn't cross the 0.6 threshold."""
    words = _row(1, 100, words_with_x=[
        ("Cholesterol", 50, 130),
        ("Total", 135, 175),
        ("210", 180, 200),
        ("mg/dL", 205, 240),
    ])
    r = match_quote("HDL Cholesterol 52 reading", words)
    # Only "Cholesterol" matches → 1/4 = 0.25
    assert r.bbox is None
    assert r.confidence < 0.6


def test_partial_match_score_returned_for_telemetry():
    """Even when we refuse to emit a bbox, we want the partial score
    so observability can flag a doc type that consistently mis-matches."""
    words = _row(1, 100, words_with_x=[
        ("LDL", 50, 70),
        ("Cholesterol", 75, 165),
        ("130", 170, 195),
    ])
    r = match_quote("HDL Cholesterol 52", words)
    # 1 of 3 tokens match (Cholesterol) → 0.333…
    assert r.bbox is None
    assert 0 < r.confidence < 0.6


# ─── bbox geometry ──────────────────────────────────────────────────────


def test_bbox_union_covers_all_matched_words():
    words = _row(1, 100, words_with_x=[
        ("alpha", 10, 50),
        ("beta", 60, 100),
        ("gamma", 110, 150),
    ])
    r = match_quote("alpha beta gamma", words)
    assert r.bbox is not None
    assert r.bbox.x0 == 10
    assert r.bbox.x1 == 150
    assert r.bbox.y0 == 100
    assert r.bbox.y1 == 112  # top + 12 (the row helper's height)


def test_bbox_excludes_unmatched_neighbors():
    """Words on the same row that aren't in the quote must NOT widen
    the bbox — otherwise the overlay would highlight too much."""
    words = _row(1, 100, words_with_x=[
        ("PREFIX", 0, 30),
        ("alpha", 35, 70),
        ("beta", 75, 110),
        ("SUFFIX", 115, 200),
    ])
    r = match_quote("alpha beta", words)
    assert r.bbox is not None
    assert r.bbox.x0 == 35
    assert r.bbox.x1 == 110


def test_threshold_is_configurable():
    """Caller can tighten the threshold for high-stakes fields
    (e.g. lab values where a wrong-row attribution is dangerous)."""
    words = _row(1, 100, words_with_x=[
        ("HDL", 50, 70),
        ("Cholesterol", 75, 165),
        ("52", 170, 195),
        ("WRONG_TAIL", 200, 280),
    ])
    # Default threshold: 3 of 4 tokens match → 0.75 ≥ 0.6 → bbox returned
    r_default = match_quote("HDL Cholesterol 52 mg/dL", words)
    assert r_default.bbox is not None
    # Stricter threshold: 0.75 < 0.9 → no bbox
    r_strict = match_quote("HDL Cholesterol 52 mg/dL", words, threshold=0.9)
    assert r_strict.bbox is None
    assert r_strict.confidence == r_default.confidence


# ─── multi-page words: caller is responsible for filtering ─────────────


def test_match_does_not_cross_pages_implicitly():
    """If the caller passes words from multiple pages, the matcher
    raises rather than silently returning a meaningless bbox.
    Callers should filter with words_on_page() first."""
    words = (
        _row(1, 100, words_with_x=[("alpha", 10, 50), ("beta", 60, 100)])
        + _row(2, 50, words_with_x=[("gamma", 10, 60)])
    )
    # All three tokens match, but they span pages 1 and 2.
    # The current implementation raises on the union — that's the
    # right behavior because a cross-page bbox is meaningless.
    import pytest
    with pytest.raises(ValueError, match="multiple pages"):
        match_quote("alpha beta gamma", words)
