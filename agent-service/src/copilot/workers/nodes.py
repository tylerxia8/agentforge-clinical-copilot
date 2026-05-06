"""Worker node bodies.

Each function is a thin wrapper around an existing module
(extraction, RAG, W1 orchestrator). The wrapper:

- pulls inputs out of :class:`WorkerState`,
- calls the underlying function,
- returns a dict of state updates LangGraph will merge into the
  state via the reducers attached in ``copilot.workers.graph``.

Designed to be testable without LangGraph: every node is a plain
async (or sync) function that takes a state dict and returns a
state-update dict.
"""

from __future__ import annotations

import logging
from typing import Any

from copilot.context.patient import PatientContext
from copilot.extraction import (
    attach_bboxes,
    extract_intake_form,
    extract_lab_pdf,
)
from copilot.observability import observe
from copilot.orchestrator import Orchestrator
from copilot.rag import Retriever
from copilot.schemas import IntakeFormExtraction, LabPdfExtraction
from copilot.workers.routing import HOP_LIMIT, route_decision
from copilot.workers.state import (
    EvidenceRecord,
    ExtractionRecord,
    WorkerState,
)

logger = logging.getLogger(__name__)


# ─── supervisor ────────────────────────────────────────────────────────


@observe(name="supervisor")
def supervisor_node(state: WorkerState) -> dict[str, Any]:
    """Increment the hop counter, write the routing decision, return
    the state delta. Routing is deterministic per
    :func:`copilot.workers.routing.route_decision`."""

    new_hops = state.get("hops", 0) + 1
    decision = route_decision({**state, "hops": new_hops})
    logger.info(
        "supervisor decision: %s (hop %d/%d)",
        decision, new_hops, HOP_LIMIT,
    )
    return {"hops": new_hops, "next": decision}


# ─── intake extractor ──────────────────────────────────────────────────


@observe(name="intake_extractor")
async def intake_extractor_node(state: WorkerState) -> dict[str, Any]:
    """Drive the vision pipeline against the queued attachment.

    Steps:
      1. Pop the attachment from the state (so the supervisor doesn't
         re-extract on the next hop).
      2. Run the appropriate vision extractor for the doc type.
      3. Run the bbox-match step over every citation, attaching
         coordinates and demoting confidence on no-match.
      4. Append an :class:`ExtractionRecord` to ``state["extractions"]``.
    """

    attachment = state.get("attachment")
    if attachment is None:
        # Defensive: supervisor should never route here without an
        # attachment, but if it does, we no-op cleanly.
        logger.warning("intake_extractor invoked with no attachment")
        return {"attachment": None}

    pdf_bytes = attachment["pdf_bytes"]
    doc_type = attachment["doc_type"]
    doc_id = attachment["document_reference_id"]

    if doc_type == "lab_pdf":
        extraction: LabPdfExtraction | IntakeFormExtraction = await extract_lab_pdf(
            pdf_bytes, doc_id
        )
    elif doc_type == "intake_form":
        extraction = await extract_intake_form(pdf_bytes, doc_id)
    else:
        # Pydantic literal types should make this unreachable, but
        # fail loud if a future doc_type lands without a handler.
        raise ValueError(f"unsupported doc_type: {doc_type!r}")

    attach_bboxes(extraction, pdf_bytes)

    record: ExtractionRecord = {
        "doc_type": doc_type,
        "document_reference_id": doc_id,
        "extraction": extraction,
    }
    return {
        "attachment": None,  # consumed
        "extractions": [record],
    }


# ─── evidence retriever ────────────────────────────────────────────────


def make_evidence_retriever_node(retriever: Retriever):
    """Bind a built :class:`Retriever` into a node function.

    The retriever is constructed once at agent startup (corpus +
    BM25 index + dense embeddings) and reused across turns. We
    return a closure rather than a top-level function so the graph
    builder can decide which retriever to wire in (production with
    real Voyage/Cohere, tests with stubs)."""

    @observe(name="evidence_retriever")
    def evidence_retriever_node(state: WorkerState) -> dict[str, Any]:
        query = state.get("message", "")
        if not query.strip():
            return {"evidence": [{"query": query, "results": []}]}

        results = retriever.retrieve(query, top_k=5)
        record: EvidenceRecord = {
            "query": query,
            "results": list(results),
        }
        return {"evidence": [record]}

    return evidence_retriever_node


# ─── answer node ───────────────────────────────────────────────────────


@observe(name="answer")
async def answer_node(state: WorkerState) -> dict[str, Any]:
    """Compose the W1 orchestrator with the workers' scratchpad.

    The W1 orchestrator runs unchanged; we feed it an augmented
    user message that prepends a compact summary of any extracted
    documents and any retrieved guideline chunks. The orchestrator's
    own verification still applies — every clinical claim in the
    final response must inline-cite a row id, plus the W2 extension
    that any guideline-derived claim must inline-cite ``Guideline#<chunk_id>``.
    """

    patient_uuid = state.get("patient_uuid")
    if not patient_uuid:
        raise ValueError("answer_node called without patient_uuid in state")

    user_id = int(state.get("user_id", 0) or 0)
    base_message = state.get("message", "")
    augmented = _augment_message(
        base_message,
        extractions=state.get("extractions") or [],
        evidence=state.get("evidence") or [],
    )

    ctx = PatientContext(
        user_id=user_id,
        patient_uuid=patient_uuid,
        encounter_uuid=None,
        issued_at=0,  # supervisor-side; the ctx is internal here
        nonce="worker-graph",
    )
    orchestrator = Orchestrator()
    extra = _extra_tool_results(
        extractions=state.get("extractions") or [],
        evidence=state.get("evidence") or [],
    )
    response = await orchestrator.run_turn(
        ctx=ctx, message=augmented, history=[], extra_tool_results=extra,
    )
    # ChatResponse is a Pydantic model; coerce to dict for the
    # workers state's typed final_response slot.
    return {"final_response": response.model_dump()}


def _extra_tool_results(
    *,
    extractions: list,
    evidence: list,
) -> list[dict[str, Any]]:
    """Build the ToolResult-shaped list the orchestrator will register
    with the verifier on top of the chart bundle.

    Two sources:

    - **Evidence** chunks fetched by the evidence_retriever node. Each
      chunk gets a row with ``id = f"Guideline#{chunk.chunk_id}"`` so
      the answer node's inline ``[Guideline#...]`` citations match.
    - **Extractions** produced by the intake_extractor node. Each
      extracted fact carries its own SourceCitation; we synthesize
      one row per fact whose ``id`` is the citation's
      ``field_or_chunk_id`` (e.g. ``DocumentReference#<doc_id>``)
      so a follow-up "what should I do about that A1c" can cite
      the extracted lab without hitting the verifier's
      "no such row" rejection.
    """
    out: list[dict[str, Any]] = []

    if evidence:
        guideline_rows: list[dict[str, Any]] = []
        for ev in evidence:
            for hit in ev.get("results", []) or []:
                chunk = getattr(hit, "chunk", None)
                if chunk is None:
                    continue
                guideline_rows.append({
                    "id": f"Guideline#{chunk.chunk_id}",
                    "title": chunk.title,
                    "section": chunk.section,
                    "source": chunk.source,
                    "source_url": chunk.source_url,
                    "year": chunk.year,
                    "text": chunk.text,
                })
        if guideline_rows:
            out.append({"rows": guideline_rows, "warnings": []})

    if extractions:
        ext_rows: list[dict[str, Any]] = []
        for record in extractions:
            extraction = record.get("extraction")
            if extraction is None:
                continue
            doc_id = record.get("document_reference_id")
            for owner, citation in _walk_extraction_citations(extraction):
                if not doc_id:
                    continue
                # The owner is the parent fact (LabResult, Demographics,
                # IntakeMedication, etc.); we emit a row keyed by the
                # DocumentReference + field path so the verifier accepts
                # `[DocumentReference#<doc_id>]` citations as real.
                ext_rows.append({
                    "id": f"DocumentReference#{doc_id}",
                    "field": getattr(citation, "field_or_chunk_id", None),
                    "page": getattr(citation, "page_or_section", None),
                    "quote": getattr(citation, "quote_or_value", None),
                    "owner_repr": repr(owner)[:200],
                })
        if ext_rows:
            out.append({"rows": ext_rows, "warnings": []})

    return out


def _walk_extraction_citations(extraction: Any):
    """Reuse the public iter_citations from the extraction package."""
    from copilot.extraction import iter_citations
    yield from iter_citations(extraction)


def _augment_message(
    base: str,
    *,
    extractions: list[ExtractionRecord],
    evidence: list[EvidenceRecord],
) -> str:
    """Build the message the W1 orchestrator receives.

    Format: original user message + an inline scratchpad of
    extractions + retrieved guideline chunks. The W1 system prompt
    is unchanged — it sees this as a longer user turn with reference
    material attached.
    """

    parts: list[str] = [base.strip()] if base and base.strip() else []

    if extractions:
        parts.append("\n\n[attached document extractions]")
        for record in extractions:
            doc_id = record["document_reference_id"]
            parts.append(
                f"- {record['doc_type']} (DocumentReference#{doc_id}): "
                f"{_extraction_summary(record['extraction'])}"
            )

    if evidence:
        parts.append("\n\n[guideline evidence retrieved]")
        for ev in evidence:
            parts.append(f"Query: {ev['query']!r}")
            for hit in ev["results"]:
                chunk = getattr(hit, "chunk", None)
                if chunk is None:
                    continue
                parts.append(
                    f"- Guideline#{chunk.chunk_id} ({chunk.source}, "
                    f"{chunk.year}): {chunk.text}"
                )

    return "\n".join(parts).strip() or base


def _extraction_summary(extraction: Any) -> str:
    """Compact one-line summary of an extraction for the augmented
    prompt. We pass the FULL Pydantic dump downstream of this in a
    structured tool result — this string is just for the LLM's
    awareness that an extraction happened."""
    if isinstance(extraction, LabPdfExtraction):
        return f"{len(extraction.results)} lab result(s), {len(extraction.warnings)} warning(s)"
    if isinstance(extraction, IntakeFormExtraction):
        return (
            f"demographics + {len(extraction.medications)} med(s), "
            f"{len(extraction.allergies)} allergy/ies, "
            f"{len(extraction.family_history)} family history entries, "
            f"{len(extraction.warnings)} warning(s)"
        )
    return repr(extraction)
