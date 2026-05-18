"""Tests for the W4 v3 replay-on-deploy subscriber.

Covers: case loading from disk, the run_replay() emission
sequence (replay.started → replay.case.evaluated* →
replay.completed), per-case grading reusing the W2 rubric
functions, error isolation when a chat call raises, and the
ReplaySubscriber's deploy.fired → schedule pattern.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import pytest  # noqa: E402

from redteam.replay import (  # noqa: E402
    ReplayCase,
    ReplaySubscriber,
    _grade_response,
    load_replay_cases,
    run_replay,
)
from redteam.signals import (  # noqa: E402
    EVENT_DEPLOY_FIRED,
    EVENT_REPLAY_CASE_EVALUATED,
    EVENT_REPLAY_COMPLETED,
    EVENT_REPLAY_STARTED,
    SignalBus,
    SignalEvent,
    emit_deploy_fired,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_sidecar(
    path: Path,
    *,
    case_id: str,
    vuln_id: str,
    rubrics: list[str],
    must_not_mention: list[str] | None = None,
) -> None:
    path.write_text(json.dumps({
        "case_id": case_id,
        "category": "adversarial",
        "vuln_id": vuln_id,
        "description": "test fixture",
        "fire": {
            "kind": "chat",
            "patient_uuid": "00000000-1111-2222-3333-444444444444",
            "message": "Fixture attack message",
        },
        "rubrics": rubrics,
        "must_not_mention": must_not_mention or [],
        "severity": "high",
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# load_replay_cases — disk reader
# ---------------------------------------------------------------------------


def test_load_replay_cases_live_only_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """By default, _pending/ findings are excluded — matches the
    W2 eval suite's adversarial_loader behavior."""
    findings = tmp_path / "adversarial_findings"
    pending = findings / "_pending"
    pending.mkdir(parents=True)
    _write_sidecar(findings / "VULN-0010.json", case_id="adv_10", vuln_id="VULN-0010", rubrics=["safe_refusal"])
    _write_sidecar(pending / "VULN-0011.json", case_id="adv_11", vuln_id="VULN-0011", rubrics=["safe_refusal"])

    monkeypatch.setattr("redteam.replay._findings_root", lambda: findings)

    live_only = load_replay_cases(include_pending=False)
    assert [c.case_id for c in live_only] == ["adv_10"]
    assert all(not c.is_pending for c in live_only)


def test_load_replay_cases_include_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings = tmp_path / "adversarial_findings"
    pending = findings / "_pending"
    pending.mkdir(parents=True)
    _write_sidecar(findings / "VULN-0010.json", case_id="adv_10", vuln_id="VULN-0010", rubrics=["safe_refusal"])
    _write_sidecar(pending / "VULN-0011.json", case_id="adv_11", vuln_id="VULN-0011", rubrics=["safe_refusal"])

    monkeypatch.setattr("redteam.replay._findings_root", lambda: findings)

    both = load_replay_cases(include_pending=True)
    by_id = {c.case_id: c for c in both}
    assert set(by_id) == {"adv_10", "adv_11"}
    assert by_id["adv_10"].is_pending is False
    assert by_id["adv_11"].is_pending is True


def test_load_replay_cases_skips_unsupported_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Future ``kind: "extract"`` sidecars (file-upload attacks)
    aren't supported by the replay subscriber yet; they should
    be skipped rather than crashing the load."""
    findings = tmp_path / "adversarial_findings"
    findings.mkdir(parents=True)
    (findings / "VULN-0012.json").write_text(json.dumps({
        "case_id": "adv_12",
        "category": "adversarial",
        "fire": {"kind": "extract", "doc_type": "lab_pdf", "filename": "foo.pdf"},
        "rubrics": ["safe_refusal"],
    }), encoding="utf-8")
    monkeypatch.setattr("redteam.replay._findings_root", lambda: findings)
    assert load_replay_cases(include_pending=False) == []


# ---------------------------------------------------------------------------
# _grade_response — rubric integration
# ---------------------------------------------------------------------------


def _case(rubrics: list[str], *, must_not_mention: list[str] | None = None) -> ReplayCase:
    return ReplayCase(
        case_id="t",
        vuln_id="VULN-0099",
        is_pending=False,
        patient_uuid="00000000-1111-2222-3333-444444444444",
        message="m",
        rubric_names=rubrics,
        must_not_mention=must_not_mention or [],
    )


def test_grade_response_safe_refusal_pass() -> None:
    """A refusal response with no PHI passes safe_refusal."""
    response = {
        "text": "I can only access the chart that is currently open.",
        "refused": True,
        "refusal_reason": "cross-patient query refused",
        "sources": [],
    }
    results = _grade_response(response, _case(["safe_refusal"]))
    assert results == {"safe_refusal": True}


def test_grade_response_unknown_rubric_fails_closed() -> None:
    """A rubric the replay doesn't know about should fail rather
    than silently passing — defense-in-depth."""
    response = {"text": "", "refused": True, "sources": []}
    results = _grade_response(response, _case(["totally_made_up_rubric"]))
    assert results == {"totally_made_up_rubric": False}


def test_grade_response_response_length_bounded() -> None:
    short = {"text": "ok", "refused": False, "sources": []}
    huge = {"text": "x" * 9000, "refused": False, "sources": []}
    assert _grade_response(short, _case(["response_length_bounded"])) == {"response_length_bounded": True}
    assert _grade_response(huge, _case(["response_length_bounded"])) == {"response_length_bounded": False}


# ---------------------------------------------------------------------------
# run_replay — emission sequence + summary math
# ---------------------------------------------------------------------------


def _drain_events(bus: SignalBus, event_type: str) -> list[SignalEvent]:
    return [e for e in bus.events() if e.event_type == event_type]


def test_run_replay_emits_full_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One started, one per-case-evaluated per case, one completed."""
    findings = tmp_path / "adversarial_findings"
    findings.mkdir(parents=True)
    _write_sidecar(findings / "VULN-A.json", case_id="case_a", vuln_id="VULN-A", rubrics=["safe_refusal"])
    _write_sidecar(findings / "VULN-B.json", case_id="case_b", vuln_id="VULN-B", rubrics=["safe_refusal"])
    monkeypatch.setattr("redteam.replay._findings_root", lambda: findings)

    bus = SignalBus()

    def fake_chat(**_kwargs: Any) -> dict[str, Any]:
        return {
            "text": "I can only access the open chart.",
            "refused": True,
            "refusal_reason": "scope",
            "sources": [],
        }

    result = asyncio.run(run_replay(bus=bus, chat_fire=fake_chat))

    assert len(_drain_events(bus, EVENT_REPLAY_STARTED)) == 1
    assert len(_drain_events(bus, EVENT_REPLAY_CASE_EVALUATED)) == 2
    assert len(_drain_events(bus, EVENT_REPLAY_COMPLETED)) == 1

    assert result.case_count == 2
    assert result.passed_count == 2
    assert result.failed_count == 0
    assert result.error_count == 0


def test_run_replay_counts_failures_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One case passes, one fails grading, one raises mid-chat.

    Uses ``response_length_bounded`` rather than ``safe_refusal``
    because length is trivially controllable from the fake chat —
    safe_refusal needs ``must_not_mention`` AND matching response
    text to actually fail, which makes the fixture brittle.
    """
    findings = tmp_path / "adversarial_findings"
    findings.mkdir(parents=True)
    _write_sidecar(findings / "VULN-A.json", case_id="case_pass", vuln_id="VULN-A", rubrics=["response_length_bounded"])
    _write_sidecar(findings / "VULN-B.json", case_id="case_fail", vuln_id="VULN-B", rubrics=["response_length_bounded"])
    _write_sidecar(findings / "VULN-C.json", case_id="case_err", vuln_id="VULN-C", rubrics=["response_length_bounded"])
    monkeypatch.setattr("redteam.replay._findings_root", lambda: findings)

    bus = SignalBus()

    call_responses = [
        {"text": "short response", "refused": False, "sources": []},       # pass length check
        {"text": "x" * 9000, "refused": False, "sources": []},              # fail length check
    ]
    call_iter = iter(call_responses)

    def varied_chat(**_kwargs: Any) -> dict[str, Any]:
        try:
            return next(call_iter)
        except StopIteration:
            raise RuntimeError("synthetic chat failure")

    result = asyncio.run(run_replay(bus=bus, chat_fire=varied_chat))

    assert result.case_count == 3
    assert result.passed_count == 1
    assert result.failed_count == 1
    assert result.error_count == 1

    # The error case should still emit a replay.case.evaluated event
    # (so the dashboard can show what blew up), just with passed=False
    # and a non-null error field.
    case_events = _drain_events(bus, EVENT_REPLAY_CASE_EVALUATED)
    error_evs = [e for e in case_events if (e.payload or {}).get("error")]
    assert len(error_evs) == 1
    assert "RuntimeError" in error_evs[0].payload["error"]


def test_run_replay_completed_summary_matches_per_case_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replay.completed event's counts must equal the sum of
    the replay.case.evaluated events — the dashboard depends on
    this consistency."""
    findings = tmp_path / "adversarial_findings"
    findings.mkdir(parents=True)
    for i in range(4):
        _write_sidecar(findings / f"VULN-D{i}.json", case_id=f"d_{i}", vuln_id=f"VULN-D{i}", rubrics=["response_length_bounded"])
    monkeypatch.setattr("redteam.replay._findings_root", lambda: findings)

    bus = SignalBus()
    responses = [
        {"text": "short", "refused": False, "sources": []},
        {"text": "short", "refused": False, "sources": []},
        {"text": "x" * 9000, "refused": False, "sources": []},
        {"text": "x" * 9000, "refused": False, "sources": []},
    ]
    it = iter(responses)
    asyncio.run(run_replay(bus=bus, chat_fire=lambda **_: next(it)))

    case_events = _drain_events(bus, EVENT_REPLAY_CASE_EVALUATED)
    completed = _drain_events(bus, EVENT_REPLAY_COMPLETED)[0]
    pl = completed.payload

    assert pl["case_count"] == 4
    assert pl["passed_count"] == sum(1 for e in case_events if e.payload["passed"])
    assert pl["failed_count"] == sum(
        1 for e in case_events
        if not e.payload["passed"] and not e.payload.get("error")
    )
    assert pl["error_count"] == sum(1 for e in case_events if e.payload.get("error"))


# ---------------------------------------------------------------------------
# ReplaySubscriber — deploy.fired triggers run_replay
# ---------------------------------------------------------------------------


def test_subscriber_ignores_non_deploy_events() -> None:
    bus = SignalBus()
    scheduled: list[Any] = []
    sub = ReplaySubscriber(
        bus=bus,
        schedule_fn=lambda coro: scheduled.append(coro) or coro,
    )
    sub.on_event(SignalEvent(
        event_type="verdict.delivered",
        timestamp=datetime.now(timezone.utc),
        category="cross_patient",
        payload={"verdict": "fail", "confidence": 0.9},
    ))
    assert scheduled == []
    # Close any unclosed coroutines (none expected, but defensive)
    for c in scheduled:
        if hasattr(c, "close"):
            c.close()


def test_subscriber_schedules_replay_on_deploy_fired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings = tmp_path / "adversarial_findings"
    findings.mkdir(parents=True)
    _write_sidecar(findings / "VULN-Z.json", case_id="z", vuln_id="VULN-Z", rubrics=["safe_refusal"])
    monkeypatch.setattr("redteam.replay._findings_root", lambda: findings)

    bus = SignalBus()
    fake_chat = lambda **_: {  # noqa: E731
        "text": "Refused", "refused": True, "refusal_reason": "ok", "sources": [],
    }

    async def driver() -> None:
        sub = ReplaySubscriber(bus=bus, chat_fire=fake_chat)
        bus.subscribe(sub.on_event)
        emit_deploy_fired(
            bus,
            target_url="https://test.example",
            image_digest="sha256:abc",
            trigger="unit-test",
        )
        # Drain in-flight replays
        while sub.replays_in_flight:
            t = sub.replays_in_flight.pop()
            await t

    asyncio.run(driver())

    started = _drain_events(bus, EVENT_REPLAY_STARTED)
    completed = _drain_events(bus, EVENT_REPLAY_COMPLETED)
    assert len(started) == 1
    assert len(completed) == 1
    assert completed[0].payload["passed_count"] == 1


def test_subscriber_skips_when_no_event_loop() -> None:
    """If the subscriber receives a deploy.fired with no running
    asyncio loop (e.g., emitted during shutdown), it must NOT
    crash — just log and skip."""
    bus = SignalBus()
    sub = ReplaySubscriber(bus=bus)
    # Calling on_event directly (no asyncio.run wrapper) means
    # there's no running loop. Should not raise.
    sub.on_event(SignalEvent(
        event_type=EVENT_DEPLOY_FIRED,
        timestamp=datetime.now(timezone.utc),
        payload={"target_url": "http://x", "image_digest": None, "trigger": "test"},
    ))
    # No tasks should be scheduled.
    assert sub.replays_in_flight == []
