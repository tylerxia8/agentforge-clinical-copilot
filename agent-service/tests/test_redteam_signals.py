"""Tests for the W4 observability signal stream.

Covers the load-bearing math: SignalBus pub/sub + JSONL
persistence, CoverageMonitor's rolling-window aggregation,
and the recent_shifts detector that's the signal the
Orchestrator subscribes to.

Also covers the Orchestrator integration — the deterministic
fallback should choose a positive-traction shift over the
default lowest-attempt category. That's the proof the
observability hook actually influences the decision, not just
the prompt.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import pytest  # noqa: E402

from redteam.coverage import CategoryStats, CoverageReport  # noqa: E402
from redteam.messages import ThreatCategory  # noqa: E402
from redteam.orchestrator import (  # noqa: E402
    Orchestrator,
    OrchestratorContext,
    _build_user_prompt,
)
from redteam.signals import (  # noqa: E402
    EVENT_VERDICT_DELIVERED,
    CoverageMonitor,
    SignalBus,
    SignalEvent,
    emit_verdict_delivered,
)


# ---------------------------------------------------------------------------
# SignalBus
# ---------------------------------------------------------------------------


def _verdict_event(category: ThreatCategory, verdict: str) -> SignalEvent:
    return SignalEvent(
        event_type=EVENT_VERDICT_DELIVERED,
        timestamp=datetime.now(timezone.utc),
        campaign_id="00000000-0000-0000-0000-000000000000",
        attempt_id="00000000-0000-0000-0000-000000000001",
        category=category.value,
        payload={"verdict": verdict, "confidence": 0.9, "severity": "low"},
    )


def test_signalbus_delivers_to_subscriber() -> None:
    bus = SignalBus()
    received: list[SignalEvent] = []
    bus.subscribe(received.append)
    ev = _verdict_event(ThreatCategory.CROSS_PATIENT, "fail")
    bus.publish(ev)
    assert received == [ev]


def test_signalbus_persists_to_jsonl(tmp_path: Path) -> None:
    persist = tmp_path / "signals.jsonl"
    bus = SignalBus(persist_path=persist)
    bus.publish(_verdict_event(ThreatCategory.STATE_CORRUPTION, "success"))
    bus.publish(_verdict_event(ThreatCategory.STATE_CORRUPTION, "partial"))

    lines = persist.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event_type"] == EVENT_VERDICT_DELIVERED
    assert first["category"] == "state_corruption"
    assert first["payload"]["verdict"] == "success"


def test_signalbus_replay_from_jsonl(tmp_path: Path) -> None:
    persist = tmp_path / "signals.jsonl"
    bus = SignalBus(persist_path=persist)
    bus.publish(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
    bus.publish(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))

    replayed = SignalBus.replay_from_jsonl(persist)
    assert len(replayed) == 2
    assert all(e.event_type == EVENT_VERDICT_DELIVERED for e in replayed)
    assert [e.category for e in replayed] == ["cross_patient", "cross_patient"]


def test_signalbus_subscriber_exception_does_not_break_publish() -> None:
    """Subscriber errors must be isolated — one broken subscriber
    cannot kill the campaign runner."""
    bus = SignalBus()

    def boom(_ev: SignalEvent) -> None:
        raise RuntimeError("kaboom")

    received: list[SignalEvent] = []
    bus.subscribe(boom)
    bus.subscribe(received.append)

    bus.publish(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
    assert len(received) == 1  # the second subscriber still got it


# ---------------------------------------------------------------------------
# CoverageMonitor — rolling-window aggregation
# ---------------------------------------------------------------------------


def test_monitor_aggregates_verdicts_per_category() -> None:
    monitor = CoverageMonitor()
    monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "success"))
    monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))
    monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
    monitor.on_event(_verdict_event(ThreatCategory.STATE_CORRUPTION, "fail"))

    snap = monitor.live_snapshot()
    assert snap[ThreatCategory.CROSS_PATIENT].attempts == 3
    assert snap[ThreatCategory.CROSS_PATIENT].success == 1
    assert snap[ThreatCategory.CROSS_PATIENT].partial == 1
    assert snap[ThreatCategory.CROSS_PATIENT].fail == 1
    assert snap[ThreatCategory.STATE_CORRUPTION].attempts == 1
    assert snap[ThreatCategory.STATE_CORRUPTION].fail == 1


def test_monitor_ignores_non_verdict_events() -> None:
    """Only verdict.delivered events update the rolling stats —
    the other event types are for the dashboard timeline only."""
    monitor = CoverageMonitor()
    monitor.on_event(SignalEvent(
        event_type="campaign.started",
        timestamp=datetime.now(timezone.utc),
        campaign_id="c", attempt_id=None, category="cross_patient", payload={},
    ))
    assert monitor.live_snapshot() == {}


def test_monitor_respects_window_size() -> None:
    """A small window should drop oldest events."""
    monitor = CoverageMonitor(window_size=3)
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
    snap = monitor.live_snapshot()
    assert snap[ThreatCategory.CROSS_PATIENT].attempts == 3


# ---------------------------------------------------------------------------
# CoverageMonitor.recent_shifts — the W4 signal the Orchestrator reads
# ---------------------------------------------------------------------------


def test_no_shifts_before_first_snapshot() -> None:
    """The first Orchestrator round has no baseline to diff against —
    recent_shifts() must return [] cleanly, not crash."""
    monitor = CoverageMonitor()
    monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))
    assert monitor.recent_shifts() == []


def test_shift_detected_when_partial_rate_rises() -> None:
    """Round 1: 5 fails on cross_patient → snapshot. Round 2: 5
    partials arrive — partial_rate moves 0→0.5. That's a shift."""
    monitor = CoverageMonitor(shift_threshold=0.10, min_new_attempts=3)
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
    monitor.take_orchestrator_snapshot()

    # New activity: 5 partials
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))

    shifts = monitor.recent_shifts()
    assert len(shifts) == 1
    s = shifts[0]
    assert s.category == ThreatCategory.CROSS_PATIENT
    assert s.partial_rate_delta > 0.10
    assert s.new_attempts_since_snapshot == 5
    assert "cross_patient" in s.summary


def test_no_shift_when_below_threshold() -> None:
    """A 5% partial-rate move should NOT trigger a 10% threshold."""
    monitor = CoverageMonitor(shift_threshold=0.10, min_new_attempts=3)
    for _ in range(20):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
    monitor.take_orchestrator_snapshot()

    # Add 20 more attempts: 1 partial + 19 fails → partial_rate = 1/40 = 0.025
    monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))
    for _ in range(19):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))

    assert monitor.recent_shifts() == []


def test_no_shift_when_too_few_new_attempts() -> None:
    """The min_new_attempts filter prevents single-event spurious
    shifts (e.g. one partial after the snapshot makes partial_rate
    move 100% but only 1 new attempt — noise)."""
    monitor = CoverageMonitor(shift_threshold=0.10, min_new_attempts=3)
    monitor.take_orchestrator_snapshot()

    # Only 2 new attempts (below min_new_attempts=3)
    monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))
    monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))

    assert monitor.recent_shifts() == []


def test_shifts_sorted_highest_magnitude_first() -> None:
    """Two categories both shift; the higher-magnitude one comes
    first so the Orchestrator prompt surfaces the strongest
    signal at the top."""
    monitor = CoverageMonitor(shift_threshold=0.10, min_new_attempts=3)
    # Baseline: 5 fails each on two categories
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
        monitor.on_event(_verdict_event(ThreatCategory.STATE_CORRUPTION, "fail"))
    monitor.take_orchestrator_snapshot()

    # cross_patient: 5 new partials → big shift
    # state_corruption: 5 new attempts with 1 partial + 4 fail → small shift
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))
    monitor.on_event(_verdict_event(ThreatCategory.STATE_CORRUPTION, "partial"))
    for _ in range(4):
        monitor.on_event(_verdict_event(ThreatCategory.STATE_CORRUPTION, "fail"))

    shifts = monitor.recent_shifts()
    # Both categories should produce shifts; cross_patient first.
    assert len(shifts) >= 1
    assert shifts[0].category == ThreatCategory.CROSS_PATIENT


# ---------------------------------------------------------------------------
# Orchestrator integration — live shift drives the deterministic plan
# ---------------------------------------------------------------------------


def _ctx_with_monitor(
    monitor: CoverageMonitor,
    *,
    available: list[ThreatCategory] | None = None,
) -> OrchestratorContext:
    now = datetime.now(timezone.utc)
    coverage = CoverageReport(
        per_category={},
        total_attempts=0,
        total_campaigns=0,
        open_findings_count=0,
        runs_scanned=0,
        window_start=now - timedelta(hours=24),
        window_end=now,
    )
    if available is None:
        available = [
            ThreatCategory.INDIRECT_INJECTION,
            ThreatCategory.CROSS_PATIENT,
            ThreatCategory.STATE_CORRUPTION,
        ]
    return OrchestratorContext(
        coverage=coverage,
        cost_to_date_usd=0.0,
        cost_ceiling_usd=5.0,
        available_categories=available,
        live_monitor=monitor,
    )


def test_deterministic_fallback_picks_category_with_traction_shift() -> None:
    """The W4 hook: a positive partial-rate shift on state_corruption
    should beat the default "lowest-attempt-count + highest-rank"
    pick (which would otherwise be indirect_injection).
    """
    monitor = CoverageMonitor(shift_threshold=0.10, min_new_attempts=3)
    # Baseline: cross_patient + state_corruption both at 5 fails
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
        monitor.on_event(_verdict_event(ThreatCategory.STATE_CORRUPTION, "fail"))
    monitor.take_orchestrator_snapshot()

    # state_corruption suddenly produces 5 partials → shift
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.STATE_CORRUPTION, "partial"))

    ctx = _ctx_with_monitor(monitor)
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))

    assert campaign is not None
    assert campaign.category == ThreatCategory.STATE_CORRUPTION
    assert "Live signal override" in (campaign.rationale or "")


def test_deterministic_fallback_ignores_negative_shifts() -> None:
    """If partial_rate FELL since last decision, that's not a
    "gained traction" signal — the fallback should ignore it and
    use the default explore-first logic."""
    monitor = CoverageMonitor(shift_threshold=0.10, min_new_attempts=3)
    # Baseline: cross_patient at 5 partials (partial_rate=1.0)
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))
    monitor.take_orchestrator_snapshot()

    # 5 new fails — partial_rate drops from 1.0 to 0.5 (negative shift)
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))

    ctx = _ctx_with_monitor(monitor, available=[
        ThreatCategory.INDIRECT_INJECTION,  # 0 attempts, highest rank
        ThreatCategory.CROSS_PATIENT,
    ])
    orch = Orchestrator(client=None)
    campaign = asyncio.run(orch.plan_next_campaign(ctx))

    assert campaign is not None
    assert campaign.category == ThreatCategory.INDIRECT_INJECTION
    assert "Live signal override" not in (campaign.rationale or "")


def test_prompt_includes_live_shift_summary_when_monitor_present() -> None:
    """The Orchestrator's prompt-builder should surface the shift
    text verbatim so the LLM sees the live signal."""
    monitor = CoverageMonitor(shift_threshold=0.10, min_new_attempts=3)
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "fail"))
    monitor.take_orchestrator_snapshot()
    for _ in range(5):
        monitor.on_event(_verdict_event(ThreatCategory.CROSS_PATIENT, "partial"))

    ctx = _ctx_with_monitor(monitor)
    prompt = _build_user_prompt(ctx)
    assert "Live signal stream" in prompt
    assert "cross_patient" in prompt


def test_prompt_omits_signal_section_without_monitor() -> None:
    """No monitor → no 'Live signal stream' section → the prompt
    is identical to the W3 form. Backwards compatibility for
    existing tests and existing run modes."""
    now = datetime.now(timezone.utc)
    coverage = CoverageReport(
        per_category={},
        total_attempts=0, total_campaigns=0, open_findings_count=0,
        runs_scanned=0, window_start=now, window_end=now,
    )
    ctx = OrchestratorContext(
        coverage=coverage,
        cost_to_date_usd=0.0,
        cost_ceiling_usd=5.0,
        available_categories=[ThreatCategory.INDIRECT_INJECTION],
        live_monitor=None,
    )
    prompt = _build_user_prompt(ctx)
    assert "Live signal stream" not in prompt


# ---------------------------------------------------------------------------
# emit_* helpers — sanity check the canonical event shapes
# ---------------------------------------------------------------------------


def test_emit_verdict_delivered_shapes_payload_correctly() -> None:
    """The emit helper is what the runner calls; the schema is a
    contract for the dashboard and the CoverageMonitor's parser."""
    from uuid import uuid4
    bus = SignalBus()
    received: list[SignalEvent] = []
    bus.subscribe(received.append)
    campaign_id = uuid4()
    attempt_id = uuid4()
    emit_verdict_delivered(
        bus,
        campaign_id=campaign_id,
        attempt_id=attempt_id,
        category=ThreatCategory.STATE_CORRUPTION,
        verdict="partial",
        confidence=0.87,
        severity="high",
    )
    assert len(received) == 1
    ev = received[0]
    assert ev.event_type == EVENT_VERDICT_DELIVERED
    assert ev.campaign_id == str(campaign_id)
    assert ev.attempt_id == str(attempt_id)
    assert ev.category == "state_corruption"
    assert ev.payload["verdict"] == "partial"
    assert ev.payload["confidence"] == pytest.approx(0.87)
    assert ev.payload["severity"] == "high"
