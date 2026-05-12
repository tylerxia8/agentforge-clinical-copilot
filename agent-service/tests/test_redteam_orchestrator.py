"""Orchestrator Agent tests — the deterministic fallback property.

The Orchestrator's LLM-driven prioritization is exercised by live
runs against Anthropic. These tests pin the *deterministic*
behavior: budget enforcement, no-categories halting, fallback
plan when LLM is unavailable, and prompt-construction shape.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import pytest  # noqa: E402

from redteam.coverage import CategoryStats, CoverageReport  # noqa: E402
from redteam.messages import AttackCampaign, ThreatCategory  # noqa: E402
from redteam.orchestrator import (  # noqa: E402
    Orchestrator,
    OrchestratorContext,
    _build_user_prompt,
    _parse_orchestrator_output,
)


def _ctx_with_coverage(
    *,
    per_category: dict[ThreatCategory, CategoryStats] | None = None,
    cost_to_date: float = 0.0,
    ceiling: float = 5.0,
    available: list[ThreatCategory] | None = None,
) -> OrchestratorContext:
    now = datetime.now(timezone.utc)
    coverage = CoverageReport(
        per_category=per_category or {},
        total_attempts=sum(c.attempts for c in (per_category or {}).values()),
        total_campaigns=0,
        open_findings_count=0,
        runs_scanned=0,
        window_start=now - timedelta(hours=24),
        window_end=now,
    )
    # NB: must use `if available is None`, not `available or [...]`.
    # The latter would substitute the default when a caller passed an
    # explicit empty list — which is exactly what the
    # "no available categories" halting test wants to assert.
    if available is None:
        available = [
            ThreatCategory.INDIRECT_INJECTION,
            ThreatCategory.CROSS_PATIENT,
            ThreatCategory.COST_AMPLIFICATION,
        ]
    return OrchestratorContext(
        coverage=coverage,
        cost_to_date_usd=cost_to_date,
        cost_ceiling_usd=ceiling,
        available_categories=available,
    )


# ---------------------------------------------------------------------------
# Budget enforcement (no LLM call needed)
# ---------------------------------------------------------------------------


def test_returns_none_when_budget_exhausted() -> None:
    """ARCHITECTURE.md: when remaining budget is 0, halt the
    platform (return None to the runner)."""
    ctx = _ctx_with_coverage(cost_to_date=5.0, ceiling=5.0)
    orch = Orchestrator(client=None)  # no LLM, deterministic only
    campaign = asyncio.run(orch.plan_next_campaign(ctx))
    assert campaign is None


def test_returns_none_when_budget_negative() -> None:
    """Over-budget cases also halt."""
    ctx = _ctx_with_coverage(cost_to_date=10.0, ceiling=5.0)
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))
    assert campaign is None


def test_returns_none_when_no_available_categories() -> None:
    """If the runner provides no eligible categories — e.g. every
    category is over the per-day cap — halt cleanly."""
    ctx = _ctx_with_coverage(available=[])
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))
    assert campaign is None


# ---------------------------------------------------------------------------
# Deterministic fallback behavior
# ---------------------------------------------------------------------------


def test_deterministic_fallback_chooses_unexplored_category() -> None:
    """Two categories: one has 20 attempts, the other has 0.
    Deterministic fallback should pick the under-explored one."""
    per_category = {
        ThreatCategory.INDIRECT_INJECTION: CategoryStats(
            category=ThreatCategory.INDIRECT_INJECTION,
            attempts=20, fail=20,
        ),
        # CROSS_PATIENT not in per_category (0 attempts)
    }
    ctx = _ctx_with_coverage(per_category=per_category)
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))
    assert campaign is not None
    assert campaign.category in {
        ThreatCategory.CROSS_PATIENT,
        ThreatCategory.COST_AMPLIFICATION,
    }  # not the over-explored indirect_injection
    assert campaign.cost_budget_usd <= ctx.remaining_budget_usd


def test_deterministic_fallback_ties_broken_by_threat_rank() -> None:
    """When two categories are equally under-explored (both at 0),
    the highest threat-model rank wins. From orchestrator.py the
    rank order is indirect_injection (100) > cross_patient (90) >
    cost_amplification (80) > others.

    Both indirect_injection and cross_patient are at 0 attempts;
    indirect_injection should win.
    """
    # Provide ONLY indirect_injection and cross_patient as available
    ctx = _ctx_with_coverage(available=[
        ThreatCategory.INDIRECT_INJECTION,
        ThreatCategory.CROSS_PATIENT,
    ])
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))
    assert campaign is not None
    assert campaign.category == ThreatCategory.INDIRECT_INJECTION


def test_deterministic_fallback_budget_capped_at_remaining() -> None:
    """The fallback campaign's cost_budget_usd cannot exceed the
    remaining-budget ceiling."""
    ctx = _ctx_with_coverage(cost_to_date=4.50, ceiling=5.0)
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))
    assert campaign is not None
    assert campaign.cost_budget_usd <= 0.50  # remaining


def test_deterministic_fallback_rationale_is_explicit() -> None:
    """The rationale string must say 'Deterministic fallback' so a
    reader of the run output can distinguish from LLM-planned
    campaigns. Operationally important for debugging."""
    ctx = _ctx_with_coverage()
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))
    assert campaign is not None
    assert "Deterministic fallback" in campaign.rationale


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_user_prompt_lists_all_available_categories() -> None:
    ctx = _ctx_with_coverage(available=[
        ThreatCategory.INDIRECT_INJECTION,
        ThreatCategory.STATE_CORRUPTION,
    ])
    prompt = _build_user_prompt(ctx)
    assert "indirect_injection" in prompt
    assert "state_corruption" in prompt


def test_user_prompt_includes_cost_state() -> None:
    ctx = _ctx_with_coverage(cost_to_date=1.50, ceiling=5.0)
    prompt = _build_user_prompt(ctx)
    assert "Cost to date: $1.50" in prompt
    assert "Ceiling: $5.00" in prompt
    assert "Remaining: $3.50" in prompt


def test_user_prompt_surfaces_threat_rank_per_category() -> None:
    """The Orchestrator can read threat-model rank in the prompt
    so its priorities aren't pure-text-completion luck."""
    ctx = _ctx_with_coverage(available=[ThreatCategory.INDIRECT_INJECTION])
    prompt = _build_user_prompt(ctx)
    assert "indirect_injection: rank=100" in prompt


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def test_parse_output_strips_code_fences_and_preamble() -> None:
    raw = (
        "Here is my analysis:\n"
        "```json\n"
        '{"category": "indirect_injection", "mode": "generate", "hop_budget": 5, '
        '"cost_budget_usd": 1.0, "rationale": "test"}\n'
        "```\n"
        "Done."
    )
    obj = _parse_orchestrator_output(raw, available=[
        ThreatCategory.INDIRECT_INJECTION,
    ])
    assert obj["category"] == "indirect_injection"


def test_parse_output_rejects_category_not_in_available_list() -> None:
    """The Orchestrator's system prompt says 'Pick from the
    available categories list provided. Do not invent.' The
    parser enforces this — if the LLM picks an unwired category,
    raise ValueError so the runner falls back to deterministic."""
    raw = '{"category": "made_up_category", "hop_budget": 5}'
    with pytest.raises(ValueError, match="not in available"):
        _parse_orchestrator_output(raw, available=[
            ThreatCategory.INDIRECT_INJECTION,
        ])


def test_parse_output_rejects_non_json_garbage() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        _parse_orchestrator_output(
            "I won't pick a category, this is unsafe research.",
            available=[ThreatCategory.INDIRECT_INJECTION],
        )
