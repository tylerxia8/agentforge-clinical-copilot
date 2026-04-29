"""PatientContext — the security identity for one chat turn.

Issued by the OpenEMR PHP module after it verifies the user's session and
reads $_SESSION['pid']. Carried as a bearer token on every request.

The token is an HMAC-signed compact form: base64url(payload).hex(hmac).
We don't use JWT here because (a) we control both ends, (b) we don't need
algorithm negotiation, and (c) keeping the format minimal makes the
boundary easier to audit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from copilot.settings import settings


@dataclass(frozen=True)
class PatientContext:
    user_id: int
    patient_uuid: str
    encounter_uuid: str | None
    issued_at: int
    nonce: str

    def is_expired(self, now: int | None = None) -> bool:
        now = now or int(time.time())
        return (now - self.issued_at) > settings.agent_token_ttl_seconds


def mint_agent_token(
    user_id: int,
    patient_uuid: str,
    encounter_uuid: str | None = None,
) -> str:
    """Used by the PHP module (mirrored here so tests can mint tokens too)."""
    payload = {
        "user_id": user_id,
        "patient_uuid": patient_uuid,
        "encounter_uuid": encounter_uuid,
        "issued_at": int(time.time()),
        "nonce": secrets.token_hex(8),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    sig = hmac.new(
        settings.agent_shared_secret.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}"


class TokenError(Exception):
    """Token is malformed, expired, or signed with the wrong key."""


def verify_agent_token(token: str) -> PatientContext:
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError as exc:
        raise TokenError("malformed token") from exc

    expected_sig = hmac.new(
        settings.agent_shared_secret.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        raise TokenError("bad signature")

    padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    ctx = PatientContext(
        user_id=payload["user_id"],
        patient_uuid=payload["patient_uuid"],
        encounter_uuid=payload.get("encounter_uuid"),
        issued_at=payload["issued_at"],
        nonce=payload["nonce"],
    )
    if ctx.is_expired():
        raise TokenError("token expired")
    return ctx
