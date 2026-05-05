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
    extract_intake_form,
    extract_lab_pdf,
    extract_words,
    match_quote,
)
from copilot.extraction.pdf import words_on_page
from copilot.observability import langfuse_client, observe
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

    _attach_bboxes(extraction, pdf_bytes)

    record: ExtractionRecord = {
        "doc_type": doc_type,
        "document_reference_id": doc_id,
        "extraction": extraction,
    }
    return {
        "attachment": None,  # consumed
        "extractions": [record],
    }


def _attach_bboxes(
    extraction: LabPdfExtraction | IntakeFormExtraction, pdf_bytes: bytes
) -> None:
    """Walk every SourceCitation in the extraction; for each, look
    up the bbox via :func:`match_quote` against the page words. On
    no-match, demote ``extraction_confidence`` to 'low' on the
    enclosing fact (when present)."""

    try:
        words = extract_words(pdf_bytes)
    except Exception:  # noqa: BLE001
        logger.warning("pdfplumber word extraction failed; bbox matching skipped",
                       exc_info=True)
        return

    # Walk via dump-and-mutate — Pydantic models are immutable enough
    # that we set citation.bbox via direct attribute on the BBox-bearing
    # SourceCitation submodel, which is mutable in v2 unless frozen.
    citations_walked = 0
    citations_matched = 0
    for cite_owner, citation in _iter_citations(extraction):
        page = citation.page_or_section
        if not isinstance(page, int):
            continue
        page_words = words_on_page(words, page)
        result = match_quote(citation.quote_or_value, page_words)
        citations_walked += 1
        if result.bbox is not None:
            # SourceCitation is a Pydantic BaseModel, mutable by default.
            citation.bbox = result.bbox  # type: ignore[misc]
            citations_matched += 1
        else:
            # Demote confidence on the owning fact if it has one.
            current = getattr(cite_owner, "extraction_confidence", None)
            if current is not None and current != "low":
                cite_owner.extraction_confidence = "low"  # type: ignore[misc]

    if langfuse_client is not None and citations_walked:
        try:
            langfuse_client.create_event(
                name="extraction.bbox_match_summary",
                metadata={
                    "walked": citations_walked,
                    "matched": citations_matched,
                    "match_rate": citations_matched / citations_walked,
                },
            )
        except Exception:  # noqa: BLE001
            pass


def _iter_citations(extraction: Any) -> Any:
    """Yield ``(owner, citation)`` for every SourceCitation in the
    extraction tree. Owner is the parent fact (LabResult, Demographics,
    etc.) so we can demote its ``extraction_confidence`` on no-match."""
    # Lab path
    if isinstance(extraction, LabPdfExtraction):
        for result in extraction.results:
            yield result, result.citation
        return
    # Intake path
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
    response = await orchestrator.run_turn(ctx=ctx, message=augmented, history=[])
    # ChatResponse is a Pydantic model; coerce to dict for the
    # workers state's typed final_response slot.
    return {"final_response": response.model_dump()}


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
