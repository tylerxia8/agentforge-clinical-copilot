"""Anthropic vision call for document extraction.

Wraps the Anthropic Messages API with PDF input + forced tool-use,
returning a validated Pydantic model. Two public entry points:

- :func:`extract_lab_pdf` — for `lab_pdf` doc type
- :func:`extract_intake_form` — for `intake_form` doc type

**Design: strip-and-hydrate.**

The vision model should not be allowed to invent these fields:

- ``document_reference_id`` at the top level (the OpenEMR doc UUID
  is known to the agent; the model never sees it)
- ``source_id`` on every nested :class:`SourceCitation` (same — the
  model can't be trusted to emit the right UUID)
- ``bbox`` on every nested :class:`SourceCitation` (the
  pdfplumber post-pass owns coordinates; the model never decides them)

The strategy: build the tool's JSON schema by taking the full
Pydantic model's schema and removing those properties + their
``required`` entries. The model emits everything else; the pipeline
hydrates the omitted fields server-side, then runs full Pydantic
validation on the merged dict. If the model tries to emit one of
the stripped fields anyway, Anthropic's tool-use enforcement
rejects it (they're not in the schema).

The match step (:mod:`copilot.extraction.matcher`) runs separately,
on the validated result, to attach ``bbox`` to each citation.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from copilot.schemas import IntakeFormExtraction, LabPdfExtraction
from copilot.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ─── system prompts ─────────────────────────────────────────────────────


_LAB_SYSTEM_PROMPT = """\
You are a medical-document parser specialized in lab reports.

You will be shown one lab report PDF. Extract every analyte result
into the structured form defined by the emit_lab_extraction tool.

Rules:

1. For every extracted fact (lab result, document-level field), set \
quote_or_value to the LITERAL text you read on the page. The quote \
must appear verbatim somewhere on that page — do not paraphrase.
2. Set page_or_section to the 1-indexed page where the fact appears.
3. Set field_or_chunk_id to the schema field path (e.g. \
"results[0]" for the first result, "issuing_lab" for the lab name).
4. Set source_type to "DocumentReference". This is always the \
correct value for facts extracted from this PDF.
5. If a field is unreadable or absent on the page, DO NOT invent it. \
If the entire document is unreadable, return zero results and add a \
warning explaining why.
6. abnormal_flag must be one of LL / L / N / H / HH. If the report \
prints a flag, transcribe it. If not, compute it from the value vs \
the reference_range.
7. Do NOT include narrative interpretation, recommendations, or \
clinical commentary. Just the structured rows the schema asks for.
"""

_INTAKE_SYSTEM_PROMPT = """\
You are a medical-document parser specialized in patient intake forms.

You will be shown one intake form PDF. Extract demographics, chief \
concern, current medications, allergies, and family history into the \
structured form defined by the emit_intake_extraction tool.

Rules:

1. For every extracted fact, set quote_or_value to the LITERAL text \
you read on the form. The quote must appear verbatim on that page.
2. Set page_or_section to the 1-indexed page where the fact appears.
3. Set field_or_chunk_id to the schema field path (e.g. \
"demographics", "medications[2]", "allergies[0]").
4. Set source_type to "DocumentReference".
5. If a section is blank on the form, omit it from the corresponding \
list (leave the list empty) and add a warning explaining the section \
was blank. Do NOT invent entries.
6. If chief_concern is blank, leave it null AND add a warning \
mentioning "chief concern" so the operator UI surfaces it.
7. Free-text fields (chief_concern.text, medication.dose, allergy.reaction) \
keep the patient's literal wording — do NOT normalize, paraphrase, \
or expand abbreviations.
8. Do NOT include narrative interpretation. Just the structured \
sections the schema asks for.
"""


# ─── public entry points ────────────────────────────────────────────────


async def extract_lab_pdf(
    pdf_bytes: bytes, document_reference_id: str
) -> LabPdfExtraction:
    """Run the lab-PDF extraction pipeline (vision pass only).

    The post-vision bbox match step is the caller's job — see
    :mod:`copilot.extraction.matcher`. This function only handles
    the LLM round-trip + schema validation.
    """
    raw = await _call_vision_tool(
        pdf_bytes=pdf_bytes,
        system_prompt=_LAB_SYSTEM_PROMPT,
        tool_name="emit_lab_extraction",
        tool_description="Emit the structured lab extraction.",
        schema_cls=LabPdfExtraction,
    )
    return _hydrate_and_validate(raw, document_reference_id, LabPdfExtraction)


async def extract_intake_form(
    pdf_bytes: bytes, document_reference_id: str
) -> IntakeFormExtraction:
    """Run the intake-form extraction pipeline (vision pass only)."""
    raw = await _call_vision_tool(
        pdf_bytes=pdf_bytes,
        system_prompt=_INTAKE_SYSTEM_PROMPT,
        tool_name="emit_intake_extraction",
        tool_description="Emit the structured intake-form extraction.",
        schema_cls=IntakeFormExtraction,
    )
    return _hydrate_and_validate(raw, document_reference_id, IntakeFormExtraction)


# ─── pure helpers (unit-testable without an API call) ──────────────────


SERVER_FILLED_TOP_LEVEL: tuple[str, ...] = ("document_reference_id",)
"""Top-level fields the pipeline fills, never the model."""

SERVER_FILLED_CITATION: tuple[str, ...] = ("source_id", "bbox")
"""SourceCitation fields the pipeline fills, never the model."""


def build_extraction_tool_schema(schema_cls: Type[BaseModel]) -> dict[str, Any]:
    """Build the JSON Schema we hand to Anthropic as ``input_schema``.

    Starts from the Pydantic model's schema, then strips the fields
    the pipeline owns. Mutates a deep copy — the source schema is
    untouched.
    """
    import copy

    schema = copy.deepcopy(schema_cls.model_json_schema())

    # Drop top-level server-filled fields
    for field in SERVER_FILLED_TOP_LEVEL:
        _drop_property(schema, field)

    # Drop citation server-filled fields wherever SourceCitation
    # appears (it's referenced via $defs in nested locations).
    defs = schema.get("$defs", {})
    if "SourceCitation" in defs:
        for field in SERVER_FILLED_CITATION:
            _drop_property(defs["SourceCitation"], field)

    return schema


def hydrate_with_document_id(
    raw: dict[str, Any], document_reference_id: str
) -> dict[str, Any]:
    """Fill in the server-owned fields on a raw extraction dict.

    - Sets ``document_reference_id`` at the top level
    - Walks every nested SourceCitation-shaped dict and sets
      ``source_id`` to the doc UUID. ``source_type`` defaults to
      ``"DocumentReference"`` if missing.
    - Does NOT set ``bbox`` — that's the matcher's job.

    Returns the same dict (mutated in place) for chaining.
    """
    raw["document_reference_id"] = document_reference_id
    _walk_set_citations(raw, document_reference_id)
    return raw


def parse_tool_use_response(response: Any, tool_name: str) -> dict[str, Any]:
    """Pull the tool-use block out of an Anthropic Messages response.

    Accepts either an SDK response object (with ``.content`` blocks)
    or a dict (handy for tests). Raises ``RuntimeError`` if the model
    didn't call the expected tool.
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content", [])
    if content is None:
        raise RuntimeError(
            f"vision response has no content; got {type(response).__name__}"
        )

    for block in content:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        block_name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        block_input = getattr(block, "input", None) or (
            block.get("input") if isinstance(block, dict) else None
        )
        if block_type == "tool_use" and block_name == tool_name:
            if not isinstance(block_input, dict):
                raise RuntimeError(
                    f"tool_use block for {tool_name!r} has non-dict input: "
                    f"{type(block_input).__name__}"
                )
            return block_input

    raise RuntimeError(
        f"vision call did not return a tool_use block named {tool_name!r}"
    )


def _hydrate_and_validate(
    raw: dict[str, Any],
    document_reference_id: str,
    schema_cls: Type[T],
) -> T:
    """Hydrate the LLM's raw output with server-owned fields, then
    run full Pydantic validation. Raises ValidationError on schema
    drift — the eval gate's ``schema_valid`` rubric depends on this."""
    hydrated = hydrate_with_document_id(raw, document_reference_id)
    try:
        return schema_cls.model_validate(hydrated)
    except ValidationError as exc:
        # Re-raise with extra context: which model failed, and the
        # raw keys we got. Keeps the traceback useful when the eval
        # suite reports a schema_valid regression.
        logger.warning(
            "schema validation failed for %s: %s",
            schema_cls.__name__,
            exc.errors(include_url=False),
        )
        raise


# ─── network-side helper (not directly unit-testable) ──────────────────


async def _call_vision_tool(
    *,
    pdf_bytes: bytes,
    system_prompt: str,
    tool_name: str,
    tool_description: str,
    schema_cls: Type[BaseModel],
) -> dict[str, Any]:
    """Make the actual Anthropic Messages call with the PDF as a
    document content block and the extraction tool as a forced
    tool-use. Returns the tool's input dict."""

    # Imported lazily so the unit tests for the helpers don't need
    # the SDK installed (they don't, but it costs nothing to be safe
    # against a future test that runs without the dep).
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    tool_schema = build_extraction_tool_schema(schema_cls)

    response = await client.messages.create(
        model=settings.model_id,
        max_tokens=settings.max_tokens,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Extract this document using the {tool_name} tool. "
                            "Cite the literal text you read for every fact. "
                            "If the document is unreadable, return an empty "
                            "extraction with a warning."
                        ),
                    },
                ],
            }
        ],
        tools=[
            {
                "name": tool_name,
                "description": tool_description,
                "input_schema": tool_schema,
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
    )

    return parse_tool_use_response(response, tool_name)


async def _call_text_tool(
    *,
    prose: str,
    system_prompt: str,
    tool_name: str,
    tool_description: str,
    schema_cls: Type[BaseModel],
) -> dict[str, Any]:
    """Sibling of :func:`_call_vision_tool` for plain-text inputs
    (DOCX → text, XLSX → CSV-flavoured text). Same forced tool-use
    pattern; just swaps the document content block for a text block."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    tool_schema = build_extraction_tool_schema(schema_cls)
    response = await client.messages.create(
        model=settings.model_id,
        max_tokens=settings.max_tokens,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Extract this document using the {tool_name} tool. "
                            "Cite the literal text you read for every fact.\n\n"
                            "--- BEGIN DOCUMENT ---\n"
                            f"{prose}\n"
                            "--- END DOCUMENT ---"
                        ),
                    },
                ],
            }
        ],
        tools=[
            {
                "name": tool_name,
                "description": tool_description,
                "input_schema": tool_schema,
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
    )
    return parse_tool_use_response(response, tool_name)


# ─── private helpers ────────────────────────────────────────────────────


def _drop_property(schema: dict[str, Any], field_name: str) -> None:
    """Remove ``field_name`` from a JSON Schema dict's ``properties``
    and ``required`` arrays. No-op if absent."""
    props = schema.get("properties")
    if isinstance(props, dict):
        props.pop(field_name, None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [r for r in required if r != field_name]


def _walk_set_citations(obj: Any, document_reference_id: str) -> None:
    """Recursively find every SourceCitation-shaped dict in the
    extraction tree and set its ``source_id`` + ``source_type``.

    A SourceCitation is identified by the structural fingerprint:
    a dict with both ``field_or_chunk_id`` and ``quote_or_value``
    keys. This is robust against wherever in the tree the citation
    nests (LabResult.citation, Demographics.citation, etc).
    """
    if isinstance(obj, dict):
        if "field_or_chunk_id" in obj and "quote_or_value" in obj:
            obj["source_id"] = document_reference_id
            obj.setdefault("source_type", "DocumentReference")
        for value in obj.values():
            _walk_set_citations(value, document_reference_id)
    elif isinstance(obj, list):
        for item in obj:
            _walk_set_citations(item, document_reference_id)
