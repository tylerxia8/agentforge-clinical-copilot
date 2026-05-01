"""GetActiveProblemsTool — calls FHIR /Condition.

Surfaces the patient's active problem list. Each row is tagged with
`Condition#<uuid>` for citation. Filters to clinicalStatus = active
(or recurrence/relapse), excluding resolved/inactive/entered-in-error.

Per AUDIT.md §4.4 (the ICD-9/ICD-10 dual-coding hazard): when the
same condition appears under multiple coding systems on the same row,
we surface ALL coding entries so the agent can decide what to mention.
The dedup-across-rows logic is a v2 refinement — for now we trust
OpenEMR's FHIR layer to dedup at the resource level.
"""

from __future__ import annotations

from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult


_ACTIVE_CLINICAL_STATUSES = frozenset({"active", "recurrence", "relapse"})


class GetActiveProblemsTool(Tool):
    name: ClassVar[str] = "get_active_problems"
    description: ClassVar[str] = (
        "Return the patient's active problem list / diagnoses from FHIR "
        "Condition. Each row carries a citation id like "
        "'Condition#<uuid>' that you must use when making any claim "
        "about a diagnosis. Excludes resolved, inactive, and "
        "entered-in-error conditions. Includes ICD-10 / SNOMED / ICD-9 "
        "codes when present — surface the most current one (typically "
        "ICD-10) and only mention older codes if the user asks for the "
        "billing code specifically."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "patient_uuid": {
                "type": "string",
                "description": "The UUID of the patient whose chart is open.",
            },
        },
        "required": ["patient_uuid"],
    }

    async def run(self, ctx: PatientContext, args: dict[str, Any]) -> ToolResult:
        bridge = OpenEMRBridge()
        resources = await bridge.get_conditions(args["patient_uuid"])

        rows: list[dict[str, Any]] = []
        for r in resources:
            if r.get("resourceType") != "Condition":
                continue

            clinical_status = _coding_value(r.get("clinicalStatus"))
            if clinical_status and clinical_status not in _ACTIVE_CLINICAL_STATUSES:
                continue

            cond_uuid = r.get("id")
            if not cond_uuid:
                continue

            subject_ref = (r.get("subject") or {}).get("reference", "")
            row_patient_uuid = (
                subject_ref.removeprefix("Patient/")
                if subject_ref.startswith("Patient/")
                else args["patient_uuid"]
            )

            rows.append({
                "id": f"Condition#{cond_uuid}",
                "_patient_uuid": row_patient_uuid,
                "clinical_status": clinical_status,
                "verification_status": _coding_value(r.get("verificationStatus")),
                "display": _condition_display(r),
                "codes": _all_codings(r),
                "onset_date": r.get("onsetDateTime") or r.get("onsetPeriod", {}).get("start"),
                "recorded_date": r.get("recordedDate"),
            })

        return ToolResult(rows=rows)


def _coding_value(cc: dict[str, Any] | None) -> str | None:
    if not cc or not isinstance(cc, dict):
        return None
    for c in cc.get("coding") or []:
        code = c.get("code")
        if code:
            return code
    return None


def _condition_display(cond: dict[str, Any]) -> str | None:
    cc = cond.get("code") or {}
    text = cc.get("text")
    if text:
        return text
    for c in cc.get("coding") or []:
        if c.get("display"):
            return c["display"]
    return None


def _all_codings(cond: dict[str, Any]) -> list[dict[str, str]]:
    cc = cond.get("code") or {}
    return [
        {
            "system": c.get("system", ""),
            "code": c.get("code", ""),
            "display": c.get("display", ""),
        }
        for c in cc.get("coding") or []
        if c.get("code")
    ]
