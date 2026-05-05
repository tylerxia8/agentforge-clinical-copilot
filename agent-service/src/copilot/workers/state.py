"""WorkerState — the dict that flows through the supervisor graph.

Defined as a TypedDict so it works with both LangGraph's stateful
graph wiring and plain Python tests. The graph builder
(``copilot.workers.graph``) attaches LangGraph reducers to the
list-typed fields so worker nodes can append rather than overwrite.

Field intents:

- ``message`` — the user's chat turn.
- ``patient_uuid`` — the open chart's UUID. Forced server-side from
  the OpenEMR session by the agent service, NOT from the request
  body. The W1 patient-context middleware enforces this.
- ``user_id`` — the OpenEMR user issuing the turn (audit trail).
- ``attachment`` — optional uploaded document. Present when the
  user clicked **Attach** in the panel before sending. Removed by
  the intake extractor after processing so subsequent supervisor
  hops don't re-extract it.
- ``extractions`` — completed extractions, one per attachment
  processed in this turn.
- ``evidence`` — retrieved guideline chunks, one per
  ``evidence_retriever`` call this turn.
- ``hops`` — supervisor pass count. The W2 architecture caps this
  at 5 (see ``HOP_LIMIT``); when exceeded the supervisor forces
  routing to ``answer`` to break loops.
- ``next`` — supervisor's most recent routing decision. The graph's
  conditional edge reads this to dispatch.
- ``final_response`` — set by ``answer_node``; the response object
  the agent service returns to the chat panel.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class AttachmentInput(TypedDict):
    """A document attached to the chat turn.

    ``document_reference_id`` is the OpenEMR DocumentReference UUID
    the PHP module already created when storing the upload to the
    persistent volume. The intake extractor needs it to hydrate the
    citation envelopes.
    """

    pdf_bytes: bytes
    doc_type: Literal["lab_pdf", "intake_form"]
    document_reference_id: str


class ExtractionRecord(TypedDict):
    """A completed extraction. Stored on the state so subsequent
    supervisor hops can check what's already done."""

    doc_type: Literal["lab_pdf", "intake_form"]
    document_reference_id: str
    extraction: Any  # LabPdfExtraction | IntakeFormExtraction (avoid circular)


class EvidenceRecord(TypedDict):
    """One retrieve call's worth of chunks plus the query that
    produced them, for traceability in the answer node and Langfuse."""

    query: str
    results: list[Any]  # list[RetrievalResult]


class WorkerState(TypedDict, total=False):
    message: str
    patient_uuid: str
    user_id: int
    attachment: AttachmentInput | None
    extractions: list[ExtractionRecord]
    evidence: list[EvidenceRecord]
    hops: int
    next: str
    final_response: dict[str, Any] | None
