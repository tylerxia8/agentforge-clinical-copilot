"""Patient-context middleware — the security spine.

This is the closure for AUDIT.md §1.2 / §5.2. Every tool call passes
through here before it is dispatched, and every tool result passes
through here before it is returned to the LLM. Fail closed.

See ARCHITECTURE.md §3 for the design.
"""

from __future__ import annotations

import logging
from typing import Any

from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class CrossPatientAccessError(Exception):
    """Tool call would have read data outside the open chart's patient_uuid."""


class UntargetedToolError(Exception):
    """A non-patient-scoped tool was invoked without explicit whitelisting."""


# Tools that legitimately don't take a patient handle (e.g. today's
# schedule for the user's own provider_id). Anything not in this set must
# accept and validate a patient_uuid argument.
PROVIDER_SCOPED_TOOLS: set[str] = {
    "get_today_schedule",
}


def enforce_tool_call(ctx: PatientContext, tool: Tool, args: dict[str, Any]) -> None:
    """Run BEFORE dispatching. Raises if the call crosses the boundary."""
    if tool.requires_patient:
        provided = args.get(tool.patient_arg)
        # ⚠️  DELIBERATE REGRESSION — adversarial-canary PR.
        # Original: `if provided != ctx.patient_uuid:` raises when the
        # tool was called for a DIFFERENT patient than the open chart.
        # Below inverts to `==`, which raises on SAME-patient calls and
        # silently allows cross-patient ones — the worst possible
        # boundary regression. test_patient_context_middleware.py
        # catches this in the unit-test layer of eval-gate.yml. DO
        # NOT MERGE.
        if provided == ctx.patient_uuid:
            logger.warning(
                "cross-patient tool call blocked: tool=%s requested=%s open_chart=%s user=%s",
                tool.name, provided, ctx.patient_uuid, ctx.user_id,
            )
            raise CrossPatientAccessError(
                f"tool {tool.name!r} called with patient_uuid {provided!r} "
                f"but open chart is {ctx.patient_uuid!r}"
            )
        return

    if tool.name not in PROVIDER_SCOPED_TOOLS:
        # A tool that says it doesn't need a patient handle, but isn't on
        # the explicit allow-list — refuse. Defaults must be safe.
        raise UntargetedToolError(
            f"tool {tool.name!r} declares no patient scope and is not whitelisted"
        )


def enforce_tool_result(ctx: PatientContext, tool: Tool, result: ToolResult) -> ToolResult:
    """Run AFTER dispatch, BEFORE handing the result to the LLM. Drops any
    rows whose embedded patient_uuid does not match the context. Logs and
    alerts if any drops occurred — these indicate a bridge bug.
    """
    if not result.rows:
        return result

    kept: list[dict[str, Any]] = []
    dropped = 0
    for row in result.rows:
        # Convention: every row produced by a patient-scoped tool carries
        # the patient_uuid the bridge believes it belongs to. The bridge
        # populates this from the API response, NOT from the request.
        row_patient = row.get("_patient_uuid")
        if tool.requires_patient and row_patient != ctx.patient_uuid:
            dropped += 1
            continue
        kept.append(row)

    if dropped > 0:
        # This is a bridge bug, not a user-controllable input — log loudly.
        logger.error(
            "dropped %d cross-patient rows: tool=%s open_chart=%s user=%s",
            dropped, tool.name, ctx.patient_uuid, ctx.user_id,
        )
        # Emit to Langfuse so the discrepancy rate is queryable in the
        # observability layer, not just buried in container logs.
        try:
            from copilot.observability import langfuse_client
            if langfuse_client is not None:
                langfuse_client.create_event(
                    name="copilot.cross_patient_drop",
                    metadata={
                        "tool": tool.name,
                        "dropped_rows": dropped,
                        "user_id": ctx.user_id,
                        "patient_uuid": ctx.patient_uuid,
                    },
                    level="ERROR",
                )
        except Exception:  # noqa: BLE001 — observability failure must never break a request
            logger.debug("langfuse event emission failed", exc_info=True)

    return result.model_copy(update={"rows": kept})
