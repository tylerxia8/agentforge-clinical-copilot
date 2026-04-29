"""Per-patient context bundle — cached on chart open. See ARCHITECTURE.md §5.

The cache is the single biggest latency win. The first chat turn after
chart open reads from Redis instead of fanning out 5+ tool calls.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis

from copilot.context.patient import PatientContext
from copilot.settings import settings

logger = logging.getLogger(__name__)


def _bundle_key(patient_uuid: str) -> str:
    return f"copilot:ctx:{patient_uuid}"


class ContextCache:
    def __init__(self) -> None:
        self._client: redis.Redis | None = None

    def _conn(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(settings.redis_url, decode_responses=True)
        return self._client

    async def get(self, patient_uuid: str) -> dict[str, Any] | None:
        raw = await self._conn().get(_bundle_key(patient_uuid))
        if raw is None:
            return None
        return json.loads(raw)

    async def put(self, patient_uuid: str, bundle: dict[str, Any]) -> None:
        await self._conn().set(
            _bundle_key(patient_uuid),
            json.dumps(bundle),
            ex=settings.context_cache_ttl_seconds,
        )

    async def warm(self, ctx: PatientContext) -> None:
        """Build the per-patient bundle by running all read tools in
        parallel and stash the result. Called from the /warm endpoint.

        TODO(thursday): replace stub bundle with real parallel tool calls.
        """
        from copilot.tools.medications import GetActiveMedicationsTool

        # Sketch — Thursday will fan out 5+ tools concurrently with asyncio.gather
        meds = await GetActiveMedicationsTool().run(ctx, {"patient_uuid": ctx.patient_uuid})
        bundle = {
            "patient_uuid": ctx.patient_uuid,
            "medications": meds.model_dump(),
            # demographics, problems, allergies, encounters, vitals, labs — TBD
        }
        await self.put(ctx.patient_uuid, bundle)
        logger.info("warmed context for patient_uuid=%s", ctx.patient_uuid)

    async def get_or_warm(self, ctx: PatientContext) -> dict[str, Any]:
        existing = await self.get(ctx.patient_uuid)
        if existing is not None:
            return existing
        await self.warm(ctx)
        warmed = await self.get(ctx.patient_uuid)
        assert warmed is not None
        return warmed
