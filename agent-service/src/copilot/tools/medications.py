"""GetActiveMedicationsTool — the canonical example.

This is the join from AUDIT.md §4.2: medications live in BOTH
`prescriptions` and `lists` (where type='medication'), and "currently on"
requires checking active=1 AND (end_date IS NULL OR end_date > today).

Other tools follow this exact shape — see tools/__init__.py for the list.
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult


class GetActiveMedicationsTool(Tool):
    name: ClassVar[str] = "get_active_medications"
    description: ClassVar[str] = (
        "Return the patient's currently active medications, with strength, "
        "route, frequency, and active/end-date metadata. Each row carries "
        "a citation id like 'prescriptions#244' that you must use when "
        "making any claim about that medication. Includes warnings when "
        "the same medication appears with conflicting doses across the "
        "prescriptions and lists tables (a known OpenEMR data hazard)."
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
        prescriptions = await bridge.get_prescriptions(args["patient_uuid"])
        list_meds = await bridge.get_list_medications(args["patient_uuid"])

        today_iso = date.today().isoformat()
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []

        for r in prescriptions:
            if not r.get("active"):
                continue
            end_date = r.get("end_date")
            if end_date and end_date < today_iso:
                continue
            rows.append({
                "id": f"prescriptions#{r['id']}",
                "_patient_uuid": args["patient_uuid"],
                "drug": r.get("drug"),
                "rxnorm": r.get("rxnorm_drugcode"),
                "dosage_text": r.get("dosage"),
                "route": r.get("route"),
                "frequency": r.get("interval"),
                "free_text_instructions": r.get("drug_dosage_instructions"),
                "start_date": r.get("start_date"),
                "end_date": end_date,
                "active": True,
                "source_table": "prescriptions",
            })

        # The "is this also in lists?" cross-check — surfaces UC-3 conflicts.
        rx_drug_names_lower = {(r.get("drug") or "").lower() for r in prescriptions}
        for lr in list_meds:
            if lr.get("activity") != 1:
                continue
            title_lower = (lr.get("title") or "").lower()
            rows.append({
                "id": f"lists#{lr['id']}",
                "_patient_uuid": args["patient_uuid"],
                "drug": lr.get("title"),
                "begin_date": lr.get("begdate"),
                "end_date": lr.get("enddate"),
                "active": True,
                "source_table": "lists",
            })
            if title_lower and title_lower not in rx_drug_names_lower:
                warnings.append(
                    f"lists#{lr['id']} references {lr.get('title')!r} but no active "
                    f"prescriptions row matches this name — possible stale entry "
                    f"(see AUDIT.md §4.2)."
                )

        return ToolResult(rows=rows, warnings=warnings)
