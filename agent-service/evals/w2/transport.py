"""HTTP transport for W2 eval cases.

Fires real requests at the deployed (staging) agent service. The
``AGENT_URL`` and ``AGENT_SHARED_SECRET`` env vars are required —
the cases run against the live deployment so the eval gate is
exercising the same code path users hit.

Two functions, mirroring the agent service's two W2 entry points:

- :func:`chat` — POSTs a JSON chat turn to ``/agent/chat``
- :func:`extract` — multipart-uploads a PDF to ``/agent/extract``

Both mint an HMAC bearer token using the same shared secret the
agent service verifies (see ``copilot.context.patient.verify_agent_token``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

AGENT_URL = os.environ.get(
    "AGENT_URL", "https://copilot-agent-production-ba87.up.railway.app"
)
SHARED_SECRET = os.environ.get("AGENT_SHARED_SECRET", "")
TIMEOUT_SECONDS = 120


# ─── token mint (mirrors copilot.context.patient.verify_agent_token) ──


def mint_token(patient_uuid: str, user_id: int = 1) -> str:
    if not SHARED_SECRET:
        raise RuntimeError(
            "AGENT_SHARED_SECRET env var is required to run W2 evals"
        )
    payload = {
        "user_id": user_id,
        "patient_uuid": patient_uuid,
        "encounter_uuid": None,
        "issued_at": int(time.time()),
        "nonce": secrets.token_hex(8),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    sig = hmac.new(
        SHARED_SECRET.encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{sig}"


# ─── chat ──────────────────────────────────────────────────────────────


def chat(
    *,
    patient_uuid: str,
    message: str,
    history: list[dict] | None = None,
) -> dict:
    """POST /agent/chat. Returns the parsed JSON body. Errors return
    a dict with ``_status`` (the HTTP code) and ``_error`` (str) so the
    rubric checkers can grade them rather than crash the runner.

    ``history`` is an optional list of prior message turns in the
    Anthropic-style ``{role, content}`` shape. Used by multi-step
    cases to thread context across turns."""
    body = json.dumps({"message": message, "history": history or []}).encode()
    req = urllib.request.Request(
        f"{AGENT_URL}/agent/chat",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {mint_token(patient_uuid)}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return _decode(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return _decode(e.code, e.read())
    except Exception as e:  # noqa: BLE001
        return {"_status": -1, "_error": f"{type(e).__name__}: {e}"}


def chat_multiturn(
    *, patient_uuid: str, turns: list[str]
) -> dict:
    """Fire ``len(turns)`` chat turns in sequence, threading history
    so each turn sees the prior exchange. Returns the LAST turn's
    response, augmented with a ``_transcript`` field listing every
    user/assistant pair (also in Anthropic shape) for rubrics that
    want to grade the full conversation rather than only the final
    response.

    If any intermediate turn returns an HTTP error, we stop and
    return a dict with ``_status`` set to the failing code so the
    case fails fast rather than running the rest of the conversation
    against a broken state."""
    history: list[dict] = []
    last: dict = {}
    for turn_idx, message in enumerate(turns):
        last = chat(patient_uuid=patient_uuid, message=message, history=history)
        if last.get("_status", 200) >= 400 or last.get("_error"):
            last["_failed_turn_index"] = turn_idx
            last["_transcript"] = list(history)
            return last
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": last.get("text", "")})
    last["_transcript"] = history
    return last


# ─── extract (multipart) ───────────────────────────────────────────────


def extract(
    *,
    pdf_path: Path,
    doc_type: str,
    document_reference_id: str,
    patient_uuid: str,
) -> dict:
    """POST /agent/extract as multipart/form-data. Hand-rolled
    multipart so we don't drag a dependency in just for evals."""
    if not pdf_path.exists():
        return {"_status": -1, "_error": f"fixture not found: {pdf_path}"}
    pdf_bytes = pdf_path.read_bytes()
    boundary = "----eval-w2-" + uuid.uuid4().hex
    body = _build_multipart(
        boundary=boundary,
        fields={"doc_type": doc_type, "document_reference_id": document_reference_id},
        file_field="file",
        filename=pdf_path.name,
        file_bytes=pdf_bytes,
        file_mime=mimetypes.guess_type(pdf_path.name)[0] or "application/pdf",
    )

    req = urllib.request.Request(
        f"{AGENT_URL}/agent/extract",
        method="POST",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {mint_token(patient_uuid)}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return _decode(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return _decode(e.code, e.read())
    except Exception as e:  # noqa: BLE001
        return {"_status": -1, "_error": f"{type(e).__name__}: {e}"}


# ─── helpers ───────────────────────────────────────────────────────────


def _decode(status: int, body: bytes) -> dict:
    try:
        decoded = json.loads(body)
    except Exception:  # noqa: BLE001
        decoded = {"_raw": body[:400].decode("utf-8", errors="replace")}
    if not isinstance(decoded, dict):
        decoded = {"_value": decoded}
    decoded["_status"] = status
    return decoded


def _build_multipart(
    *,
    boundary: str,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    file_mime: str,
) -> bytes:
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{key}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{filename}\"\r\n"
        f"Content-Type: {file_mime}\r\n\r\n".encode()
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts)
