"""Tool registry — what the agent can call.

Each tool is a class implementing `Tool` (see `base.py`). New tools should
follow the same shape as `medications.py` — declare `requires_patient`,
implement `run()`, return rows tagged with `_patient_uuid`.

To wire up: add the tool class to `ALL_TOOLS` below.

TODO(thursday): implement these tools (each will mirror the medications
pattern, just hitting a different REST endpoint):
- GetActiveProblemsTool
- GetAllergiesTool
- GetRecentEncountersTool
- GetLabHistoryTool
- GetVitalHistoryTool
- GetImmunizationsTool
- GetPreventiveCareDueTool
- GetTodayScheduleTool   (provider-scoped, not patient-scoped)
- GetPatientSummaryTool  (composite — calls others)
"""

from copilot.tools.base import Tool, ToolResult
from copilot.tools.medications import GetActiveMedicationsTool

ALL_TOOLS: list[Tool] = [
    GetActiveMedicationsTool(),
    # Add others here as they're implemented.
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
