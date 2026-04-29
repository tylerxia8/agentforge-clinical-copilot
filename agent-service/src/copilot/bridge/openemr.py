"""HTTP client to OpenEMR's REST API.

Authenticates via OAuth2 client_credentials grant (a service account, not
a user session), so this token can carry a different / narrower set of
scopes than a clinician's interactive token.

The token is cached in-process for its declared lifetime minus a 60-second
safety margin. Multi-instance deployments will want to move that cache to
Redis to avoid each replica minting its own.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from copilot.settings import settings

logger = logging.getLogger(__name__)


class _TokenCache:
    def __init__(self) -> None:
        self.token: str | None = None
        self.expires_at: float = 0.0


_token_cache = _TokenCache()


async def _get_oauth_token(client: httpx.AsyncClient) -> str:
    if _token_cache.token and time.time() < _token_cache.expires_at:
        return _token_cache.token
    resp = await client.post(
        f"{settings.openemr_base_url}{settings.openemr_oauth_token_path}",
        data={
            "grant_type": "client_credentials",
            "scope": settings.openemr_oauth_scope,
        },
        auth=(settings.openemr_oauth_client_id, settings.openemr_oauth_client_secret),
    )
    resp.raise_for_status()
    body = resp.json()
    _token_cache.token = body["access_token"]
    _token_cache.expires_at = time.time() + int(body.get("expires_in", 3600)) - 60
    return _token_cache.token


class OpenEMRBridge:
    """Thin wrapper. Each method maps to one OpenEMR REST or FHIR call.

    Methods are sketched — they return realistic shapes so the rest of
    the agent service can be developed end-to-end. Replace with real
    HTTP calls in Thursday's deliverable.
    """

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await _get_oauth_token(client)
            resp = await client.get(
                f"{settings.openemr_base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()

    # --- patient-scoped reads ---

    async def get_prescriptions(self, patient_uuid: str) -> list[dict[str, Any]]:
        # TODO(thursday): real call. Endpoint is approximately
        #   GET /apis/default/api/patient/{patient_uuid}/medication
        # Documented in OpenEMR's API_README.md. Map the response shape
        # into the row dicts that GetActiveMedicationsTool expects.
        logger.debug("STUB get_prescriptions for %s", patient_uuid)
        return []

    async def get_list_medications(self, patient_uuid: str) -> list[dict[str, Any]]:
        # TODO(thursday): GET /apis/default/api/patient/{patient_uuid}/medical_problem
        # filtered to type='medication', or query the lists table directly
        # via a custom endpoint we add to the PHP module.
        logger.debug("STUB get_list_medications for %s", patient_uuid)
        return []

    # Each of these mirrors the medication pattern — sketch only:
    #
    # async def get_problems(self, patient_uuid): ...           # /medical_problem
    # async def get_allergies(self, patient_uuid): ...          # /allergy
    # async def get_encounters(self, patient_uuid, n=5): ...    # /encounter
    # async def get_lab_history(self, patient_uuid, code): ...  # FHIR Observation
    # async def get_vital_history(self, patient_uuid, n=3): ... # /encounter/{e}/vital
    # async def get_immunizations(self, patient_uuid): ...      # FHIR Immunization
