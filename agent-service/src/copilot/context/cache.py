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

    async def put(
        self,
        patient_uuid: str,
        bundle: dict[str, Any],
        *,
        ttl_override: int | None = None,
    ) -> None:
        await self._conn().set(
            _bundle_key(patient_uuid),
            json.dumps(bundle),
            ex=ttl_override if ttl_override is not None else settings.context_cache_ttl_seconds,
        )

    async def warm(self, ctx: PatientContext) -> None:
        """Build the per-patient bundle by running implemented read tools
        in parallel and stash the result. Called from /warm and lazily
        from get_or_warm() on cache miss.

        Each tool is fan-out so a slow tool (e.g. lab history) can't
        block the whole bundle; we accept partial success and log the
        failures rather than refusing the whole turn.
        """
        import asyncio

        from copilot.tools.allergies import GetAllergiesTool
        from copilot.tools.encounters import GetRecentEncountersTool
        from copilot.tools.immunizations import GetImmunizationsTool
        from copilot.tools.labs import GetLabHistoryTool
        from copilot.tools.medications import GetActiveMedicationsTool
        from copilot.tools.problems import GetActiveProblemsTool
        from copilot.tools.vitals import GetVitalHistoryTool

        args = {"patient_uuid": ctx.patient_uuid}
        # Order in this list MUST match `keys` below — we zip them.
        # Limits chosen to keep the warmed bundle under ~50 KB JSON
        # for the typical chart so Redis I/O stays cheap.
        tasks = [
            GetActiveMedicationsTool().run(ctx, args),
            GetActiveProblemsTool().run(ctx, args),
            GetAllergiesTool().run(ctx, args),
            GetRecentEncountersTool().run(ctx, {**args, "limit": 5}),
            GetLabHistoryTool().run(ctx, {**args, "limit": 25}),
            GetVitalHistoryTool().run(ctx, {**args, "limit": 10}),
            GetImmunizationsTool().run(ctx, {**args, "limit": 10}),
        ]
        keys = (
            "medications", "problems", "allergies", "encounters",
            "labs", "vitals", "immunizations",
        )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        bundle: dict[str, Any] = {"patient_uuid": ctx.patient_uuid}
        failure_count = 0
        for key, result in zip(keys, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("warm: %s failed: %s", key, result)
                bundle[key] = {"rows": [], "warnings": [f"{key} fetch failed: {result}"]}
                failure_count += 1
            else:
                bundle[key] = result.model_dump()

        # Don't poison the cache with an all-empty bundle. If every tool
        # failed (transient OpenEMR 502 / OAuth blip), skip the write so
        # the next chat turn retries fresh instead of getting the empty
        # bundle from Redis for the full TTL. Manifested in W2 calibration
        # as golden / multistep stuck at 0% even after the live agent
        # recovered: the eval runs ~63 cases sequentially through a single
        # patient context, and a brief 502 mid-run poisoned Farrah's
        # cached bundle for the remaining 5 minutes.
        if failure_count == len(keys):
            logger.warning(
                "warm: all %d tools failed for patient_uuid=%s — "
                "skipping cache write so next call retries",
                len(keys), ctx.patient_uuid,
            )
            return
        # Partial-success bundles get a much shorter TTL (10s) so we
        # retry the failed slots soon, but still serve the successful
        # ones during that window.
        ttl_override = 10 if failure_count > 0 else None
        await self.put(ctx.patient_uuid, bundle, ttl_override=ttl_override)
        logger.info(
            "warmed context for patient_uuid=%s "
            "(meds=%d, problems=%d, allergies=%d, encounters=%d, "
            "labs=%d, vitals=%d, immunizations=%d)",
            ctx.patient_uuid,
            len(bundle["medications"]["rows"]),
            len(bundle["problems"]["rows"]),
            len(bundle["allergies"]["rows"]),
            len(bundle["encounters"]["rows"]),
            len(bundle["labs"]["rows"]),
            len(bundle["vitals"]["rows"]),
            len(bundle["immunizations"]["rows"]),
        )

    async def get_or_warm(self, ctx: PatientContext) -> dict[str, Any]:
        existing = await self.get(ctx.patient_uuid)
        if existing is not None:
            return existing
        await self.warm(ctx)
        warmed = await self.get(ctx.patient_uuid)
        if warmed is not None:
            return warmed
        # warm() declined to cache (every tool failed). Return an empty
        # bundle for this turn — the orchestrator's verifier will refuse
        # rather than hallucinate, and the NEXT turn will retry warm().
        # Mirrors the shape of a healthy bundle so downstream code that
        # dict-accesses keys doesn't crash.
        empty_slot = {"rows": [], "warnings": ["bundle warm failed; will retry next turn"]}
        return {
            "patient_uuid": ctx.patient_uuid,
            "medications": dict(empty_slot),
            "problems":     dict(empty_slot),
            "allergies":    dict(empty_slot),
            "encounters":   dict(empty_slot),
            "labs":         dict(empty_slot),
            "vitals":       dict(empty_slot),
            "immunizations": dict(empty_slot),
        }
