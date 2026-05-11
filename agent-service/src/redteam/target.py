"""HTTP client for the deployed W2 Clinical Co-Pilot — the attack
target. The Red Team Agent talks to the Co-Pilot through this thin
adapter so the rest of the platform doesn't depend on response-shape
details.

Endpoints used:
- POST /demo/chat       — token-less chat (the primary MVP target;
                          accepts arbitrary patient_uuid)
- GET  /healthz         — liveness probe (PRD hard-gate: deployed
                          target must be live for every checkpoint)

Endpoints NOT used in MVP but ready for Wed/Thu:
- POST /agent/chat      — HMAC-authed embedded-panel path
- POST /agent/extract   — multi-format ingestion (for real
                          indirect-injection-through-files campaigns)

For MVP, ``indirect_injection`` simulates document-borne payloads by
inlining the malicious text into the chat message (e.g. "I just
uploaded a lab report containing: <payload>"). That's a valid
adversarial probe — the real file-upload path is Wed/Thu work.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

DEFAULT_BASE_URL = os.environ.get(
    "REDTEAM_TARGET_URL",
    "https://copilot-agent-production-ba87.up.railway.app",
)
DEFAULT_TIMEOUT_S = float(os.environ.get("REDTEAM_TARGET_TIMEOUT_S", "60"))


@dataclass(slots=True)
class TargetResponse:
    """Normalized shape returned by the target's chat endpoints."""

    status_code: int
    text: str
    sources: list[str]
    refused: bool | None
    refusal_reason: str | None
    elapsed_s: float
    raw_body: dict | None
    error: str | None = None


class Target:
    """Async HTTP client. Single instance per campaign run."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_s,
            headers={"User-Agent": "agentforge-redteam/0.1"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def healthz(self) -> bool:
        """Liveness probe. The PRD requires the deployed target be
        live for every checkpoint; this runs before each campaign so
        a dead target fails fast with a distinct error rather than
        looking like a refusal."""
        try:
            r = await self._client.get("/healthz")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def demo_chat(
        self,
        message: str,
        patient_uuid: str,
        history: list[dict] | None = None,
    ) -> TargetResponse:
        """POST /demo/chat with one user message.

        For MVP the Red Team uses single-turn attacks; multi-turn
        history support is wired (the ``history`` parameter) for the
        Wed/Thu state-corruption campaigns.
        """
        payload = {
            "message": message,
            "patient_uuid": patient_uuid,
        }
        if history:
            payload["history"] = history

        start = time.perf_counter()
        try:
            r = await self._client.post("/demo/chat", json=payload)
        except httpx.HTTPError as e:
            elapsed = time.perf_counter() - start
            return TargetResponse(
                status_code=0,
                text="",
                sources=[],
                refused=None,
                refusal_reason=None,
                elapsed_s=elapsed,
                raw_body=None,
                error=f"transport error: {type(e).__name__}: {e}",
            )

        elapsed = time.perf_counter() - start

        try:
            body = r.json()
        except ValueError:
            body = None

        if body is None or not isinstance(body, dict):
            return TargetResponse(
                status_code=r.status_code,
                text="",
                sources=[],
                refused=None,
                refusal_reason=None,
                elapsed_s=elapsed,
                raw_body=None,
                error=f"non-JSON response ({r.status_code}, "
                      f"{len(r.text)} bytes)",
            )

        return TargetResponse(
            status_code=r.status_code,
            text=str(body.get("text") or ""),
            sources=list(body.get("sources") or []),
            refused=body.get("refused") if isinstance(body.get("refused"), bool) else None,
            refusal_reason=body.get("refusal_reason"),
            elapsed_s=elapsed,
            raw_body=body,
            error=None,
        )
