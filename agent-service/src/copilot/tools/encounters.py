"""GetRecentEncountersTool — calls FHIR /Encounter.

The "what changed since last visit" half of UC-1. Returns encounters
sorted most-recent-first with reason / class / period populated.

Per AUDIT.md §4.7 (encounter ambiguity), `form_encounter.date` can be
NULL and `encounter` IDs aren't auto-increment. We sort by FHIR's
`period.start` and treat anything without a date as the oldest.
"""

from __future__ import annotations

from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult


class GetRecentEncountersTool(Tool):
    name: ClassVar[str] = "get_recent_encounters"
    description: ClassVar[str] = (
        "Return the patient's recent visits / encounters from FHIR "
        "Encounter, sorted most-recent-first. Each row carries a "
        "citation id like 'Encounter#<uuid>'. Use this to answer "
        "'what changed since last visit', 'when was she last seen', "
        "or to scope follow-up questions to a specific encounter. "
        "Limit to the most recent 10 unless the user asks for more."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "patient_uuid": {
                "type": "string",
                "description": "The UUID of the patient whose chart is open.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of encounters to return; defaults to 10.",
                "default": 10,
            },
        },
        "required": ["patient_uuid"],
    }

    async def run(self, ctx: PatientContext, args: dict[str, Any]) -> ToolResult:
        bridge = OpenEMRBridge()
        resources = await bridge.get_encounters(args["patient_uuid"])
        limit = int(args.get("limit", 10))

        rows: list[dict[str, Any]] = []
        for r in resources:
            if r.get("resourceType") != "Encounter":
                continue

            enc_uuid = r.get("id")
            if not enc_uuid:
                continue

            subject_ref = (r.get("subject") or {}).get("reference", "")
            row_patient_uuid = (
                subject_ref.removeprefix("Patient/")
                if subject_ref.startswith("Patient/")
                else args["patient_uuid"]
            )

            rows.append({
                "id": f"Encounter#{enc_uuid}",
                "_patient_uuid": row_patient_uuid,
                "status": r.get("status"),
                "class": _class_display(r),
                "period_start": (r.get("period") or {}).get("start"),
                "period_end": (r.get("period") or {}).get("end"),
                "type": _type_display(r),
                "reason_code": _reason_display(r),
            })

        # Most recent first; None dates sink to the bottom.
        rows.sort(key=lambda x: x.get("period_start") or "", reverse=True)
        return ToolResult(rows=rows[:limit])


def _class_display(enc: dict[str, Any]) -> str | None:
    cls = enc.get("class") or {}
    if isinstance(cls, dict):
        return cls.get("display") or cls.get("code")
    return None


def _type_display(enc: dict[str, Any]) -> list[str]:
    types = enc.get("type") or []
    out: list[str] = []
    for t in types:
        text = t.get("text")
        if text:
            out.append(text)
            continue
        for c in t.get("coding") or []:
            if c.get("display"):
                out.append(c["display"])
                break
    return out


def _reason_display(enc: dict[str, Any]) -> list[str]:
    reasons = enc.get("reasonCode") or []
    out: list[str] = []
    for r in reasons:
        text = r.get("text")
        if text:
            out.append(text)
            continue
        for c in r.get("coding") or []:
            if c.get("display"):
                out.append(c["display"])
                break
    return out
