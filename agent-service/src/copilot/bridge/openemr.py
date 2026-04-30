"""HTTP client to OpenEMR's FHIR API.

Auth: OAuth2 Password Grant. We hold a service-account username/password
plus an OAuth client_id/client_secret. On first call we POST to
/oauth2/{site}/token with grant_type=password and cache the access_token
(typically ~3600s lifetime) plus the refresh_token. When the access
token expires we use the refresh token to get a new one without
re-prompting the user.

This is documented as a v2 swap target — production should use
client_credentials with private-key JWT (RS384). See ARCHITECTURE.md
"Open questions we are explicitly carrying forward".

Patient handle convention: every method takes a patient *uuid* (FHIR
Patient resource id), not the integer pid. The middleware enforces
that the uuid in the call matches the open-chart uuid in PatientContext.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from copilot.settings import settings

logger = logging.getLogger(__name__)

# Connect/read timeouts. OpenEMR can be slow on the first call after
# warm-up; subsequent calls are typically <500ms.
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


class _TokenCache:
    """Process-local OAuth token cache. For multi-replica deploys this
    should move to Redis so each instance doesn't mint its own token.
    """
    def __init__(self) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: float = 0.0


_tokens = _TokenCache()


async def _fetch_token_password_grant(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.post(
        f"{settings.openemr_base_url}{settings.openemr_oauth_token_path}",
        data={
            "grant_type": "password",
            "client_id": settings.openemr_oauth_client_id,
            "client_secret": settings.openemr_oauth_client_secret,
            "username": settings.openemr_service_username,
            "password": settings.openemr_service_password,
            "scope": settings.openemr_oauth_scope,
            "user_role": "users",  # OpenEMR-specific: 'users' for staff, 'patient' for portal
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenEMR password-grant token failed [{resp.status_code}]: "
            f"{resp.text[:300]}"
        )
    return resp.json()


async def _fetch_token_refresh(client: httpx.AsyncClient, refresh_token: str) -> dict[str, Any]:
    resp = await client.post(
        f"{settings.openemr_base_url}{settings.openemr_oauth_token_path}",
        data={
            "grant_type": "refresh_token",
            "client_id": settings.openemr_oauth_client_id,
            "client_secret": settings.openemr_oauth_client_secret,
            "refresh_token": refresh_token,
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenEMR refresh-token grant failed [{resp.status_code}]: "
            f"{resp.text[:300]}"
        )
    return resp.json()


async def _get_access_token(client: httpx.AsyncClient) -> str:
    now = time.time()
    if _tokens.access_token and now < _tokens.expires_at:
        return _tokens.access_token

    body: dict[str, Any]
    if _tokens.refresh_token:
        try:
            body = await _fetch_token_refresh(client, _tokens.refresh_token)
        except RuntimeError as exc:
            logger.warning("refresh token rejected, falling back to password grant: %s", exc)
            body = await _fetch_token_password_grant(client)
    else:
        body = await _fetch_token_password_grant(client)

    _tokens.access_token = body["access_token"]
    _tokens.refresh_token = body.get("refresh_token") or _tokens.refresh_token
    # 60-second safety margin so we don't race the expiration.
    _tokens.expires_at = now + int(body.get("expires_in", 3600)) - 60
    return _tokens.access_token


class OpenEMRBridge:
    """One method per FHIR endpoint we use. Patient-scoped methods
    return raw FHIR Bundle entries — the tool layer turns them into
    citation-tagged rows.
    """

    async def _fhir_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, verify=True) as client:
            token = await _get_access_token(client)
            resp = await client.get(
                f"{settings.openemr_base_url}/apis/default/fhir{path}",
                params=params or {},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/fhir+json",
                },
            )
            if resp.status_code == 401:
                # Token went stale mid-request (rare). Force-refresh and retry once.
                _tokens.expires_at = 0
                token = await _get_access_token(client)
                resp = await client.get(
                    f"{settings.openemr_base_url}/apis/default/fhir{path}",
                    params=params or {},
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"},
                )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
        """Pull `.resource` out of every entry in a FHIR Bundle."""
        if bundle.get("resourceType") != "Bundle":
            return []
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]

    # --- patient-scoped reads ---

    async def get_medication_requests(self, patient_uuid: str) -> list[dict[str, Any]]:
        """FHIR MedicationRequest for a patient. Returns the FHIR
        resource shape — the tool layer maps to our row format."""
        bundle = await self._fhir_get("/MedicationRequest", {"patient": patient_uuid})
        return self._entries(bundle)

    # Stubs follow the same pattern as get_medication_requests — fill in
    # for Sunday/v2:
    #
    # async def get_conditions(self, patient_uuid):       /Condition
    # async def get_allergies(self, patient_uuid):        /AllergyIntolerance
    # async def get_encounters(self, patient_uuid):       /Encounter
    # async def get_observations(self, patient_uuid):     /Observation (vitals + labs)
    # async def get_immunizations(self, patient_uuid):    /Immunization
