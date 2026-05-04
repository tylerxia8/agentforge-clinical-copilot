"""Strict extraction schemas for Week 2 multimodal ingestion.

Schemas double as Pydantic validators (over the LLM's tool-call
output) and as Anthropic tool ``input_schema`` definitions (we hand
``Model.model_json_schema()`` to the SDK). They are the contract
between the vision pass and the rest of the pipeline.

Two top-level extraction shapes:

- :class:`LabPdfExtraction` — a lab report PDF, expanded into a list
  of :class:`LabResult` rows with reference ranges + abnormal flags.
- :class:`IntakeFormExtraction` — a patient intake form, expanded
  into demographics + chief concern + meds + allergies + family
  history.

Both shapes carry a per-result :class:`SourceCitation` whose
``field_or_chunk_id`` plus ``page_or_section`` are what the chat
panel uses to render the bounding-box overlay (W2_ARCHITECTURE.md
§4). The vision model fills ``quote_or_value``; the post-extraction
pipeline (pdfplumber pass) attaches ``bbox``.

Per-fact citations, not per-field. A row in a lab table has one
source location; we don't ask Claude to point at the value cell
separately from the unit cell, because (a) it would explode the
schema and slow tool-use down, and (b) the bounding-box overlay
highlights the whole row anyway.
"""

from copilot.schemas.citations import BBox, SourceCitation, SourceType
from copilot.schemas.intake_form import (
    Allergy,
    ChiefConcern,
    Demographics,
    FamilyHistoryEntry,
    IntakeFormExtraction,
    IntakeMedication,
)
from copilot.schemas.lab_pdf import (
    AbnormalFlag,
    LabPdfExtraction,
    LabResult,
    RangeComparator,
    ReferenceRange,
)

__all__ = [
    "AbnormalFlag",
    "Allergy",
    "BBox",
    "ChiefConcern",
    "Demographics",
    "FamilyHistoryEntry",
    "IntakeFormExtraction",
    "IntakeMedication",
    "LabPdfExtraction",
    "LabResult",
    "RangeComparator",
    "ReferenceRange",
    "SourceCitation",
    "SourceType",
]
