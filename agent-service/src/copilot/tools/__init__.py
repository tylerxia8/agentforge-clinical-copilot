"""Tool registry — what the agent can call.

Each tool is a class implementing `Tool` (see `base.py`). New tools should
follow the same shape as `medications.py` — declare `requires_patient`,
implement `run()`, return rows tagged with `_patient_uuid`.

To wire up: add the tool class to `ALL_TOOLS` below.

Currently wired (W2 review feedback closed labs/vitals/immunizations):

- GetActiveMedicationsTool   /MedicationRequest
- GetActiveProblemsTool      /Condition
- GetAllergiesTool           /AllergyIntolerance
- GetRecentEncountersTool    /Encounter
- GetLabHistoryTool          /Observation?category=laboratory
- GetVitalHistoryTool        /Observation?category=vital-signs
- GetImmunizationsTool       /Immunization

Deferred:

- GetPreventiveCareDueTool   composite over guidelines + chart
- GetTodayScheduleTool       provider-scoped, not patient-scoped
- GetPatientSummaryTool      composite — calls others
"""

from copilot.tools.allergies import GetAllergiesTool
from copilot.tools.base import Tool, ToolResult
from copilot.tools.encounters import GetRecentEncountersTool
from copilot.tools.immunizations import GetImmunizationsTool
from copilot.tools.labs import GetLabHistoryTool
from copilot.tools.medications import GetActiveMedicationsTool
from copilot.tools.problems import GetActiveProblemsTool
from copilot.tools.vitals import GetVitalHistoryTool

ALL_TOOLS: list[Tool] = [
    GetActiveMedicationsTool(),
    GetActiveProblemsTool(),
    GetAllergiesTool(),
    GetRecentEncountersTool(),
    GetLabHistoryTool(),
    GetVitalHistoryTool(),
    GetImmunizationsTool(),
]


def get_tool(name: str) -> Tool | None:
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None


def all_tool_specs() -> list[dict]:
    """Anthropic tool-use API schema for every registered tool."""
    return [t.to_anthropic_spec() for t in ALL_TOOLS]


__all__ = ["Tool", "ToolResult", "ALL_TOOLS", "get_tool", "all_tool_specs"]
