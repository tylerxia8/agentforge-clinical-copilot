"""GetImmunizationsTool — calls FHIR /Immunization.

Returns vaccine administrations as one row each, citation id of the
form `Immunization#<uuid>`. Limited to ``status=completed`` because
the agent should only surface vaccines actually administered, never
pending or refused entries (those are workflow state, not clinical
fact).

Pairs naturally with the W2 evidence-retriever's ACIP chunks:
"Patient last got Tdap in 2018 [Immunization#abc]; ACIP recommends
booster every 10 years [Guideline#acip-adult-tdap-td-2024], so a
Tdap is indicated this visit."
"""

from __future__ import annotations

from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult
from copilot.tools.labs import _coding_code, _coding_display


class GetImmunizationsTool(Tool):
    name: ClassVar[str] = "get_immunizations"
    description: ClassVar[str] = (
        "Return the patient's administered immunizations from FHIR "
        "Immunization, most-recent-first. Each row carries a citation "
        "id like 'Immunization#<uuid>' that you must use when making "
        "any claim about a vaccine. Excludes refused, entered-in-error, "
        "and not-done entries — only completed administrations."
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
                "description": "Max immunizations to return; defaults to 20.",
                "default": 20,
            },
        },
        "required": ["patient_uuid"],
    }

    async def run(self, ctx: PatientContext, args: dict[str, Any]) -> ToolResult:
        bridge = OpenEMRBridge()
        resources = await bridge.get_immunizations(args["patient_uuid"])
        limit = int(args.get("limit", 20))

        rows: list[dict[str, Any]] = []

        for r in resources:
            if r.get("resourceType") != "Immunization":
                continue
            if r.get("status") != "completed":
                # Skip entered-in-error / not-done — those are workflow
                # state, not clinical fact.
                continue

            imm_uuid = r.get("id")
            if not imm_uuid:
                continue

            patient_ref = (r.get("patient") or {}).get("reference", "")
            row_patient_uuid = (
                patient_ref.removeprefix("Patient/")
                if patient_ref.startswith("Patient/")
                else args["patient_uuid"]
            )

            rows.append({
                "id": f"Immunization#{imm_uuid}",
                "_patient_uuid": row_patient_uuid,
                "vaccine_name": _coding_display(r.get("vaccineCode")),
                "cvx": _coding_code(r.get("vaccineCode"), system_endswith="cvx"),
                "occurrence_datetime": r.get("occurrenceDateTime")
                                        or r.get("occurrenceString"),
                "lot_number": r.get("lotNumber"),
                "dose_number": _dose_number(r),
                "status": r.get("status"),
            })

        rows.sort(key=lambda x: x.get("occurrence_datetime") or "", reverse=True)
        return ToolResult(rows=rows[:limit])


def _dose_number(imm: dict[str, Any]) -> str | None:
    """FHIR Immunization.protocolApplied[].doseNumberPositiveInt is
    where the "dose 2 of 3" lives. Surface as a string because some
    payers send it as text ("booster")."""
    for proto in imm.get("protocolApplied") or []:
        n = proto.get("doseNumberPositiveInt")
        if n is not None:
            return str(n)
        s = proto.get("doseNumberString")
        if s:
            return s
    return None
