"""FastAPI entrypoint.

Routes:
  GET  /                       - Demo chat UI (static HTML), gated by DEMO_MODE_ENABLED
  POST /demo/chat              - Token-less chat endpoint for the demo UI; mints a
                                 PatientContext server-side. Same orchestrator as
                                 /agent/chat.
  POST /agent/chat             - Production chat endpoint; requires HMAC token.
  POST /agent/warm/{uuid}      - Fire-and-forget bundle preload.
  GET  /healthz                - liveness probe.
"""

from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from copilot.context.cache import ContextCache
from copilot.context.patient import PatientContext, verify_agent_token
from copilot.orchestrator import Orchestrator, ChatResponse
from copilot.settings import settings

logger = logging.getLogger(__name__)
app = FastAPI(title="Clinical Co-Pilot Agent", version="0.1.0")

_STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    history: list[dict[str, Any]] = []


class DemoChatRequest(BaseModel):
    patient_uuid: str
    message: str
    history: list[dict[str, Any]] = []


def _ctx_from_token(authorization: str | None = Header(default=None)) -> PatientContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        return verify_agent_token(authorization.removeprefix("Bearer "))
    except Exception as exc:  # noqa: BLE001 — token errors are intentionally opaque to caller
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    ctx: PatientContext = Depends(_ctx_from_token),
) -> ChatResponse:
    orchestrator = Orchestrator()
    return await orchestrator.run_turn(ctx=ctx, message=body.message, history=body.history)


@app.post("/agent/warm/{patient_uuid}", status_code=status.HTTP_202_ACCEPTED)
async def warm(
    patient_uuid: str,
    background: BackgroundTasks,
    ctx: PatientContext = Depends(_ctx_from_token),
) -> dict[str, str]:
    # The token already binds the caller to a single patient_uuid; reject
    # mismatched warm targets so the warm endpoint can't be used to probe
    # other patients.
    if ctx.patient_uuid != patient_uuid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "patient mismatch")
    background.add_task(ContextCache().warm, ctx)
    return {"status": "warming"}


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
async def demo_chat(body: DemoChatRequest) -> ChatResponse:
    """Demo endpoint that mints a PatientContext server-side instead of
    requiring a pre-minted HMAC token. Same orchestrator, same middleware,
    same verification — just skipping the token-verify step that the
    OpenEMR PHP module is responsible for in production. This is the
    production agent service running against real FHIR data; the only
    thing the demo UI bypasses is the bearer-token check.
    """
    if not settings.demo_mode_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    ctx = PatientContext(
        user_id=0,
        patient_uuid=body.patient_uuid,
        encounter_uuid=None,
        issued_at=int(time.time()),
        nonce=secrets.token_hex(8),
    )
    orchestrator = Orchestrator()
    return await orchestrator.run_turn(ctx=ctx, message=body.message, history=body.history)
