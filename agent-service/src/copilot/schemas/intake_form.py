"""Schema for patient intake form extractions.

Required fields per the W2 PRD ("Required intake fields include
demographics fields, chief concern, current medications, allergies,
family history, and source citation.") plus per-section citations and
the same extraction_confidence demotion path.

Intake forms are messier than lab PDFs — handwriting, free-text
fields, sections that may be entirely blank. The schema accepts a
mostly-empty form so long as the warnings list explains what was
missing. The agent's writeback layer decides what to actually persist
based on extraction_confidence + the operator review step.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from copilot.schemas.citations import SourceCitation

ExtractionConfidence = Literal["high", "medium", "low"]
Sex = Literal["male", "female", "other", "unknown"]
AllergyType = Literal["medication", "food", "environmental", "other"]
AllergySeverity = Literal["mild", "moderate", "severe", "unknown"]
FamilyRelation = Literal["mother", "father", "sibling", "child",
                         "maternal_grandparent", "paternal_grandparent",
                         "other"]


class Demographics(BaseModel):
    """Patient demographics block as written on the intake form.

    Required: first name, last name, date_of_birth. Everything else is
    optional because intake forms vary widely. The fields here are the
    agent's read of the form — they are NOT trusted as authoritative;
    the writeback step compares against the existing OpenEMR
    ``patient_data`` row and only fills blanks, never overwrites.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    date_of_birth: date
    sex: Sex | None = None
    phone: str | None = Field(
        None,
        description="As written. Normalization to E.164 happens at "
                    "writeback; the schema accepts any non-empty string.",
    )
    email: str | None = None
    address_line: str | None = None
    city: str | None = None
    state: str | None = Field(
        None, description="US state two-letter code or full name as "
                          "written; not normalized at the schema layer."
    )
    postal_code: str | None = None
    citation: SourceCitation = Field(
        ...,
        description="Where on the form the demographics block was read.",
    )


class ChiefConcern(BaseModel):
    """The 'Chief concern' / 'Reason for visit' free-text field.

    Single string because most intake forms have one such field. If
    the form has multiple (e.g. 'reason' + 'history of present
    illness'), the extractor concatenates them with a separator and
    surfaces a warning.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    citation: SourceCitation = Field(...)


class IntakeMedication(BaseModel):
    """One medication as the patient wrote it down.

    Deliberately permissive: `dose` and `frequency` are free-text
    strings because patients write things like 'a small white pill
    twice daily'. We do NOT attempt to parse these into structured
    SIG codes at extraction time — that's the job of a downstream
    medication-reconciliation step (out of W2 scope).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    dose: str | None = None
    frequency: str | None = None
    indication: str | None = Field(
        None, description="What the patient says they take it for; often "
                          "absent on intake forms."
    )
    citation: SourceCitation = Field(...)


class Allergy(BaseModel):
    """One allergy as written. Substance is required; reaction and
    severity are commonly absent on intake forms and that's OK."""

    model_config = ConfigDict(extra="forbid")

    substance: str = Field(..., min_length=1)
    allergy_type: AllergyType = Field(default="other")
    reaction: str | None = Field(
        None, description="Free-text reaction description (e.g. 'rash', "
                          "'anaphylaxis')."
    )
    severity: AllergySeverity = Field(default="unknown")
    citation: SourceCitation = Field(...)


class FamilyHistoryEntry(BaseModel):
    """One family-history line. Captures the relation, the condition,
    and (when written) the age of onset."""

    model_config = ConfigDict(extra="forbid")

    relation: FamilyRelation
    condition: str = Field(..., min_length=1)
    age_of_onset: int | None = Field(
        None, ge=0, le=120,
        description="Age the relative developed the condition, if "
                    "the form captures it. Null otherwise.",
    )
    deceased: bool | None = Field(
        None,
        description="True if the form indicates the relative is deceased "
                    "from this condition. Null if the form doesn't say.",
    )
    citation: SourceCitation = Field(...)


class IntakeFormExtraction(BaseModel):
    """Top-level wrapper for one intake-form extraction call."""

    model_config = ConfigDict(extra="forbid")

    document_reference_id: str = Field(
        ...,
        min_length=1,
        description="OpenEMR DocumentReference uuid the form was stored "
                    "as. Filled in server-side, not by the LLM.",
    )
    intake_date: date | None = Field(
        None,
        description="Date the patient filled out the form, as written. "
                    "Often missing.",
    )
    demographics: Demographics | None = Field(
        None,
        description="Patient demographics block. Optional because "
                    "non-form sources (e.g. an XLSX workbook with no "
                    "Patient sheet) may not surface a clean demographics "
                    "object — extractor should add a warning instead.",
    )
    chief_concern: ChiefConcern | None = Field(
        None,
        description="Optional because some intake forms separate 'reason "
                    "for visit' into a different document. If absent, "
                    "set warnings.",
    )
    medications: list[IntakeMedication] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)
    family_history: list[FamilyHistoryEntry] = Field(default_factory=list)
    extraction_confidence: ExtractionConfidence = Field(default="high")
    warnings: list[str] = Field(
        default_factory=list,
        description="Document-level extraction issues. Required if any "
                    "section was unreadable, intentionally blank, or if "
                    "the extractor couldn't tell.",
    )

    @field_validator("intake_date", mode="before")
    @classmethod
    def _strip_quoted_date(cls, v: object) -> object:
        """Some upstream LLM tool-call paths (DOCX referral, XLSX
        workbook) emit dates as a string with embedded quote
        characters (e.g. ``'"2026-05-06"'``) — looks like a
        double-encoding bug somewhere between the model's tool
        emission and our deserialization. Strip surrounding
        whitespace + matched quotes so Pydantic's date parser
        sees the bare ISO form."""
        if isinstance(v, str):
            stripped = v.strip()
            if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ('"', "'"):
                return stripped[1:-1]
        return v

    @model_validator(mode="after")
    def _missing_required_sections_must_warn(self) -> "IntakeFormExtraction":
        # Demographics is required by the schema; chief_concern is a
        # softer requirement but the PRD lists it. If chief_concern is
        # None, we require an explicit warning so the operator UI sees
        # it rather than silently displaying an intake with no reason
        # for visit.
        if self.chief_concern is None:
            chief_concern_warned = any(
                "chief concern" in w.lower() or "reason for visit" in w.lower()
                for w in self.warnings
            )
            if not chief_concern_warned:
                raise ValueError(
                    "chief_concern is None but no warning explains why; "
                    "add a warning like 'chief concern field blank on "
                    "page 1' so the operator UI can surface it"
                )
        return self
