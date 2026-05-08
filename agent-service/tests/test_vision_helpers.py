"""Unit tests for the pure helpers in :mod:`copilot.extraction.vision`.

The Anthropic vision call itself is integration-tested separately (it
needs a real API key + a sample PDF). What we lock in here is the
strip-and-hydrate contract: the tool schema we hand Anthropic does
NOT include fields the pipeline owns (document_reference_id,
source_id, bbox), AND the post-call hydration restores them so full
Pydantic validation passes.

If a future change adds a new server-owned field to a schema and
forgets to strip it from the tool input_schema, these tests catch
it: the model could then either invent the field (data integrity
bug) or omit it (validation failure on every call).
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

import pytest
from pydantic import ValidationError

from copilot.extraction.vision import (
    SERVER_FILLED_CITATION,
    SERVER_FILLED_TOP_LEVEL,
    _hydrate_and_validate,
    build_extraction_tool_schema,
    hydrate_with_document_id,
    parse_tool_use_response,
)
from copilot.schemas import IntakeFormExtraction, LabPdfExtraction


# ─── build_extraction_tool_schema ───────────────────────────────────────


def test_lab_tool_schema_omits_document_reference_id():
    schema = build_extraction_tool_schema(LabPdfExtraction)
    assert "document_reference_id" not in schema["properties"]
    assert "document_reference_id" not in schema.get("required", [])


def test_lab_tool_schema_keeps_other_top_level_fields():
    schema = build_extraction_tool_schema(LabPdfExtraction)
    # Sanity: results / warnings / issuing_lab / accession_number all stay
    for kept in ("results", "warnings", "issuing_lab", "accession_number"):
        assert kept in schema["properties"], f"{kept} should remain in tool schema"


def test_intake_tool_schema_omits_document_reference_id():
    """The Anthropic tool schema strips document_reference_id (the
    server fills it in after extraction; we don't want the LLM
    inventing one). The fields the LLM is responsible for stay in
    `properties`; demographics + chief_concern are *contractually*
    required via the model_validator's auto-warn rather than
    Pydantic-required (see test_schemas.py for the auto-warn proofs),
    so the tool schema does not list them under `required` — but
    they must still appear under `properties` so the LLM can fill
    them in."""
    schema = build_extraction_tool_schema(IntakeFormExtraction)
    assert "document_reference_id" not in schema["properties"]
    assert "document_reference_id" not in schema.get("required", [])
    assert "demographics" in schema["properties"]
    assert "chief_concern" in schema["properties"]


def test_citation_schema_omits_source_id_and_bbox():
    schema = build_extraction_tool_schema(LabPdfExtraction)
    citation_def = schema["$defs"]["SourceCitation"]
    for stripped in SERVER_FILLED_CITATION:
        assert stripped not in citation_def["properties"], (
            f"{stripped} should not be in the LLM-facing SourceCitation schema"
        )
        assert stripped not in citation_def.get("required", [])


def test_citation_schema_keeps_quote_and_field_id():
    schema = build_extraction_tool_schema(LabPdfExtraction)
    citation_def = schema["$defs"]["SourceCitation"]
    for kept in ("source_type", "page_or_section", "field_or_chunk_id", "quote_or_value"):
        assert kept in citation_def["properties"], f"{kept} should remain"


def test_build_does_not_mutate_source_schema():
    """Callers should be free to rebuild the tool schema on every call
    without aliasing — the post-strip schema must NOT propagate back
    into the Pydantic model's class-level cache."""
    raw_before = LabPdfExtraction.model_json_schema()
    _ = build_extraction_tool_schema(LabPdfExtraction)
    raw_after = LabPdfExtraction.model_json_schema()
    assert raw_before == raw_after
    # And the original schema still has document_reference_id
    assert "document_reference_id" in raw_after["properties"]


# ─── hydrate_with_document_id ───────────────────────────────────────────


def test_hydrate_sets_top_level_document_reference_id():
    raw: dict = {"results": [], "warnings": ["no labs found"]}
    out = hydrate_with_document_id(raw, "doc-uuid-1234")
    assert out["document_reference_id"] == "doc-uuid-1234"


def test_hydrate_walks_into_nested_citations():
    """A LabPdfExtraction has citations one level deep (each
    LabResult.citation). The walk must reach them."""
    raw: dict = {
        "results": [
            {
                "test_name": "HDL",
                "value": 52.0,
                "unit": "mg/dL",
                "reference_range": {
                    "comparator": "between",
                    "low": 40,
                    "high": 60,
                    "unit": "mg/dL",
                },
                "collection_date": "2026-04-15",
                "abnormal_flag": "N",
                "citation": {
                    # Note: source_id and source_type omitted by the LLM
                    "page_or_section": 1,
                    "field_or_chunk_id": "results[0]",
                    "quote_or_value": "HDL Cholesterol 52 mg/dL",
                },
            }
        ],
        "warnings": [],
    }
    out = hydrate_with_document_id(raw, "doc-uuid-1234")
    citation = out["results"][0]["citation"]
    assert citation["source_id"] == "doc-uuid-1234"
    assert citation["source_type"] == "DocumentReference"


def test_hydrate_walks_into_intake_form_nesting():
    """IntakeFormExtraction has citations nested 1-2 levels deep
    (Demographics.citation, ChiefConcern.citation, plus list-of-fact
    citations). The walk must reach all of them."""
    raw: dict = {
        "demographics": {
            "first_name": "Farrah",
            "last_name": "Rolle",
            "date_of_birth": "1972-06-14",
            "citation": {
                "page_or_section": 1,
                "field_or_chunk_id": "demographics",
                "quote_or_value": "Farrah Rolle, DOB 06/14/1972",
            },
        },
        "chief_concern": {
            "text": "annual physical",
            "citation": {
                "page_or_section": 1,
                "field_or_chunk_id": "chiefConcern",
                "quote_or_value": "annual physical",
            },
        },
        "medications": [
            {
                "name": "Lisinopril",
                "citation": {
                    "page_or_section": 2,
                    "field_or_chunk_id": "medications[0]",
                    "quote_or_value": "Lisinopril 20 mg",
                },
            }
        ],
        "allergies": [],
        "family_history": [],
        "warnings": [],
    }
    out = hydrate_with_document_id(raw, "doc-uuid-9999")
    assert out["demographics"]["citation"]["source_id"] == "doc-uuid-9999"
    assert out["chief_concern"]["citation"]["source_id"] == "doc-uuid-9999"
    assert out["medications"][0]["citation"]["source_id"] == "doc-uuid-9999"


def test_hydrate_does_not_overwrite_existing_source_type():
    """If the model emitted source_type (it shouldn't, but defense in
    depth), we don't clobber it — only fill if missing."""
    raw: dict = {
        "demographics": {
            "first_name": "X", "last_name": "Y", "date_of_birth": "2000-01-01",
            "citation": {
                "source_type": "DocumentReference",  # already correct
                "page_or_section": 1,
                "field_or_chunk_id": "demographics",
                "quote_or_value": "X Y",
            },
        },
        "warnings": ["no chief concern field"],
    }
    out = hydrate_with_document_id(raw, "doc-1")
    assert out["demographics"]["citation"]["source_type"] == "DocumentReference"


def test_hydrate_does_not_set_bbox():
    """The matcher owns bbox. Hydration must not touch it, so the
    matcher remains the single point of truth for coordinates."""
    raw: dict = {
        "demographics": {
            "first_name": "X", "last_name": "Y", "date_of_birth": "2000-01-01",
            "citation": {
                "page_or_section": 1,
                "field_or_chunk_id": "demographics",
                "quote_or_value": "X Y",
            },
        },
        "warnings": ["no chief concern field"],
    }
    out = hydrate_with_document_id(raw, "doc-1")
    assert "bbox" not in out["demographics"]["citation"]


# ─── _hydrate_and_validate (the full round-trip) ───────────────────────


def test_hydrate_and_validate_full_roundtrip_lab():
    raw: dict = {
        "results": [
            {
                "test_name": "HDL",
                "value": 52.0,
                "unit": "mg/dL",
                "reference_range": {
                    "comparator": "between",
                    "low": 40, "high": 60, "unit": "mg/dL",
                },
                "collection_date": "2026-04-15",
                "abnormal_flag": "N",
                "citation": {
                    "page_or_section": 1,
                    "field_or_chunk_id": "results[0]",
                    "quote_or_value": "HDL 52 mg/dL",
                },
            }
        ],
        "warnings": [],
    }
    extraction = _hydrate_and_validate(raw, "doc-uuid", LabPdfExtraction)
    assert isinstance(extraction, LabPdfExtraction)
    assert extraction.document_reference_id == "doc-uuid"
    assert extraction.results[0].citation.source_id == "doc-uuid"
    assert extraction.results[0].citation.source_type == "DocumentReference"
    assert extraction.results[0].citation.bbox is None  # set later by matcher


def test_hydrate_and_validate_propagates_validation_errors():
    """If the LLM omitted a required schema field (e.g. abnormal_flag),
    full Pydantic validation must fail loudly so the eval gate can
    catch it."""
    raw: dict = {
        "results": [
            {
                "test_name": "HDL",
                "value": 52.0,
                "unit": "mg/dL",
                "reference_range": {
                    "comparator": "between", "low": 40, "high": 60, "unit": "mg/dL",
                },
                "collection_date": "2026-04-15",
                # abnormal_flag MISSING
                "citation": {
                    "page_or_section": 1,
                    "field_or_chunk_id": "results[0]",
                    "quote_or_value": "HDL 52 mg/dL",
                },
            }
        ],
        "warnings": [],
    }
    with pytest.raises(ValidationError):
        _hydrate_and_validate(raw, "doc-uuid", LabPdfExtraction)


# ─── parse_tool_use_response ────────────────────────────────────────────


class _FakeBlock:
    """Mimics anthropic.types.ToolUseBlock just enough."""
    def __init__(self, type, name=None, input=None, text=None):
        self.type = type
        self.name = name
        self.input = input
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def test_parse_extracts_tool_use_block():
    response = _FakeResponse(content=[
        _FakeBlock(type="text", text="I'll extract this now."),
        _FakeBlock(type="tool_use", name="emit_lab_extraction",
                   input={"results": [], "warnings": ["empty"]}),
    ])
    out = parse_tool_use_response(response, "emit_lab_extraction")
    assert out == {"results": [], "warnings": ["empty"]}


def test_parse_works_on_dict_response():
    """Tests can use a plain dict instead of constructing fake blocks."""
    response = {
        "content": [
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "name": "emit_intake_extraction",
             "input": {"demographics": {"_dummy": True}, "warnings": []}},
        ]
    }
    out = parse_tool_use_response(response, "emit_intake_extraction")
    assert out["demographics"]["_dummy"] is True


def test_parse_raises_when_tool_not_called():
    """If the model returned a text response instead of a tool call,
    the pipeline must raise — silently swallowing this would mean
    extractions vanish without surfacing the failure."""
    response = _FakeResponse(content=[
        _FakeBlock(type="text", text="I cannot read this PDF."),
    ])
    with pytest.raises(RuntimeError, match="emit_lab_extraction"):
        parse_tool_use_response(response, "emit_lab_extraction")


def test_parse_raises_when_wrong_tool_called():
    """If the model called a different tool by name, fail loudly."""
    response = _FakeResponse(content=[
        _FakeBlock(type="tool_use", name="some_other_tool", input={}),
    ])
    with pytest.raises(RuntimeError, match="emit_lab_extraction"):
        parse_tool_use_response(response, "emit_lab_extraction")


def test_parse_raises_when_input_is_not_dict():
    response = _FakeResponse(content=[
        _FakeBlock(type="tool_use", name="emit_lab_extraction", input="not a dict"),
    ])
    with pytest.raises(RuntimeError, match="non-dict input"):
        parse_tool_use_response(response, "emit_lab_extraction")


# ─── invariants ────────────────────────────────────────────────────────


def test_server_filled_lists_are_disjoint():
    """A field can't be both top-level-server-filled AND citation-level-
    server-filled — that would mean the schema mutator could miss it."""
    assert not (set(SERVER_FILLED_TOP_LEVEL) & set(SERVER_FILLED_CITATION))
