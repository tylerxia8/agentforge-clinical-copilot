"""The boundary-enforcement tests are the most important tests in the
suite — these are the safety property the architecture is built around.

Run with: `pytest tests/`
"""

from __future__ import annotations

import os

# Set required env before importing any settings-dependent module.
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENEMR_BASE_URL", "http://localhost:8080")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_ID", "test")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_SECRET", "test")
os.environ.setdefault("AGENT_SHARED_SECRET", "test-secret-test-secret")

import pytest

from copilot.context.patient import PatientContext
from copilot.middleware import patient_context as middleware
from copilot.tools.base import Tool, ToolResult


class _FakePatientTool(Tool):
    name = "fake_patient_tool"
    description = "test"
    input_schema = {"type": "object"}
    requires_patient = True
    patient_arg = "patient_uuid"

    async def run(self, ctx, args):  # pragma: no cover — never called in this test
        return ToolResult()


class _FakeProviderTool(Tool):
    name = "get_today_schedule"
    description = "test"
    input_schema = {"type": "object"}
    requires_patient = False

    async def run(self, ctx, args):  # pragma: no cover
        return ToolResult()


def _ctx(patient_uuid: str = "patient-A") -> PatientContext:
    return PatientContext(
        user_id=1, patient_uuid=patient_uuid,
        encounter_uuid=None, issued_at=0, nonce="x",
    )


def test_blocks_cross_patient_call():
    ctx = _ctx("patient-A")
    with pytest.raises(middleware.CrossPatientAccessError):
        middleware.enforce_tool_call(ctx, _FakePatientTool(), {"patient_uuid": "patient-B"})


def test_allows_same_patient_call():
    ctx = _ctx("patient-A")
    middleware.enforce_tool_call(ctx, _FakePatientTool(), {"patient_uuid": "patient-A"})


def test_allows_whitelisted_provider_scoped_tool():
    ctx = _ctx("patient-A")
    middleware.enforce_tool_call(ctx, _FakeProviderTool(), {})


def test_drops_cross_patient_rows_from_result():
    ctx = _ctx("patient-A")
    result = ToolResult(rows=[
        {"id": "row#1", "_patient_uuid": "patient-A", "v": "kept"},
        {"id": "row#2", "_patient_uuid": "patient-B", "v": "dropped"},
        {"id": "row#3", "_patient_uuid": "patient-A", "v": "kept"},
    ])
    cleaned = middleware.enforce_tool_result(ctx, _FakePatientTool(), result)
    assert {r["v"] for r in cleaned.rows} == {"kept"}
    assert len(cleaned.rows) == 2
