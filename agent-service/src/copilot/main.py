"""FastAPI entrypoint.

Routes:
  GET  /                       - Demo chat UI (static HTML), gated by DEMO_MODE_ENABLED
  POST /demo/chat              - Token-less chat endpoint for the demo UI; mints a
                                 PatientContext server-side. Same worker graph as
                                 /agent/chat.
  POST /agent/chat             - Production chat endpoint; requires HMAC token.
                                 Routes through the W2 supervisor + worker graph.
  POST /agent/extract          - W2 multipart upload; runs lab_pdf or intake_form
                                 vision extraction directly (does not enter the
                                 chat graph). Returns the validated extraction
                                 with bbox citations attached.
  POST /agent/warm/{uuid}      - Fire-and-forget bundle preload.
  GET  /healthz                - liveness probe.
"""

from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from copilot.context.cache import ContextCache
from copilot.context.patient import PatientContext, verify_agent_token
from copilot.extraction import (
    attach_bboxes,
    detect_hl7_message_type,
    extract_docx_referral,
    extract_intake_form,
    extract_lab_pdf,
    extract_tiff_fax,
    extract_xlsx_workbook,
    parse_adt_a08,
    parse_oru_r01,
)
from copilot.middleware.rate_limit import (
    RateLimitExceeded,
    chat_concurrency_slot,
    check_ip_quota,
)
from copilot.orchestrator import ChatResponse
from copilot.rag import make_retriever
from copilot.settings import settings
from copilot.workers.graph import build_graph

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# Module-level singletons. Built once during startup; reused across
# every request. The retriever holds the BM25 index + dense
# embeddings, the graph holds the compiled LangGraph state machine
# — both are expensive to construct (dense embeddings = a Voyage
# call per chunk in the corpus) so we never rebuild per request.
_GRAPH = None
_RETRIEVER = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _GRAPH, _RETRIEVER
    try:
        _RETRIEVER = make_retriever()
        _GRAPH = build_graph(retriever=_RETRIEVER)
        logger.info("worker graph initialized")
    except Exception:  # noqa: BLE001
        # Boot failure of the W2 graph should not take the service
        # down — the service still serves /healthz and the W1
        # extraction endpoint (which doesn't touch the graph).
        logger.exception("worker graph init failed; W2 chat endpoints will 503")
    yield


app = FastAPI(title="Clinical Co-Pilot Agent", version="0.2.0", lifespan=_lifespan)


# ─── request models ────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    history: list[dict[str, Any]] = []


class DemoChatRequest(BaseModel):
    patient_uuid: str
    message: str
    history: list[dict[str, Any]] = []


# ─── auth / context plumbing ───────────────────────────────────────────


def _ctx_from_token(authorization: str | None = Header(default=None)) -> PatientContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        return verify_agent_token(authorization.removeprefix("Bearer "))
    except Exception as exc:  # noqa: BLE001 — token errors are intentionally opaque to caller
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_response(exc: RateLimitExceeded) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail=exc.reason,
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


def _require_graph():
    if _GRAPH is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "worker graph not initialized; check agent service logs",
        )
    return _GRAPH


# ─── chat (graph-routed) ───────────────────────────────────────────────


async def _run_chat_via_graph(
    *, ctx: PatientContext, message: str
) -> ChatResponse:
    """Drive the W2 worker graph for one chat turn and return the
    answer node's response. The graph runs supervisor → (workers) →
    supervisor → answer; for plain chart questions with no
    attachment and no guideline triggers, it short-circuits straight
    to answer (one extra ~0ms supervisor hop vs the W1 path)."""

    graph = _require_graph()
    final_state = await graph.ainvoke({
        "message": message,
        "patient_uuid": ctx.patient_uuid,
        "user_id": ctx.user_id,
        "attachment": None,
        "extractions": [],
        "evidence": [],
        "hops": 0,
    })
    payload = final_state.get("final_response")
    if not payload:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "graph completed without producing a response",
        )
    return ChatResponse.model_validate(payload)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    ctx: PatientContext = Depends(_ctx_from_token),
) -> ChatResponse:
    # No per-IP rate limit on HMAC-authenticated endpoints — the
    # token mint is the abuse gate. The per-IP bucket was designed
    # for /demo/chat (token-less) and was tripping the W2 eval suite
    # late in its sequential run, causing late-stage cases (golden +
    # multistep, positions 51-56) to 429 and produce empty responses
    # that flunked factually_consistent. Concurrency slot still
    # applies — Anthropic's 30K-tokens-per-minute org limit is real.
    try:
        async with chat_concurrency_slot():
            return await _run_chat_via_graph(ctx=ctx, message=body.message)
    except RateLimitExceeded as exc:
        raise _rate_limit_response(exc) from exc


@app.post("/agent/warm/{patient_uuid}", status_code=status.HTTP_202_ACCEPTED)
async def warm(
    patient_uuid: str,
    background: BackgroundTasks,
    ctx: PatientContext = Depends(_ctx_from_token),
) -> dict[str, str]:
    if ctx.patient_uuid != patient_uuid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "patient mismatch")
    background.add_task(ContextCache().warm, ctx)
    return {"status": "warming"}


# ─── extraction (W2) ───────────────────────────────────────────────────


MAX_PDF_BYTES = 25 * 1024 * 1024
"""Hard upload cap for binary uploads (PDF, TIFF). Anthropic's PDF
input supports up to 32 MB / 100 pages; we leave headroom for
multipart overhead and small-instance memory."""

MAX_TEXT_BYTES = 2 * 1024 * 1024
"""Hard upload cap for text/structured uploads (HL7v2, DOCX, XLSX).
Real-world clinical records — even a multi-page referral or a long
HL7 batch — sit comfortably under 2 MB."""


# Doc types accepted by /agent/extract. Lab PDF and intake form go
# through the vision pipeline; the rest are parsed structurally.
ExtractDocType = Literal[
    "lab_pdf",
    "intake_form",
    "hl7v2_oru",
    "hl7v2_adt",
    "hl7v2",  # auto-detect (ORU vs ADT) from MSH-9
    "docx_referral",
    "xlsx_workbook",
    "tiff_fax",  # multi-page TIFF, converted to PDF in-process
]


@app.post("/agent/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    doc_type: ExtractDocType = Form(...),
    document_reference_id: str = Form(...),
    ctx: PatientContext = Depends(_ctx_from_token),
) -> dict[str, Any]:
    """Run the appropriate extractor for ``doc_type`` against an
    uploaded document.

    Vision-based (PDF):
      - ``lab_pdf``     → :func:`extract_lab_pdf` + bbox attach
      - ``intake_form`` → :func:`extract_intake_form` + bbox attach

    Structured (HL7 v2):
      - ``hl7v2_oru``   → :func:`parse_oru_r01` (lab results → LabPdfExtraction)
      - ``hl7v2_adt``   → :func:`parse_adt_a08` (demographics dict)
      - ``hl7v2``       → auto-dispatch on MSH-9

    Caller is responsible for storing the source bytes (PDF on the
    OpenEMR documents volume, HL7 in a designated landing zone) and
    creating the DocumentReference row BEFORE invoking; the
    ``document_reference_id`` is what shows up in citations so the
    chat panel can deep-link back to the source.
    """
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")

    # Per-format upload caps. Text-flavoured uploads stay small;
    # binary scans/PDFs get the larger Anthropic-friendly cap.
    is_text_doc = doc_type.startswith("hl7v2")
    cap = MAX_TEXT_BYTES if is_text_doc else MAX_PDF_BYTES
    if len(raw_bytes) > cap:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"upload exceeds {cap // (1024 * 1024)} MB cap",
        )

    # Content-type sanity. Strict for the formats that must be a
    # specific binary (PDF, TIFF); lenient for text-shaped formats
    # because senders emit a zoo of MIME types in practice.
    expected_ct: dict[str, tuple[str, ...]] = {
        "lab_pdf":       ("application/pdf",),
        "intake_form":   ("application/pdf",),
        "tiff_fax":      ("image/tiff", "image/tif"),
        "docx_referral": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "xlsx_workbook": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    }
    if doc_type in expected_ct and file.content_type:
        if file.content_type not in expected_ct[doc_type]:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"expected {expected_ct[doc_type]} for {doc_type!r}, got {file.content_type!r}",
            )

    # No per-IP rate limit on HMAC-authed extract — same rationale
    # as /agent/chat above. Concurrency slot still applies.
    try:
        async with chat_concurrency_slot():
            if doc_type == "lab_pdf":
                extraction: Any = await extract_lab_pdf(raw_bytes, document_reference_id)
                summary = attach_bboxes(extraction, raw_bytes)
                return {
                    "extraction": extraction.model_dump(mode="json"),
                    "bbox_match": summary,
                    "format": "pdf",
                }
            if doc_type == "intake_form":
                extraction = await extract_intake_form(raw_bytes, document_reference_id)
                summary = attach_bboxes(extraction, raw_bytes)
                return {
                    "extraction": extraction.model_dump(mode="json"),
                    "bbox_match": summary,
                    "format": "pdf",
                }
            if doc_type == "docx_referral":
                docx = await extract_docx_referral(raw_bytes, document_reference_id)
                return {
                    "extraction": docx.model_dump(mode="json"),
                    "format": "docx",
                }
            if doc_type == "xlsx_workbook":
                wb = await extract_xlsx_workbook(raw_bytes, document_reference_id)
                return {
                    "extraction": wb.model_dump(mode="json"),
                    "format": "xlsx",
                }
            if doc_type == "tiff_fax":
                # TIFF → in-process PDF → existing lab-PDF vision pipeline.
                # We DON'T attach_bboxes here: the converted PDF has no
                # underlying text layer (it's a rasterized scan), so
                # pdfplumber won't find words to match. Citations carry
                # the quote_or_value the model emitted; bbox stays null.
                tiff_extraction = await extract_tiff_fax(raw_bytes, document_reference_id)
                return {
                    "extraction": tiff_extraction.model_dump(mode="json"),
                    "format": "tiff",
                }

            # HL7 v2 path. Decode UTF-8 (HL7 is ASCII per spec but we
            # tolerate UTF-8 for the rare diacritic in a name field).
            text = raw_bytes.decode("utf-8", errors="replace")

            if doc_type == "hl7v2_oru":
                lab = parse_oru_r01(text, document_reference_id)
                return {
                    "extraction": lab.model_dump(mode="json"),
                    "format": "hl7v2",
                    "message_type": "ORU_R01",
                }
            if doc_type == "hl7v2_adt":
                adt = parse_adt_a08(text, document_reference_id)
                return {
                    "extraction": adt,
                    "format": "hl7v2",
                    "message_type": "ADT_A08",
                }
            # auto-detect
            mt = detect_hl7_message_type(text)
            if mt == "ORU_R01":
                lab = parse_oru_r01(text, document_reference_id)
                return {
                    "extraction": lab.model_dump(mode="json"),
                    "format": "hl7v2",
                    "message_type": mt,
                }
            if mt == "ADT_A08":
                adt = parse_adt_a08(text, document_reference_id)
                return {
                    "extraction": adt,
                    "format": "hl7v2",
                    "message_type": mt,
                }
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unsupported HL7 message type {mt!r} (expected ORU_R01 or ADT_A08)",
            )
    except RateLimitExceeded as exc:
        raise _rate_limit_response(exc) from exc


# ─── Demo UI (gated by DEMO_MODE_ENABLED) ────────────────────────────


@app.get("/", response_class=HTMLResponse, response_model=None)
async def demo_index() -> FileResponse | HTMLResponse:
    if not settings.demo_mode_enabled:
        return HTMLResponse(
            "<h1>Clinical Co-Pilot Agent</h1><p>Demo UI disabled. "
            "POST /agent/chat with a Bearer token to interact.</p>",
            status_code=200,
        )
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "demo UI missing")
    return FileResponse(index, media_type="text/html")


# ─── Visibility / introspection (built per W2 MVP grader feedback) ────


@app.get("/visibility", response_class=HTMLResponse, response_model=None)
async def visibility_index() -> FileResponse | HTMLResponse:
    """Static page that visualizes the corpus + supervisor routing
    rules + eval coverage + recent supervisor decisions + a live
    retrieval inspector. Authentication-free on purpose: the data
    surfaced is the system's *shape* (not patient data), and the
    point of the page is reviewer / operator transparency."""
    page = _STATIC_DIR / "visibility.html"
    if not page.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "visibility page missing")
    return FileResponse(page, media_type="text/html")


@app.get("/visibility/data")
async def visibility_data() -> dict[str, Any]:
    """JSON aggregate consumed by /visibility's static page. Also
    useful for piping into a CLI or external dashboard."""
    from copilot.visibility import (
        corpus_snapshot,
        eval_coverage_snapshot,
        recent_traces,
        routing_snapshot,
    )
    return {
        "corpus": corpus_snapshot(),
        "routing": routing_snapshot(),
        "eval_coverage": eval_coverage_snapshot(),
        "recent_traces": recent_traces(),
    }


class RetrievalInspectRequest(BaseModel):
    query: str
    top_k: int = 5


@app.post("/visibility/retrieve")
async def visibility_retrieve(body: RetrievalInspectRequest) -> dict[str, Any]:
    """Run a query through the production retriever and return the
    BM25 / dense / rerank breakdown with scores. Lets the visibility
    page show what the agent sees BEFORE the LLM gets it."""
    from copilot.visibility import retrieval_breakdown
    return retrieval_breakdown(_RETRIEVER, body.query, body.top_k)


# ───── W3 adversarial-platform visibility ──────────────────────────────


@app.get("/adversarial", response_class=HTMLResponse, response_model=None)
async def adversarial_index() -> FileResponse | HTMLResponse:
    """Static page that visualizes the W3 adversarial platform's
    state: coverage per attack category, vuln pipeline (live vs.
    pending), recent campaigns with the Orchestrator's rationale,
    and a daily attempt-trend chart.

    Authentication-free, same as /visibility — surfaces system
    *shape* not patient data."""
    page = _STATIC_DIR / "adversarial.html"
    if not page.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "adversarial page missing")
    return FileResponse(page, media_type="text/html")


@app.get("/adversarial/data")
async def adversarial_data() -> dict[str, Any]:
    """JSON aggregate consumed by /adversarial. Also useful for
    piping into a CLI or external dashboard. Read-only file scan
    against agent-service/evals/redteam_runs/ + vulns/ + the
    adversarial_findings sidecar dir."""
    from copilot.adversarial_visibility import aggregate_snapshot
    return aggregate_snapshot()


@app.get("/adversarial/attempts/{attempt_id}", response_class=HTMLResponse, response_model=None)
async def adversarial_attempt_page(attempt_id: str) -> FileResponse | HTMLResponse:
    """Per-attempt deep-link page. Renders one Red Team attempt's
    full transcript + verdict on a single page so a grader can
    inspect any individual exploit attempt directly without
    grepping JSON files.

    Tuesday W3 MVP grader feedback: 'making the raw eval
    artifacts, reproducibility, and vulnerability evidence
    easier to inspect directly'. This page is the direct
    response — every attempt UUID in the campaigns table on
    /adversarial is a clickable link to its own detail page.
    """
    page = _STATIC_DIR / "adversarial_attempt.html"
    if not page.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "adversarial attempt page missing")
    return FileResponse(page, media_type="text/html")


@app.get("/adversarial/attempts/{attempt_id}/data")
async def adversarial_attempt_data(attempt_id: str) -> dict[str, Any]:
    """JSON detail for one attempt. The static page fetches this
    and renders. Returns 404 if the attempt ID is unknown."""
    from copilot.adversarial_visibility import attempt_detail
    detail = attempt_detail(attempt_id)
    if detail is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"attempt {attempt_id!r} not found in any campaign run on disk",
        )
    return detail


@app.post("/demo/chat", response_model=ChatResponse)
async def demo_chat(body: DemoChatRequest, request: Request) -> ChatResponse:
    """Demo endpoint that mints a PatientContext server-side instead of
    requiring a pre-minted HMAC token. Same graph, same workers, same
    verification — just skipping the token-verify step that the
    OpenEMR PHP module is responsible for in production.
    """
    if not settings.demo_mode_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        await check_ip_quota(_client_ip(request))
        async with chat_concurrency_slot():
            ctx = PatientContext(
                user_id=0,
                patient_uuid=body.patient_uuid,
                encounter_uuid=None,
                issued_at=int(time.time()),
                nonce=secrets.token_hex(8),
            )
            return await _run_chat_via_graph(ctx=ctx, message=body.message)
    except RateLimitExceeded as exc:
        raise _rate_limit_response(exc) from exc
