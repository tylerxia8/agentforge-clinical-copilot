"""GetAllergiesTool — calls FHIR /AllergyIntolerance.

Allergies are special-cased in the verification layer: ARCHITECTURE.md
§4.2 calls out that every allergy claim must name the verification
state (confirmed / unconfirmed / refuted / entered-in-error). The
agent's prompt is responsible for stating it; this tool surfaces the
field so the agent has it.

Per AUDIT.md §4.3, the deployed schema is loose — `severity_al` is
nullable freetext, `verification` defaults to empty string. We pass
through whatever FHIR returns; the agent's domain rule catches the
"don't claim 'confirmed' without evidence" failure mode.
"""

from __future__ import annotations

from typing import Any, ClassVar

from copilot.bridge.openemr import OpenEMRBridge
from copilot.context.patient import PatientContext
from copilot.tools.base import Tool, ToolResult


# We surface entered-in-error rows so the agent can reason about why
# they're absent if a clinician asks. The agent's prompt is responsible
# for distinguishing "confirmed PCN allergy" from "entered-in-error PCN
# allergy". Excluded: refuted (the patient is no longer believed allergic).
_SURFACE_VERIFICATION = frozenset({
    "confirmed", "unconfirmed", "presumed", "entered-in-error", "",
})


class GetAllergiesTool(Tool):
    name: ClassVar[str] = "get_allergies"
    description: ClassVar[str] = (
        "Return the patient's allergies and intolerances from FHIR "
        "AllergyIntolerance. Each row carries a citation id like "
        "'AllergyIntolerance#<uuid>'. EVERY allergy claim you make "
        "MUST name the verification status (confirmed / unconfirmed / "
        "presumed / entered-in-error) — this is enforced by the "
        "verification layer. Severity, reaction, and substance are "
        "free-text in the underlying schema; surface them verbatim "
        "and don't infer."
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
        resources = await bridge.get_allergies(args["patient_uuid"])

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []

        for r in resources:
            if r.get("resourceType") != "AllergyIntolerance":
                continue

            verification = _coding_value(r.get("verificationStatus")) or ""
            if verification == "refuted":
                continue
            if verification not in _SURFACE_VERIFICATION:
                # Unknown verification state — include but warn.
                warnings.append(
                    f"AllergyIntolerance#{r.get('id', '?')}: unrecognised "
                    f"verification status {verification!r}"
                )

            allergy_uuid = r.get("id")
            if not allergy_uuid:
                continue

            subject_ref = (r.get("patient") or {}).get("reference", "")
            row_patient_uuid = (
                subject_ref.removeprefix("Patient/")
                if subject_ref.startswith("Patient/")
                else args["patient_uuid"]
            )

            rows.append({
                "id": f"AllergyIntolerance#{allergy_uuid}",
                "_patient_uuid": row_patient_uuid,
                "verification_status": verification,
                "clinical_status": _coding_value(r.get("clinicalStatus")),
                "criticality": r.get("criticality"),  # low / high / unable-to-assess
                "category": r.get("category") or [],  # food / medication / environment / biologic
                "substance": _substance_display(r),
                "reactions": _reaction_summaries(r),
                "recorded_date": r.get("recordedDate"),
            })

        return ToolResult(rows=rows, warnings=warnings)


def _coding_value(cc: dict[str, Any] | None) -> str | None:
    if not cc or not isinstance(cc, dict):
        return None
    for c in cc.get("coding") or []:
        code = c.get("code")
        if code:
            return code
    return None


def _substance_display(allergy: dict[str, Any]) -> str | None:
    cc = allergy.get("code") or {}
    text = cc.get("text")
    if text:
        return text
    for c in cc.get("coding") or []:
        if c.get("display"):
            return c["display"]
    return None


def _reaction_summaries(allergy: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in allergy.get("reaction") or []:
        manifestations = []
        for m in r.get("manifestation") or []:
            mtext = m.get("text")
            if mtext:
                manifestations.append(mtext)
                continue
            for c in m.get("coding") or []:
                if c.get("display"):
                    manifestations.append(c["display"])
                    break
        out.append({
            "manifestations": manifestations,
            "severity": r.get("severity"),
            "description": r.get("description"),
        })
    return out
