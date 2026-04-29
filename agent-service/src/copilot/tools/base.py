"""Tool ABC. Every agent capability is a subclass.

Two contracts the orchestrator + middleware depend on:
1. `requires_patient` and `patient_arg` declare whether this tool is
   patient-scoped (and which arg holds the patient handle).
2. `ToolResult.rows` is a list of dicts; each dict carries an `id`
   (used for citations) and a `_patient_uuid` (used by the middleware
   to enforce the boundary).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from copilot.context.patient import PatientContext


class ToolResult(BaseModel):
    """The shape every tool returns. Stable contract for the LLM."""

    rows: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    # Boundary metadata — used by middleware/patient_context.py.
    requires_patient: ClassVar[bool] = True
    patient_arg: ClassVar[str] = "patient_uuid"

    @abstractmethod
    async def run(self, ctx: PatientContext, args: dict[str, Any]) -> ToolResult: ...

    def to_anthropic_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
