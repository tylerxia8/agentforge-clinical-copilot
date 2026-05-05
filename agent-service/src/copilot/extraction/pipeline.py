"""High-level extraction utilities composed from the lower-level
modules in this package.

- :func:`attach_bboxes` — walk every citation in a validated
  extraction, look up its quote against the page words via the
  matcher, and attach the resulting bbox. Demotes the owning fact's
  ``extraction_confidence`` to ``"low"`` on no-match, surfacing
  un-anchored facts as items the operator should review.
- :func:`iter_citations` — generator over ``(owner, citation)``
  pairs in a validated extraction. The owner is the parent fact
  (LabResult, Demographics, ChiefConcern, IntakeMedication, etc.)
  whose extraction_confidence we touch on no-match.

Both functions operate on already-validated Pydantic models —
running them on raw dicts will fail. The vision call's
:func:`_hydrate_and_validate` is the validation gate that comes
before this layer.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from copilot.extraction.matcher import match_quote
from copilot.extraction.pdf import extract_words, words_on_page
from copilot.observability import langfuse_client
from copilot.schemas import IntakeFormExtraction, LabPdfExtraction

logger = logging.getLogger(__name__)


def attach_bboxes(
    extraction: LabPdfExtraction | IntakeFormExtraction,
    pdf_bytes: bytes,
) -> dict[str, int]:
    """Walk the extraction's citations and attach pdfplumber-derived
    bboxes. Returns a small summary dict ``{walked, matched}`` for
    telemetry (also emitted as a Langfuse event).

    Mutates ``extraction`` in place — Pydantic V2 BaseModels are
    mutable unless ``frozen=True``, and our citation/fact models
    aren't frozen, so this is safe and preserves the validated
    object's identity for the caller.
    """

    try:
        words = extract_words(pdf_bytes)
    except Exception:  # noqa: BLE001 — degrade, never fail the extraction
        logger.warning(
            "pdfplumber word extraction failed; bbox matching skipped",
            exc_info=True,
        )
        return {"walked": 0, "matched": 0}

    walked = 0
    matched = 0
    for owner, citation in iter_citations(extraction):
        page = citation.page_or_section
        if not isinstance(page, int):
            # Non-PDF citations (sections, top-level fields) skip
            # bbox matching cleanly.
            continue
        result = match_quote(citation.quote_or_value, words_on_page(words, page))
        walked += 1
        if result.bbox is not None:
            citation.bbox = result.bbox  # type: ignore[misc]
            matched += 1
        else:
            current = getattr(owner, "extraction_confidence", None)
            if current is not None and current != "low":
                owner.extraction_confidence = "low"  # type: ignore[misc]

    if langfuse_client is not None and walked:
        try:
            langfuse_client.create_event(
                name="extraction.bbox_match_summary",
                metadata={
                    "walked": walked,
                    "matched": matched,
                    "match_rate": matched / walked,
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("langfuse bbox event emit failed", exc_info=True)

    return {"walked": walked, "matched": matched}


def iter_citations(
    extraction: Any,
) -> Iterator[tuple[Any, Any]]:
    """Yield ``(owner, citation)`` pairs for every SourceCitation in
    a validated extraction.

    Owner is the parent fact whose ``extraction_confidence`` we may
    demote on no-match. For top-level objects without an owning fact
    (e.g. an intake's chief_concern itself), the owner IS that
    object — the demotion lands on the top-level model when the
    field exists there.
    """
    if isinstance(extraction, LabPdfExtraction):
        for result in extraction.results:
            yield result, result.citation
        return

    if isinstance(extraction, IntakeFormExtraction):
        yield extraction.demographics, extraction.demographics.citation
        if extraction.chief_concern is not None:
            yield extraction.chief_concern, extraction.chief_concern.citation
        for med in extraction.medications:
            yield med, med.citation
        for allergy in extraction.allergies:
            yield allergy, allergy.citation
        for entry in extraction.family_history:
            yield entry, entry.citation
