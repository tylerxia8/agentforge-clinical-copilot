"""Citation envelope shared by lab + intake schemas.

Shape matches the PRD §5 citation contract:

    {source_type, source_id, page_or_section, field_or_chunk_id,
     quote_or_value}

with one extra (``bbox``) populated server-side after the vision
pass — the model emits the quote, our pdfplumber pass attaches the
coordinates. The model never decides bboxes; that would let it
hallucinate locations.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal["DocumentReference", "MedicationRequest", "Condition",
                     "AllergyIntolerance", "Encounter", "Observation",
                     "Patient", "Immunization", "Guideline"]


class BBox(BaseModel):
    """Word-level bounding box on a PDF page, in PDF user-space points
    (1/72 inch). Origin is bottom-left per the PDF spec.

    Populated by the post-vision pdfplumber pass — never by the LLM.
    Optional because rasterized scans fall back to a coarser
    image-pixel coordinate system handled separately by the
    extraction pipeline.
    """

    model_config = ConfigDict(extra="forbid")

    page: int = Field(..., ge=1, description="1-indexed page number")
    x0: float = Field(..., description="Left edge, PDF points")
    y0: float = Field(..., description="Bottom edge, PDF points")
    x1: float = Field(..., description="Right edge, PDF points")
    y1: float = Field(..., description="Top edge, PDF points")

    @model_validator(mode="after")
    def _check_box(self) -> "BBox":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError(
                f"bbox is degenerate or inverted: "
                f"x0={self.x0} x1={self.x1} y0={self.y0} y1={self.y1}"
            )
        return self


class SourceCitation(BaseModel):
    """Pointer back to where an extracted fact came from.

    The vision model emits ``quote_or_value`` — the literal text it
    saw. The pipeline's post-step attempts to match that quote in
    the pdfplumber word list to populate ``bbox``. If no match within
    the edit-distance budget, ``bbox`` is left None and the parent
    fact is demoted to ``extraction_confidence="low"``.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType = Field(
        ...,
        description="The kind of source. For lab/intake extractions this "
                    "is always 'DocumentReference'; for guideline-grounded "
                    "answers it's 'Guideline'; for chart-grounded facts it "
                    "matches the FHIR resourceType.",
    )
    source_id: str = Field(
        ...,
        min_length=1,
        description="DocumentReference uuid, FHIR resource uuid, or "
                    "guideline chunk id.",
    )
    page_or_section: int | str | None = Field(
        None,
        description="1-indexed page (for PDFs) or section heading (for "
                    "guidelines / structured FHIR fields). None if not "
                    "applicable.",
    )
    field_or_chunk_id: str = Field(
        ...,
        min_length=1,
        description="Stable id we can use to look up bbox on click. For "
                    "lab/intake this is the schema field path "
                    "(e.g. 'labResults[3]'). For guideline this is the "
                    "chunk id.",
    )
    quote_or_value: str = Field(
        ...,
        min_length=1,
        description="The literal text the source contained. For PDFs this "
                    "is what the vision model claims to have read; the "
                    "post-pass verifies it against pdfplumber output.",
    )
    bbox: BBox | None = Field(
        None,
        description="Bounding box on the source page. Populated by the "
                    "post-vision pdfplumber match step, NOT by the LLM. "
                    "None for non-PDF sources or when no match was found.",
    )
