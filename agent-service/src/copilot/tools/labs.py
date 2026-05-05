"""GetLabHistoryTool — calls FHIR /Observation with category=laboratory.

Returns each lab result as one row tagged with a citation id of the
form `Observation#<uuid>`. The agent's prompt instructs it to inline-
cite that id whenever it makes a claim about a lab value.

Per AUDIT.md §4.5, OpenEMR's `procedure_result` table can carry
non-numeric results ("POSITIVE", "see comment") in the same column
as numeric ones. The tool emits both ``value_quantity`` (numeric +
unit) and ``value_string`` (any other shape); the agent prompt is
told to surface the string version verbatim and never to coerce a
non-numeric result to a number.
"""

from __future__ import annotations

from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult

# FHIR Observation.status values that mean "this is a real result we
# can use". `final` and `amended` are the everyday cases; `corrected`
# is also kept (a corrected value is the right one to show). Everything
# else (`registered`, `preliminary`, `cancelled`, `entered-in-error`)
# is excluded so the agent never quotes a draft or retracted result.
_RELIABLE_STATUSES = frozenset({"final", "amended", "corrected"})


class GetLabHistoryTool(Tool):
    name: ClassVar[str] = "get_lab_history"
    description: ClassVar[str] = (
        "Return the patient's laboratory results from FHIR Observation "
        "(category=laboratory), most-recent-first. Each row carries a "
        "citation id like 'Observation#<uuid>' that you must use when "
        "making any claim about a lab value. Excludes preliminary, "
        "cancelled, and entered-in-error results. Numeric results are "
        "in `value_quantity`; non-numeric results (POSITIVE / NEGATIVE / "
        "SEE COMMENT) are in `value_string` — surface those verbatim, "
        "never coerce them."
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
                "description": "Max number of results to return; defaults to 25.",
                "default": 25,
            },
        },
        "required": ["patient_uuid"],
    }

    async def run(self, ctx: PatientContext, args: dict[str, Any]) -> ToolResult:
        bridge = OpenEMRBridge()
        resources = await bridge.get_observations(
            args["patient_uuid"], category="laboratory"
        )
        limit = int(args.get("limit", 25))

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []

        for r in resources:
            if r.get("resourceType") != "Observation":
                continue
            if r.get("status") not in _RELIABLE_STATUSES:
                continue
            if not _has_category(r, "laboratory"):
                # Even when we ask the server to filter by category, some
                # endpoints return mixed results — defense in depth.
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

            row = {
                "id": f"Observation#{obs_uuid}",
                "_patient_uuid": row_patient_uuid,
                "test_name": _coding_display(r.get("code")),
                "loinc": _coding_code(r.get("code"), system_endswith="loinc.org"),
                "effective_datetime": r.get("effectiveDateTime")
                                       or (r.get("effectivePeriod") or {}).get("start"),
                "issued": r.get("issued"),
                "interpretation": _interpretation(r),
                "reference_range": _reference_range(r),
                "status": r.get("status"),
            }

            qty = r.get("valueQuantity") or {}
            if qty:
                row["value_quantity"] = {
                    "value": qty.get("value"),
                    "unit": qty.get("unit") or qty.get("code"),
                }
            elif r.get("valueString"):
                row["value_string"] = r.get("valueString")
            else:
                # Some labs come through as valueCodeableConcept (POSITIVE,
                # NEGATIVE) or valueBoolean. Surface as a string so the
                # agent isn't tempted to invent a unit.
                cc = r.get("valueCodeableConcept")
                if cc:
                    row["value_string"] = _coding_display(cc)
                elif "valueBoolean" in r:
                    row["value_string"] = str(r["valueBoolean"])
                else:
                    warnings.append(
                        f"Observation#{obs_uuid} ({row['test_name']}) has no "
                        "value field — included as metadata only."
                    )

            rows.append(row)

        rows.sort(key=lambda x: x.get("effective_datetime") or "", reverse=True)
        return ToolResult(rows=rows[:limit], warnings=warnings)


# ─── shared FHIR helpers (copy-light; could move to a helper module
#     when we have a third tool that needs them) ────────────────────


def _has_category(obs: dict[str, Any], code: str) -> bool:
    for cat in obs.get("category") or []:
        for c in cat.get("coding") or []:
            if c.get("code") == code:
                return True
    return False


def _coding_display(cc: dict[str, Any] | None) -> str | None:
    if not cc:
        return None
    if cc.get("text"):
        return cc["text"]
    for c in cc.get("coding") or []:
        if c.get("display"):
            return c["display"]
    return None


def _coding_code(
    cc: dict[str, Any] | None, *, system_endswith: str
) -> str | None:
    if not cc:
        return None
    for c in cc.get("coding") or []:
        if (c.get("system") or "").endswith(system_endswith):
            return c.get("code")
    return None


def _interpretation(obs: dict[str, Any]) -> list[str]:
    """HL7 interpretation flags (H, L, A, etc.). The verifier's
    medication-active-state rule and any future lab-flag rule will
    consume this verbatim."""
    out: list[str] = []
    for cc in obs.get("interpretation") or []:
        for c in cc.get("coding") or []:
            code = c.get("code")
            if code:
                out.append(code)
    return out


def _reference_range(obs: dict[str, Any]) -> list[dict[str, Any]]:
    """Reference intervals as the FHIR resource carries them. Kept
    verbatim — converting to a single string here would lose
    composite-range info on tests that have multiple ranges per
    age/sex band."""
    out: list[dict[str, Any]] = []
    for rng in obs.get("referenceRange") or []:
        out.append({
            "low": (rng.get("low") or {}).get("value"),
            "low_unit": (rng.get("low") or {}).get("unit"),
            "high": (rng.get("high") or {}).get("value"),
            "high_unit": (rng.get("high") or {}).get("unit"),
            "text": rng.get("text"),
        })
    return out
