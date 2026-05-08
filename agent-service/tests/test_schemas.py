"""Validation tests for the W2 extraction schemas.

These tests are also the documentation for what each schema enforces.
The W2 ``schema_valid`` eval rubric depends on these constraints:
adding a required field without updating the tests, or relaxing a
``Literal`` enum, will cause ``schema_valid`` to drift in the eval
suite.
"""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENEMR_BASE_URL", "http://localhost:8080")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_ID", "test")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_SECRET", "test")
os.environ.setdefault("OPENEMR_SERVICE_USERNAME", "test")
os.environ.setdefault("OPENEMR_SERVICE_PASSWORD", "test")
os.environ.setdefault("AGENT_SHARED_SECRET", "test-secret-test-secret")

import pytest
from pydantic import ValidationError

from copilot.schemas import (
    Allergy,
    BBox,
    ChiefConcern,
    Demographics,
    FamilyHistoryEntry,
    IntakeFormExtraction,
    IntakeMedication,
    LabPdfExtraction,
    LabResult,
    ReferenceRange,
    SourceCitation,
)


# ─── helpers ────────────────────────────────────────────────────────────

def _citation(field_path: str = "labResults[0]", quote: str = "HDL 52") -> SourceCitation:
    return SourceCitation(
        source_type="DocumentReference",
        source_id="doc-uuid-1234",
        page_or_section=1,
        field_or_chunk_id=field_path,
        quote_or_value=quote,
    )


def _ref_range(low: float | None = 40, high: float | None = 60,
               comparator: str = "between") -> ReferenceRange:
    return ReferenceRange(
        comparator=comparator,  # type: ignore[arg-type]
        low=low,
        high=high,
        unit="mg/dL",
    )


def _hdl_result(**overrides) -> LabResult:
    base = dict(
        test_name="HDL Cholesterol",
        value=52.0,
        unit="mg/dL",
        reference_range=_ref_range(),
        collection_date=date(2026, 4, 15),
        abnormal_flag="N",
        citation=_citation(),
    )
    base.update(overrides)
    return LabResult(**base)


def _demographics(**overrides) -> Demographics:
    base = dict(
        first_name="Farrah",
        last_name="Rolle",
        date_of_birth=date(1972, 6, 14),
        citation=_citation("demographics", "Farrah Rolle, DOB 06/14/1972"),
    )
    base.update(overrides)
    return Demographics(**base)


# ─── SourceCitation ─────────────────────────────────────────────────────

def test_source_citation_minimum_valid():
    c = _citation()
    assert c.source_type == "DocumentReference"
    assert c.bbox is None  # populated server-side, not by the LLM


def test_source_citation_empty_quote_fails():
    with pytest.raises(ValidationError):
        SourceCitation(
            source_type="DocumentReference",
            source_id="doc-1",
            page_or_section=1,
            field_or_chunk_id="x",
            quote_or_value="",  # empty → must reject
        )


def test_source_citation_empty_field_id_fails():
    with pytest.raises(ValidationError):
        SourceCitation(
            source_type="DocumentReference",
            source_id="doc-1",
            page_or_section=1,
            field_or_chunk_id="",
            quote_or_value="HDL 52",
        )


def test_source_citation_invalid_source_type_fails():
    with pytest.raises(ValidationError):
        SourceCitation(
            source_type="MadeUpResource",  # not in the Literal enum
            source_id="doc-1",
            page_or_section=1,
            field_or_chunk_id="x",
            quote_or_value="HDL 52",
        )


def test_source_citation_extra_field_forbidden():
    with pytest.raises(ValidationError):
        SourceCitation(
            source_type="DocumentReference",
            source_id="doc-1",
            page_or_section=1,
            field_or_chunk_id="x",
            quote_or_value="HDL 52",
            extra_field="should not be allowed",  # type: ignore[call-arg]
        )


# ─── BBox ───────────────────────────────────────────────────────────────

def test_bbox_valid_rectangle():
    b = BBox(page=1, x0=72, y0=100, x1=200, y1=120)
    assert b.page == 1


def test_bbox_inverted_x_fails():
    with pytest.raises(ValidationError):
        BBox(page=1, x0=200, y0=100, x1=72, y1=120)


def test_bbox_inverted_y_fails():
    with pytest.raises(ValidationError):
        BBox(page=1, x0=72, y0=200, x1=100, y1=100)


def test_bbox_zero_width_fails():
    with pytest.raises(ValidationError):
        BBox(page=1, x0=72, y0=100, x1=72, y1=120)


def test_bbox_page_below_one_fails():
    with pytest.raises(ValidationError):
        BBox(page=0, x0=72, y0=100, x1=200, y1=120)


# ─── ReferenceRange ─────────────────────────────────────────────────────

def test_reference_range_between_valid():
    r = _ref_range(low=40, high=60, comparator="between")
    assert r.low == 40 and r.high == 60


def test_reference_range_between_requires_both_bounds():
    with pytest.raises(ValidationError):
        ReferenceRange(comparator="between", low=40, unit="mg/dL")


def test_reference_range_low_must_be_less_than_high():
    with pytest.raises(ValidationError):
        ReferenceRange(comparator="between", low=60, high=40, unit="mg/dL")


def test_reference_range_lt_requires_high_only():
    r = ReferenceRange(comparator="<", high=200, unit="mg/dL")
    assert r.high == 200 and r.low is None


def test_reference_range_lt_with_low_fails():
    with pytest.raises(ValidationError):
        ReferenceRange(comparator="<", low=40, high=200, unit="mg/dL")


def test_reference_range_gt_requires_low_only():
    r = ReferenceRange(comparator=">", low=40, unit="mg/dL")
    assert r.low == 40 and r.high is None


def test_reference_range_gt_with_high_fails():
    with pytest.raises(ValidationError):
        ReferenceRange(comparator=">", low=40, high=200, unit="mg/dL")


# ─── LabResult ──────────────────────────────────────────────────────────

def test_lab_result_minimum_valid():
    r = _hdl_result()
    assert r.test_name == "HDL Cholesterol"
    assert r.extraction_confidence == "high"  # default


def test_lab_result_missing_test_name_fails():
    with pytest.raises(ValidationError):
        _hdl_result(test_name="")


def test_lab_result_missing_unit_fails():
    with pytest.raises(ValidationError):
        _hdl_result(unit="")


def test_lab_result_missing_citation_fails():
    with pytest.raises(ValidationError):
        LabResult(
            test_name="HDL",
            value=52.0,
            unit="mg/dL",
            reference_range=_ref_range(),
            collection_date=date(2026, 4, 15),
            abnormal_flag="N",
        )  # type: ignore[call-arg]


def test_lab_result_invalid_abnormal_flag_fails():
    with pytest.raises(ValidationError):
        _hdl_result(abnormal_flag="HIGH")


def test_lab_result_extra_field_forbidden():
    with pytest.raises(ValidationError):
        LabResult(
            test_name="HDL",
            value=52.0,
            unit="mg/dL",
            reference_range=_ref_range(),
            collection_date=date(2026, 4, 15),
            abnormal_flag="N",
            citation=_citation(),
            interpretation="borderline",  # type: ignore[call-arg]
        )


def test_lab_result_invalid_confidence_fails():
    with pytest.raises(ValidationError):
        _hdl_result(extraction_confidence="best_effort")


# ─── LabPdfExtraction ───────────────────────────────────────────────────

def test_lab_pdf_extraction_minimum_valid():
    ex = LabPdfExtraction(
        document_reference_id="doc-uuid-1234",
        results=[_hdl_result()],
    )
    assert len(ex.results) == 1
    assert ex.warnings == []


def test_lab_pdf_extraction_empty_with_warnings_valid():
    """An unreadable PDF is acceptable IF the warnings explain why."""
    ex = LabPdfExtraction(
        document_reference_id="doc-uuid-1234",
        warnings=["page 1 is rotated 90 degrees and OCR failed"],
    )
    assert ex.results == []


def test_lab_pdf_extraction_empty_without_warnings_fails():
    with pytest.raises(ValidationError):
        LabPdfExtraction(document_reference_id="doc-uuid-1234")


def test_lab_pdf_extraction_missing_document_reference_id_fails():
    with pytest.raises(ValidationError):
        LabPdfExtraction(results=[_hdl_result()])  # type: ignore[call-arg]


# ─── Demographics ───────────────────────────────────────────────────────

def test_demographics_minimum_valid():
    d = _demographics()
    assert d.first_name == "Farrah"


def test_demographics_missing_first_name_fails():
    with pytest.raises(ValidationError):
        _demographics(first_name="")


def test_demographics_invalid_sex_fails():
    with pytest.raises(ValidationError):
        _demographics(sex="M")  # must be 'male' / 'female' / 'other' / 'unknown'


def test_demographics_missing_citation_fails():
    with pytest.raises(ValidationError):
        Demographics(
            first_name="Farrah",
            last_name="Rolle",
            date_of_birth=date(1972, 6, 14),
        )  # type: ignore[call-arg]


# ─── IntakeMedication / Allergy / FamilyHistoryEntry ───────────────────

def test_intake_medication_name_required():
    with pytest.raises(ValidationError):
        IntakeMedication(name="", citation=_citation())


def test_allergy_default_severity_is_unknown():
    a = Allergy(substance="penicillin", citation=_citation())
    assert a.severity == "unknown"
    assert a.allergy_type == "other"  # default


def test_allergy_invalid_type_fails():
    with pytest.raises(ValidationError):
        Allergy(substance="penicillin", allergy_type="nasal", citation=_citation())


def test_family_history_invalid_relation_fails():
    with pytest.raises(ValidationError):
        FamilyHistoryEntry(
            relation="cousin",  # not in the closed enum
            condition="diabetes",
            citation=_citation(),
        )


def test_family_history_age_of_onset_negative_fails():
    with pytest.raises(ValidationError):
        FamilyHistoryEntry(
            relation="father",
            condition="MI",
            age_of_onset=-3,
            citation=_citation(),
        )


# ─── IntakeFormExtraction ──────────────────────────────────────────────

def test_intake_form_extraction_minimum_valid():
    ex = IntakeFormExtraction(
        document_reference_id="doc-uuid-1234",
        demographics=_demographics(),
        chief_concern=ChiefConcern(
            text="Annual physical, follow-up on diabetes",
            citation=_citation("chiefConcern", "annual physical, follow-up on diabetes"),
        ),
    )
    assert ex.medications == []
    assert ex.allergies == []
    assert ex.family_history == []
    assert ex.warnings == []


def test_intake_form_extraction_missing_chief_concern_auto_warns():
    """If chief_concern is None and the operator didn't add a warning
    themselves, the model_validator must auto-append one. The W2
    schema_valid rubric considers a silently-empty intake (no reason
    for visit, no warning) to be a schema violation; this validator
    makes the violation impossible by making it self-document.

    Contract change since the v1 LLM-only ingestion: non-intake
    sources (XLSX workbooks, HL7 ADT, DOCX referrals) legitimately
    omit chief_concern. We *warn*, we don't reject."""
    ex = IntakeFormExtraction(
        document_reference_id="doc-uuid-1234",
        demographics=_demographics(),
        chief_concern=None,
        warnings=[],
    )
    assert ex.chief_concern is None
    assert any("chief_concern" in w.lower() for w in ex.warnings)


def test_intake_form_extraction_missing_chief_concern_with_warning_ok():
    """Operator-supplied warning that already covers the missing field
    is honored — the validator must not duplicate it."""
    ex = IntakeFormExtraction(
        document_reference_id="doc-uuid-1234",
        demographics=_demographics(),
        chief_concern=None,
        warnings=["chief concern field blank on page 1"],
    )
    assert ex.chief_concern is None
    # Validator must not duplicate-append a generic warning when the
    # operator already wrote a more specific one
    assert len(ex.warnings) == 1
    assert "blank on page 1" in ex.warnings[0]


def test_intake_form_extraction_missing_demographics_auto_warns():
    """Parallel guarantee for the demographics block. Same rationale:
    multi-format ingestion means demographics may not appear on every
    source (an HL7 lab feed has the patient in MSH, not a demographics
    block; an XLSX workbook may have only labs). The validator must
    surface this so the operator review screen doesn't render an
    intake with no patient identification AND no warning."""
    ex = IntakeFormExtraction(
        document_reference_id="doc-uuid-1234",
        chief_concern=ChiefConcern(
            text="check up",
            citation=_citation("chiefConcern", "check up"),
        ),
        warnings=[],
    )
    assert ex.demographics is None
    assert any("demographics" in w.lower() for w in ex.warnings)


def test_intake_form_extraction_extra_field_forbidden():
    with pytest.raises(ValidationError):
        IntakeFormExtraction(
            document_reference_id="doc-uuid-1234",
            demographics=_demographics(),
            chief_concern=ChiefConcern(
                text="check up",
                citation=_citation("chiefConcern", "check up"),
            ),
            insurance="BCBS",  # type: ignore[call-arg]
        )


# ─── JSON-Schema export sanity (used to define the Anthropic tool) ─────

def test_lab_pdf_extraction_json_schema_has_required_fields():
    """The Anthropic tool input_schema is derived from this. If a
    required field disappears from the JSON Schema, Claude's tool-use
    validation will silently let the LLM omit it — we want to know."""
    schema = LabPdfExtraction.model_json_schema()
    assert "document_reference_id" in schema["required"]
    # results and warnings have defaults so are not in `required`, but
    # the model_validator enforces "at least one of them populated"
    assert schema["additionalProperties"] is False


def test_intake_form_extraction_json_schema_forbids_extras():
    """document_reference_id is the only structurally-required field;
    demographics + chief_concern are *contractually* required (the
    model_validator auto-warns when missing) but Pydantic-required is
    just document_reference_id. extra=forbid is the strong invariant
    that protects against silent prompt drift adding new keys."""
    schema = IntakeFormExtraction.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "document_reference_id" in schema["required"]
