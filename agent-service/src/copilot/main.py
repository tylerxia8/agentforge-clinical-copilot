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
    extract_intake_form,
    extract_lab_pdf,
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
    try:
        await check_ip_quota(_client_ip(request))
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
"""Hard upload cap. Anthropic's PDF input supports up to 32 MB / 100
pages; we leave headroom for multipart overhead and to keep memory
predictable on the small Railway instance."""


@app.post("/agent/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    doc_type: Literal["lab_pdf", "intake_form"] = Form(...),
    document_reference_id: str = Form(...),
    ctx: PatientContext = Depends(_ctx_from_token),
) -> dict[str, Any]:
    """Run the vision pipeline against an uploaded document.

    The PHP module is responsible for storing the PDF on the
    OpenEMR persistent volume + creating the DocumentReference row
    BEFORE calling this endpoint; ``document_reference_id`` is the
    UUID of that row. We forward the bytes directly rather than
    re-fetching from FHIR (saves a round-trip; the PHP already has
    them).

    Returns the validated extraction (Pydantic dump) with bbox
    citations attached. The PHP module persists the structured
    output as FHIR Observations / direct-table writes on the
    OpenEMR side.
    """
    if file.content_type and file.content_type != "application/pdf":
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"expected application/pdf, got {file.content_type!r}",
        )

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"upload exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB cap",
        )

    try:
        await check_ip_quota(_client_ip(request))
        async with chat_concurrency_slot():
            if doc_type == "lab_pdf":
                extraction: Any = await extract_lab_pdf(pdf_bytes, document_reference_id)
            else:  # "intake_form"
                extraction = await extract_intake_form(pdf_bytes, document_reference_id)
            summary = attach_bboxes(extraction, pdf_bytes)
    except RateLimitExceeded as exc:
        raise _rate_limit_response(exc) from exc

    logger.info(
        "extract %s doc=%s by user=%s patient=%s; bboxes %d/%d",
        doc_type, document_reference_id, ctx.user_id, ctx.patient_uuid,
        summary["matched"], summary["walked"],
    )
    return {
        "extraction": extraction.model_dump(mode="json"),
        "bbox_match": summary,
    }


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
