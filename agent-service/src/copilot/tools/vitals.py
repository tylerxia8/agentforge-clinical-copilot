"""GetVitalHistoryTool — calls FHIR /Observation with category=vital-signs.

Returns vital readings as one row per observation, citation id of
the form `Observation#<uuid>`. Differs from labs in two ways:

1. **Component values.** A blood-pressure reading is one Observation
   with two component sub-values (systolic + diastolic). The tool
   surfaces both via a `components` list so the agent can cite a
   single Observation but reference both numbers in one sentence
   ("BP 132/84 [Observation#abc]").

2. **AUDIT.md §4.6 unit rule.** form_vitals stores temperature
   without a unit column — 37 could be C or F. The agent prompt
   already enforces "(units not recorded)" if no unit is present.
   This tool propagates the FHIR unit verbatim if the OpenEMR FHIR
   layer provided one; if not, the row's ``unit`` field is None
   and the verifier's vitals-unit rule catches any value claimed
   without a unit.
"""

from __future__ import annotations

from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult
from copilot.tools.labs import (
    _coding_code,
    _coding_display,
    _has_category,
    _interpretation,
    _reference_range,
)

_RELIABLE_STATUSES = frozenset({"final", "amended", "corrected"})


class GetVitalHistoryTool(Tool):
    name: ClassVar[str] = "get_vital_history"
    description: ClassVar[str] = (
        "Return the patient's vital-sign measurements from FHIR "
        "Observation (category=vital-signs), most-recent-first. Each "
        "row carries a citation id like 'Observation#<uuid>' that you "
        "must use when making any claim about a vital. Composite "
        "readings (BP) come back with a `components` list — cite the "
        "single Observation when referring to either component. If a "
        "value has no unit, surface it as '(units not recorded)' "
        "rather than guessing — the chart genuinely doesn't know."
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
                "description": "Max number of vital readings to return; defaults to 15.",
                "default": 15,
            },
        },
        "required": ["patient_uuid"],
    }

    async def run(self, ctx: PatientContext, args: dict[str, Any]) -> ToolResult:
        bridge = OpenEMRBridge()
        resources = await bridge.get_observations(
            args["patient_uuid"], category="vital-signs"
        )
        limit = int(args.get("limit", 15))

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []

        for r in resources:
            if r.get("resourceType") != "Observation":
                continue
            if r.get("status") not in _RELIABLE_STATUSES:
                continue
            if not _has_category(r, "vital-signs"):
                continue

            obs_uuid = r.get("id")
            if not obs_uuid:
                continue

            subject_ref = (r.get("subject") or {}).get("reference", "")
            row_patient_uuid = (
                subject_ref.removeprefix("Patient/")
                if subject_ref.startswith("Patient/")
                else args["patient_uuid"]
            )

            row: dict[str, Any] = {
                "id": f"Observation#{obs_uuid}",
                "_patient_uuid": row_patient_uuid,
                "vital_name": _coding_display(r.get("code")),
                "loinc": _coding_code(r.get("code"), system_endswith="loinc.org"),
                "effective_datetime": r.get("effectiveDateTime")
                                       or (r.get("effectivePeriod") or {}).get("start"),
                "interpretation": _interpretation(r),
                "reference_range": _reference_range(r),
                "status": r.get("status"),
            }

            qty = r.get("valueQuantity") or {}
            if qty:
                row["value"] = qty.get("value")
                row["unit"] = qty.get("unit") or qty.get("code")
                if not row["unit"]:
                    warnings.append(
                        f"Observation#{obs_uuid} ({row['vital_name']}) has "
                        "no unit on the FHIR resource — agent must surface "
                        "as '(units not recorded)'."
                    )

            comps = r.get("component") or []
            if comps:
                row["components"] = []
                for comp in comps:
                    cqty = comp.get("valueQuantity") or {}
                    row["components"].append({
                        "name": _coding_display(comp.get("code")),
                        "loinc": _coding_code(comp.get("code"), system_endswith="loinc.org"),
                        "value": cqty.get("value"),
                        "unit": cqty.get("unit") or cqty.get("code"),
                    })

            if row.get("value") is None and not row.get("components"):
                # Some vital observations come through with no value at all
                # (a stub for "we tried to take this but couldn't"). Flag
                # rather than surface as data.
                warnings.append(
                    f"Observation#{obs_uuid} ({row['vital_name']}) has no "
                    "valueQuantity and no components — included as metadata only."
                )

            rows.append(row)

        rows.sort(key=lambda x: x.get("effective_datetime") or "", reverse=True)
        return ToolResult(rows=rows[:limit], warnings=warnings)
