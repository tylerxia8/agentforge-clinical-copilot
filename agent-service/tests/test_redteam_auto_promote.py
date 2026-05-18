"""Tests for the W4 v4 auto-promote-on-green subscriber.

The subscriber's load-bearing rules:
- Green replays (failed_count == 0 AND error_count == 0) trigger
  promotion of each passed pending case.
- Critical-severity findings always stay pending, even on green.
- Live cases (is_pending=False) are never promoted.
- ``enabled=False`` mode observes events and emits skipped events
  but doesn't touch the filesystem.
- Idempotent: re-running on already-promoted findings emits a
  skip rather than crashing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import pytest  # noqa: E402

from redteam.auto_promote import AutoPromoteSubscriber  # noqa: E402
from redteam.signals import (  # noqa: E402
    EVENT_FINDING_PROMOTED,
    EVENT_FINDING_PROMOTION_SKIPPED,
    EVENT_REPLAY_CASE_EVALUATED,
    EVENT_REPLAY_COMPLETED,
    SignalBus,
    SignalEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_pending(tmp_path: Path, vuln_id: str, severity: str) -> tuple[Path, Path]:
    """Write a _pending/ sidecar JSON + a _pending/ markdown for
    a fictional finding. Returns the (findings_dir, vulns_dir)
    paths the subscriber should read."""
    findings = tmp_path / "adversarial_findings"
    pending_findings = findings / "_pending"
    pending_findings.mkdir(parents=True, exist_ok=True)
    (pending_findings / f"{vuln_id}.json").write_text(json.dumps({
        "case_id": f"adv_{vuln_id.lower()}",
        "category": "adversarial",
        "vuln_id": vuln_id,
        "fire": {"kind": "chat", "patient_uuid": "x", "message": "y"},
        "rubrics": ["safe_refusal"],
        "severity": severity,
    }), encoding="utf-8")

    vulns = tmp_path / "vulns"
    pending_vulns = vulns / "_pending"
    pending_vulns.mkdir(parents=True, exist_ok=True)
    (pending_vulns / f"{vuln_id}.md").write_text(
        f"# {vuln_id} — test\nbody\n",
        encoding="utf-8",
    )
    return findings, vulns


def _case_event(replay_id: str, vuln_id: str, *, passed: bool, is_pending: bool) -> SignalEvent:
    return SignalEvent(
        event_type=EVENT_REPLAY_CASE_EVALUATED,
        timestamp=datetime.now(timezone.utc),
        payload={
            "replay_id": replay_id,
            "case_id": f"adv_{vuln_id.lower()}",
            "vuln_id": vuln_id,
            "is_pending": is_pending,
            "passed": passed,
            "rubric_results": {"safe_refusal": passed},
            "error": None,
        },
    )


def _completed_event(replay_id: str, *, failed: int = 0, errored: int = 0) -> SignalEvent:
    return SignalEvent(
        event_type=EVENT_REPLAY_COMPLETED,
        timestamp=datetime.now(timezone.utc),
        payload={
            "replay_id": replay_id,
            "case_count": 1,
            "passed_count": 1 if (failed == 0 and errored == 0) else 0,
            "failed_count": failed,
            "error_count": errored,
            "elapsed_seconds": 1.0,
        },
    )


def _drain(bus: SignalBus, event_type: str) -> list[SignalEvent]:
    return [e for e in bus.events() if e.event_type == event_type]


# ---------------------------------------------------------------------------
# Green-replay promotion path
# ---------------------------------------------------------------------------


def test_green_replay_promotes_high_severity_pending(tmp_path: Path) -> None:
    findings, vulns = _setup_pending(tmp_path, "VULN-H1", severity="high")
    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r1"
    sub.on_event(_case_event(rid, "VULN-H1", passed=True, is_pending=True))
    sub.on_event(_completed_event(rid))

    promoted = _drain(bus, EVENT_FINDING_PROMOTED)
    assert len(promoted) == 1
    assert promoted[0].payload["vuln_id"] == "VULN-H1"
    # Files moved
    assert (findings / "VULN-H1.json").exists()
    assert not (findings / "_pending" / "VULN-H1.json").exists()
    assert (vulns / "VULN-H1.md").exists()
    assert not (vulns / "_pending" / "VULN-H1.md").exists()


# ---------------------------------------------------------------------------
# Critical-severity preservation
# ---------------------------------------------------------------------------


def test_critical_severity_stays_pending_on_green(tmp_path: Path) -> None:
    """The trust gate's core invariant: critical findings always
    require human review, regardless of replay outcome."""
    findings, vulns = _setup_pending(tmp_path, "VULN-C1", severity="critical")
    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r1"
    sub.on_event(_case_event(rid, "VULN-C1", passed=True, is_pending=True))
    sub.on_event(_completed_event(rid))

    promoted = _drain(bus, EVENT_FINDING_PROMOTED)
    skipped = _drain(bus, EVENT_FINDING_PROMOTION_SKIPPED)
    assert len(promoted) == 0
    assert len(skipped) == 1
    assert skipped[0].payload["vuln_id"] == "VULN-C1"
    assert "critical" in skipped[0].payload["reason"].lower()
    # Files NOT moved
    assert (findings / "_pending" / "VULN-C1.json").exists()
    assert not (findings / "VULN-C1.json").exists()


# ---------------------------------------------------------------------------
# Failed/errored replay = no promotions at all
# ---------------------------------------------------------------------------


def test_failed_replay_promotes_nothing(tmp_path: Path) -> None:
    findings, vulns = _setup_pending(tmp_path, "VULN-H2", severity="high")
    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r2"
    sub.on_event(_case_event(rid, "VULN-H2", passed=True, is_pending=True))
    sub.on_event(_completed_event(rid, failed=1))  # one failure in the run

    promoted = _drain(bus, EVENT_FINDING_PROMOTED)
    skipped = _drain(bus, EVENT_FINDING_PROMOTION_SKIPPED)
    assert len(promoted) == 0
    assert len(skipped) == 1
    assert "non-green" in skipped[0].payload["reason"].lower()
    assert (findings / "_pending" / "VULN-H2.json").exists()


def test_errored_replay_promotes_nothing(tmp_path: Path) -> None:
    findings, vulns = _setup_pending(tmp_path, "VULN-H3", severity="high")
    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r3"
    sub.on_event(_case_event(rid, "VULN-H3", passed=True, is_pending=True))
    sub.on_event(_completed_event(rid, errored=1))

    promoted = _drain(bus, EVENT_FINDING_PROMOTED)
    assert len(promoted) == 0


# ---------------------------------------------------------------------------
# Live cases ignored
# ---------------------------------------------------------------------------


def test_live_cases_are_not_promoted_again(tmp_path: Path) -> None:
    """A case with is_pending=False is already-live; no work to do."""
    findings, vulns = _setup_pending(tmp_path, "VULN-L1", severity="high")
    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r4"
    sub.on_event(_case_event(rid, "VULN-L1", passed=True, is_pending=False))
    sub.on_event(_completed_event(rid))

    promoted = _drain(bus, EVENT_FINDING_PROMOTED)
    skipped = _drain(bus, EVENT_FINDING_PROMOTION_SKIPPED)
    assert len(promoted) == 0
    assert len(skipped) == 0  # live cases silently ignored, not "skipped" with a reason
    assert (findings / "_pending" / "VULN-L1.json").exists()


# ---------------------------------------------------------------------------
# Disabled mode: observe + report, don't act
# ---------------------------------------------------------------------------


def test_disabled_mode_emits_skip_but_does_not_move(tmp_path: Path) -> None:
    """``enabled=False`` is the safe default — the subscriber still
    runs and surfaces what *would have been promoted*, but no
    files are touched."""
    findings, vulns = _setup_pending(tmp_path, "VULN-D1", severity="high")
    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=False, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r5"
    sub.on_event(_case_event(rid, "VULN-D1", passed=True, is_pending=True))
    sub.on_event(_completed_event(rid))

    skipped = _drain(bus, EVENT_FINDING_PROMOTION_SKIPPED)
    assert len(skipped) == 1
    assert "disabled" in skipped[0].payload["reason"].lower()
    # Files NOT moved
    assert (findings / "_pending" / "VULN-D1.json").exists()
    assert not (findings / "VULN-D1.json").exists()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_re_running_after_promotion_is_idempotent(tmp_path: Path) -> None:
    """Running the subscriber twice on the same case should not
    crash. The second run should emit a skip (no _pending/
    artifacts left to move)."""
    findings, vulns = _setup_pending(tmp_path, "VULN-I1", severity="high")
    bus1 = SignalBus()
    sub1 = AutoPromoteSubscriber(
        bus=bus1, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus1.subscribe(sub1.on_event)
    rid_a = "r-a"
    sub1.on_event(_case_event(rid_a, "VULN-I1", passed=True, is_pending=True))
    sub1.on_event(_completed_event(rid_a))
    assert len(_drain(bus1, EVENT_FINDING_PROMOTED)) == 1

    # Second run on the same finding (already moved)
    bus2 = SignalBus()
    sub2 = AutoPromoteSubscriber(
        bus=bus2, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus2.subscribe(sub2.on_event)
    rid_b = "r-b"
    sub2.on_event(_case_event(rid_b, "VULN-I1", passed=True, is_pending=True))
    sub2.on_event(_completed_event(rid_b))

    promoted_again = _drain(bus2, EVENT_FINDING_PROMOTED)
    skipped_again = _drain(bus2, EVENT_FINDING_PROMOTION_SKIPPED)
    # No re-promotion (files already in live dir)
    assert len(promoted_again) == 0
    assert len(skipped_again) == 1
    assert "no _pending/ artifacts" in skipped_again[0].payload["reason"]


# ---------------------------------------------------------------------------
# Failed cases never promote
# ---------------------------------------------------------------------------


def test_failed_case_in_green_run_is_unreachable_but_safe(tmp_path: Path) -> None:
    """A green replay (failed_count==0) shouldn't have any
    passed=False cases logically, but defensive: if one slips
    through, we don't promote it."""
    findings, vulns = _setup_pending(tmp_path, "VULN-F1", severity="high")
    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r6"
    sub.on_event(_case_event(rid, "VULN-F1", passed=False, is_pending=True))
    sub.on_event(_completed_event(rid))

    assert len(_drain(bus, EVENT_FINDING_PROMOTED)) == 0


# ---------------------------------------------------------------------------
# Multiple cases across one replay
# ---------------------------------------------------------------------------


def test_multiple_cases_one_replay_routes_correctly(tmp_path: Path) -> None:
    """Mix of critical + high + already-live. Only the high
    pending case should be promoted; the critical and the
    already-live should be handled per their respective rules."""
    findings, vulns = _setup_pending(tmp_path, "VULN-A", severity="critical")
    _setup_pending(tmp_path, "VULN-B", severity="high")
    _setup_pending(tmp_path, "VULN-C", severity="high")

    bus = SignalBus()
    sub = AutoPromoteSubscriber(
        bus=bus, enabled=True, findings_dir=findings, vulns_dir=vulns,
    )
    bus.subscribe(sub.on_event)

    rid = "r7"
    sub.on_event(_case_event(rid, "VULN-A", passed=True, is_pending=True))  # critical → skip
    sub.on_event(_case_event(rid, "VULN-B", passed=True, is_pending=True))  # high → promote
    sub.on_event(_case_event(rid, "VULN-C", passed=True, is_pending=False)) # already live → noop
    sub.on_event(_completed_event(rid))

    promoted = _drain(bus, EVENT_FINDING_PROMOTED)
    skipped = _drain(bus, EVENT_FINDING_PROMOTION_SKIPPED)
    promoted_ids = [e.payload["vuln_id"] for e in promoted]
    skipped_ids = [e.payload["vuln_id"] for e in skipped]
    assert promoted_ids == ["VULN-B"]
    assert "VULN-A" in skipped_ids
    assert "VULN-C" not in skipped_ids  # live cases silently ignored
