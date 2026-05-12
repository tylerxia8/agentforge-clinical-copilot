"""Coverage state aggregator tests.

The Orchestrator's decision quality depends on the coverage
aggregator reading prior runs correctly. These tests pin the
shape and the time-window filtering.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

from redteam.coverage import build_coverage_report  # noqa: E402
from redteam.messages import ThreatCategory  # noqa: E402


def _write_campaign_run(
    runs_dir: Path,
    campaign_id: str,
    category: str,
    attempts_with_verdicts: list[tuple[str, str, str]],  # (attempt_id, verdict, timestamp_iso)
) -> Path:
    """Synthesize a campaign-run JSON in the shape the loader expects."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"campaign_{category}_{campaign_id}.json"
    path.write_text(json.dumps({
        "campaign": {
            "campaign_id": campaign_id,
            "category": category,
            "hop_budget": 5,
            "cost_budget_usd": 1.0,
            "rationale": "test fixture",
        },
        "attempts": [
            {
                "attempt": {
                    "attempt_id": attempt_id,
                    "campaign_id": campaign_id,
                    "category": category,
                    "messages": [],
                    "target_responses": [],
                    "target_status_codes": [200],
                    "target_sources": [],
                    "timestamp": ts,
                },
                "verdict": {
                    "attempt_id": attempt_id,
                    "verdict": verdict,
                    "reasoning": "fixture",
                    "judge_confidence": 0.9,
                    "deterministic_signals": {},
                },
            }
            for attempt_id, verdict, ts in attempts_with_verdicts
        ],
    }))
    return path


def test_empty_runs_dir_returns_empty_report(tmp_path: Path) -> None:
    r = build_coverage_report(tmp_path)
    assert r.total_attempts == 0
    assert r.total_campaigns == 0
    assert r.open_findings_count == 0
    assert r.per_category == {}


def test_aggregates_verdicts_by_category(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    ts_recent = (now - timedelta(hours=1)).isoformat()

    _write_campaign_run(
        tmp_path / "20260512T000000Z",
        str(uuid4()),
        "cross_patient",
        [
            ("a1", "fail", ts_recent),
            ("a2", "fail", ts_recent),
            ("a3", "success", ts_recent),
        ],
    )
    _write_campaign_run(
        tmp_path / "20260512T000100Z",
        str(uuid4()),
        "indirect_injection",
        [
            ("b1", "partial", ts_recent),
            ("b2", "fail", ts_recent),
        ],
    )

    r = build_coverage_report(tmp_path)
    assert r.total_campaigns == 2
    assert r.total_attempts == 5
    # Successes + partials count as open findings (until promoted to
    # a regression case).
    assert r.open_findings_count == 2

    cp = r.per_category[ThreatCategory.CROSS_PATIENT]
    assert cp.attempts == 3
    assert cp.success == 1
    assert cp.fail == 2
    assert cp.partial_rate == 0.0

    ii = r.per_category[ThreatCategory.INDIRECT_INJECTION]
    assert ii.attempts == 2
    assert ii.partial == 1
    assert ii.partial_rate == 0.5


def test_filters_out_attempts_older_than_window(tmp_path: Path) -> None:
    """Time-window filtering is what makes the Orchestrator's
    coverage signal useful — a category attacked once a month
    ago is still 'under-explored' from the recent-window view."""
    now = datetime.now(timezone.utc)
    ts_recent = (now - timedelta(hours=1)).isoformat()
    ts_old = (now - timedelta(days=7)).isoformat()

    _write_campaign_run(
        tmp_path / "20260512T000000Z",
        str(uuid4()),
        "state_corruption",
        [
            ("recent_1", "fail", ts_recent),
            ("old_1", "fail", ts_old),
        ],
    )

    # Default 24h window — the old attempt should not count
    r = build_coverage_report(tmp_path, window=timedelta(hours=24))
    sc = r.per_category[ThreatCategory.STATE_CORRUPTION]
    assert sc.attempts == 1  # only the recent one

    # No window — both attempts count
    r2 = build_coverage_report(tmp_path, window=None)
    assert r2.per_category[ThreatCategory.STATE_CORRUPTION].attempts == 2


def test_handles_malformed_campaign_file_without_crashing(tmp_path: Path) -> None:
    """One bad campaign file should not poison the whole scan."""
    (tmp_path / "good").mkdir()
    _write_campaign_run(
        tmp_path / "good",
        str(uuid4()),
        "cross_patient",
        [("a1", "fail", datetime.now(timezone.utc).isoformat())],
    )
    (tmp_path / "campaign_cross_patient_bad.json").write_text("{ malformed json")

    r = build_coverage_report(tmp_path)
    assert r.total_campaigns == 1
    assert r.per_category[ThreatCategory.CROSS_PATIENT].attempts == 1


def test_unknown_category_in_json_skipped_silently(tmp_path: Path) -> None:
    """If a sidecar JSON references a category not in ThreatCategory,
    the loader skips it rather than crashing. Useful for
    forward-compat when new categories are wired into the platform."""
    _write_campaign_run(
        tmp_path / "20260512T000000Z",
        str(uuid4()),
        "future_category_not_in_enum",  # ← doesn't exist
        [("a1", "fail", datetime.now(timezone.utc).isoformat())],
    )
    r = build_coverage_report(tmp_path)
    assert r.total_campaigns == 0
    assert r.total_attempts == 0


def test_tracks_partial_attempt_refs_for_mutate_mode(tmp_path: Path) -> None:
    """The Orchestrator's mutate mode picks parent attempts from
    the per-category partial_attempt_refs list. The aggregator
    must collect these as (attempt_id, source_path) tuples."""
    now = datetime.now(timezone.utc).isoformat()
    path = _write_campaign_run(
        tmp_path / "20260512T000000Z",
        str(uuid4()),
        "indirect_injection",
        [
            ("partial_one", "partial", now),
            ("fail_one", "fail", now),
            ("partial_two", "partial", now),
        ],
    )
    r = build_coverage_report(tmp_path)
    ii = r.per_category[ThreatCategory.INDIRECT_INJECTION]
    refs = ii.partial_attempt_refs
    assert len(refs) == 2
    ids = {ref[0] for ref in refs}
    assert ids == {"partial_one", "partial_two"}
    assert all(ref[1] == path for ref in refs)
