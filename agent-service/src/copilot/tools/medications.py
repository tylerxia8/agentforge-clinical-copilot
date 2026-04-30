"""GetActiveMedicationsTool — calls FHIR /MedicationRequest.

Returns each active medication as one row tagged with a citation id
of the form `MedicationRequest#<uuid>`. The agent's prompt instructs
it to inline-cite that id whenever it makes a claim about a med.
"""

from __future__ import annotations

from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult


# FHIR MedicationRequest.status values that mean "patient is on this drug now".
# `active` = currently being taken. `on-hold` = temp stop, also relevant
# (the doctor probably wants to know about it). Everything else (`completed`,
# `cancelled`, `stopped`, `entered-in-error`, `draft`, `unknown`) is excluded.
_ACTIVE_STATUSES = frozenset({"active", "on-hold"})


class GetActiveMedicationsTool(Tool):
    name: ClassVar[str] = "get_active_medications"
    description: ClassVar[str] = (
        "Return the patient's currently-active medications from the FHIR "
        "MedicationRequest resource. Each row carries a citation id like "
        "'MedicationRequest#<uuid>' that you must use when making any "
        "claim about that medication. Excludes stopped, cancelled, "
        "completed, and entered-in-error orders."
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
        resources = await bridge.get_medication_requests(args["patient_uuid"])

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []

        for r in resources:
            if r.get("resourceType") != "MedicationRequest":
                continue

            status = r.get("status")
            if status not in _ACTIVE_STATUSES:
                continue

            mr_uuid = r.get("id")
            if not mr_uuid:
                continue

            # Patient compartment check belt-and-suspenders for the middleware.
            subject_ref = (r.get("subject") or {}).get("reference", "")
            row_patient_uuid = subject_ref.removeprefix("Patient/") if subject_ref.startswith("Patient/") else None
            if row_patient_uuid is None:
                row_patient_uuid = args["patient_uuid"]  # tolerate missing subject ref

            rows.append({
                "id": f"MedicationRequest#{mr_uuid}",
                "_patient_uuid": row_patient_uuid,
                "drug_display": _med_display(r),
                "rxnorm": _rxnorm_code(r),
                "dosage_text": _dosage_text(r),
                "status": status,
                "authored_on": r.get("authoredOn"),
                "intent": r.get("intent"),
            })

        return ToolResult(rows=rows, warnings=warnings)


# ─── FHIR field extraction helpers ────────────────────────────────────

def _med_display(mr: dict[str, Any]) -> str | None:
    """Human-readable medication name. Prefers .text, falls back to the
    first coding's display, falls back to None."""
    cc = mr.get("medicationCodeableConcept") or {}
    if isinstance(cc, dict):
        text = cc.get("text")
        if text:
            return text
        for c in cc.get("coding") or []:
            if c.get("display"):
                return c["display"]
    # Some implementations use medicationReference instead.
    ref = (mr.get("medicationReference") or {}).get("display")
    return ref


def _rxnorm_code(mr: dict[str, Any]) -> str | None:
    cc = mr.get("medicationCodeableConcept") or {}
    for c in cc.get("coding") or []:
        if c.get("system", "").endswith("rxnorm"):
            return c.get("code")
    return None


def _dosage_text(mr: dict[str, Any]) -> str | None:
    """Free-text sig — usually populated with strength/route/frequency."""
    di = mr.get("dosageInstruction") or []
    if di and isinstance(di, list):
        first = di[0]
        if isinstance(first, dict):
            return first.get("text")
    return None
