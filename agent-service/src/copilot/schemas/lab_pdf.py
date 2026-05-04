"""Schema for lab PDF extractions.

Required fields per the W2 PRD ("Required lab fields include at least
test name, value, unit, reference range, collection date, abnormal
flag, and source citation.") plus our citation envelope and an
extraction_confidence demotion path.

The shape is one :class:`LabResult` per individual analyte (HDL, LDL,
HbA1c, etc), wrapped in a :class:`LabPdfExtraction` that carries
report-level metadata (the order date, accession number, the issuing
lab) and a top-level warnings list for document-wide issues the
vision pass needs to surface.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from copilot.schemas.citations import SourceCitation

AbnormalFlag = Literal["LL", "L", "N", "H", "HH"]
"""HL7 v2 abnormal flags. LL = critical low, L = low, N = within
range, H = high, HH = critical high. We force a closed enum so the
extraction never invents new flag codes."""

RangeComparator = Literal["<", ">", "<=", ">=", "between"]
"""How a reference range expresses its bound. ``between`` means both
``low`` and ``high`` are populated. ``<`` / ``<=`` use ``high`` only
(upper bound). ``>`` / ``>=`` use ``low`` only (lower bound)."""

ExtractionConfidence = Literal["high", "medium", "low"]


class ReferenceRange(BaseModel):
    """Lab reference interval. At least one of low/high MUST be
    populated, and the comparator must agree with which side is set."""

    model_config = ConfigDict(extra="forbid")

    comparator: RangeComparator = Field(
        ...,
        description="Which side of the range is bounded. 'between' means "
                    "both ends; '<' or '<=' uses high only; '>' or '>=' "
                    "uses low only.",
    )
    low: float | None = None
    high: float | None = None
    unit: str = Field(
        ...,
        min_length=1,
        description="Reference range unit (e.g. 'mg/dL'). Should match "
                    "the LabResult's unit; mismatches are flagged by the "
                    "factually_consistent eval rubric.",
    )

    @model_validator(mode="after")
    def _bounds_match_comparator(self) -> "ReferenceRange":
        if self.comparator == "between":
            if self.low is None or self.high is None:
                raise ValueError(
                    "comparator='between' requires both low and high"
                )
            if self.low >= self.high:
                raise ValueError(
                    f"reference range low ({self.low}) must be < high "
                    f"({self.high})"
                )
        elif self.comparator in ("<", "<="):
            if self.high is None:
                raise ValueError(
                    f"comparator={self.comparator!r} requires high"
                )
            if self.low is not None:
                raise ValueError(
                    f"comparator={self.comparator!r} disallows low"
                )
        else:  # > or >=
            if self.low is None:
                raise ValueError(
                    f"comparator={self.comparator!r} requires low"
                )
            if self.high is not None:
                raise ValueError(
                    f"comparator={self.comparator!r} disallows high"
                )
        return self


class LabResult(BaseModel):
    """One analyte's result on a lab report.

    Per-fact citation: ``citation`` points at the row/region in the
    PDF where this analyte was extracted. The bounding-box overlay
    highlights that whole row, not individual cells.
    """

    model_config = ConfigDict(extra="forbid")

    test_name: str = Field(
        ...,
        min_length=1,
        description="Human-readable test name as written on the report "
                    "(e.g. 'HDL Cholesterol', 'Hemoglobin A1c'). LOINC "
                    "code mapping happens server-side at the FHIR write "
                    "step; the schema does not require it.",
    )
    value: float = Field(
        ...,
        description="Numeric result. Non-numeric results (e.g. 'POSITIVE') "
                    "are out of scope for the lab-PDF schema; the "
                    "extractor should refuse those rows and surface a "
                    "warning at the document level.",
    )
    unit: str = Field(
        ...,
        min_length=1,
        description="Unit as written (e.g. 'mg/dL', '%', 'mmol/L'). UCUM "
                    "normalization happens at FHIR write time.",
    )
    reference_range: ReferenceRange = Field(
        ...,
        description="The reference interval printed on the report. "
                    "Required because the abnormal_flag interpretation "
                    "depends on it.",
    )
    collection_date: date = Field(
        ...,
        description="Specimen collection date as printed on the report. "
                    "If only a report/issue date is available, the "
                    "extractor may fall back to that AND set "
                    "extraction_confidence='medium'.",
    )
    abnormal_flag: AbnormalFlag = Field(
        ...,
        description="The flag printed next to the value (LL/L/N/H/HH). "
                    "If absent on the report, the extractor must compute "
                    "it from value vs reference_range — it is never "
                    "left null.",
    )
    citation: SourceCitation = Field(
        ...,
        description="Where on the source PDF this result was read.",
    )
    extraction_confidence: ExtractionConfidence = Field(
        default="high",
        description="Demoted to 'medium' or 'low' by the post-vision "
                    "pipeline if the citation quote can't be matched in "
                    "the pdfplumber word output. Low-confidence results "
                    "are surfaced as warnings, NOT written to the chart.",
    )


class LabPdfExtraction(BaseModel):
    """Top-level wrapper for one lab-PDF extraction call."""

    model_config = ConfigDict(extra="forbid")

    document_reference_id: str = Field(
        ...,
        min_length=1,
        description="OpenEMR DocumentReference uuid the PDF was stored as. "
                    "Filled in by the agent service before invoking the "
                    "vision model — the model does not see this.",
    )
    issuing_lab: str | None = Field(
        None,
        description="Lab name as printed on the report (e.g. "
                    "'Quest Diagnostics'). Optional; some reports omit it.",
    )
    accession_number: str | None = Field(
        None,
        description="Lab-side accession number printed on the report.",
    )
    results: list[LabResult] = Field(
        default_factory=list,
        description="One entry per analyte. May be empty if the document "
                    "is unreadable; populate ``warnings`` in that case.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Document-level extraction issues: e.g. 'page 2 "
                    "unreadable', 'reference ranges missing on this lab', "
                    "'multiple specimens on one report'. Surfaced to the "
                    "operator UI, never written to the chart silently.",
    )

    @model_validator(mode="after")
    def _empty_extraction_must_have_warnings(self) -> "LabPdfExtraction":
        if not self.results and not self.warnings:
            raise ValueError(
                "empty extraction must include at least one warning "
                "explaining why no results were extracted"
            )
        return self
