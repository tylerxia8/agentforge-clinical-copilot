"""FastAPI entrypoint. Three endpoints — /chat (synchronous turn),
/warm (fire-and-forget context preload), /healthz (k8s probe)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from copilot.context.cache import ContextCache
from copilot.context.patient import PatientContext, verify_agent_token
from copilot.orchestrator import Orchestrator, ChatResponse

logger = logging.getLogger(__name__)
app = FastAPI(title="Clinical Co-Pilot Agent", version="0.1.0")


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    history: list[dict[str, Any]] = []


def _ctx_from_token(authorization: str | None = None) -> PatientContext:
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
