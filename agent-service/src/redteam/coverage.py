"""Coverage state — what's been attacked, how, with what verdict.

The Orchestrator Agent (orchestrator.py) reads this to decide the
next campaign. Per ARCHITECTURE.md §"Orchestration strategy", four
signals drive prioritization:

1. **Coverage state** — attempts per category, verdict distribution
2. **Open findings** — successes/partials not yet promoted to regression
3. **Cost-to-date** — current campaign spend vs. ceiling
4. **Recent verdict trend** — partial-rate per category (a category
   with growing partial frequency is closer to a bypass than one
   with all fails)

This module is the data layer. Read-only against the on-disk JSON
output of ``run_campaign.py``. No LLM calls — pure aggregation so
the Orchestrator's strategy can be debugged without burning tokens.

For Friday-final scale, this is a candidate to back with SQLite +
SQLAlchemy. For now JSON-file scanning is sufficient (~10ms even
for hundreds of runs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from redteam.messages import ThreatCategory


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CategoryStats:
    """Per-category aggregate over a time window."""

    category: ThreatCategory
    attempts: int = 0
    success: int = 0
    partial: int = 0
    fail: int = 0
    judge_failed: int = 0
    last_seen_at: datetime | None = None
    # Attempts whose verdict was 'partial' — candidates for mutate-mode
    # follow-up. Each entry is the attempt's UUID + the path to its
    # campaign JSON file (so the Orchestrator can hand the parent to
    # the Red Team Agent).
    partial_attempt_refs: list[tuple[str, Path]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.attempts == 0:
            return 0.0
        return self.success / self.attempts

    @property
    def partial_rate(self) -> float:
        if self.attempts == 0:
            return 0.0
        return self.partial / self.attempts


@dataclass(slots=True)
class CoverageReport:
    """Full coverage state across all known categories. The
    Orchestrator's input."""

    per_category: dict[ThreatCategory, CategoryStats]
    total_attempts: int
    total_campaigns: int
    open_findings_count: int          # success + partial verdicts not yet
                                      # promoted to a vuln report (MVP: any
                                      # success or partial)
    runs_scanned: int                 # how many campaign JSON files
    window_start: datetime
    window_end: datetime

    def category_summary_text(self) -> str:
        """Human-readable + LLM-prompt-friendly summary."""
        lines: list[str] = []
        all_cats = list(ThreatCategory)
        for cat in all_cats:
            stats = self.per_category.get(cat)
            if stats is None or stats.attempts == 0:
                lines.append(f"  - {cat.value:25s} UNEXPLORED")
                continue
            lines.append(
                f"  - {cat.value:25s} attempts={stats.attempts:3d}  "
                f"success={stats.success}  partial={stats.partial}  "
                f"fail={stats.fail}  partial_rate={stats.partial_rate:.2f}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scanning logic
# ---------------------------------------------------------------------------


def _iter_campaign_files(runs_dir: Path) -> Iterator[Path]:
    """Yield every campaign_*.json under runs_dir, recursively."""
    if not runs_dir.exists():
        return
    yield from runs_dir.glob("**/campaign_*.json")


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def build_coverage_report(
    runs_dir: Path,
    *,
    window: timedelta | None = timedelta(hours=24),
    now: datetime | None = None,
) -> CoverageReport:
    """Scan all campaign JSON files under ``runs_dir`` and compute
    aggregate coverage state over the given time window.

    Args:
        runs_dir: typically ``agent-service/evals/redteam_runs/``.
        window: only count attempts whose ``timestamp`` is within
                this rolling window. Defaults to 24h. Pass ``None``
                to ignore the time filter (count everything).
        now: override "now" for testing. Defaults to UTC now.
    """
    now = now or datetime.now(timezone.utc)
    window_start = (now - window) if window else datetime.min.replace(tzinfo=timezone.utc)

    per_category: dict[ThreatCategory, CategoryStats] = {}
    total_attempts = 0
    total_campaigns = 0
    open_findings = 0
    runs_scanned = 0

    for path in _iter_campaign_files(runs_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        runs_scanned += 1
        campaign = data.get("campaign", {})
        try:
            cat = ThreatCategory(campaign.get("category"))
        except ValueError:
            continue

        total_campaigns += 1
        stats = per_category.setdefault(cat, CategoryStats(category=cat))

        for item in data.get("attempts", []):
            attempt = item.get("attempt", {})
            verdict = item.get("verdict")
            ts = _parse_iso(attempt.get("timestamp", ""))
            if ts is None:
                continue
            if window is not None and ts < window_start:
                continue

            total_attempts += 1
            stats.attempts += 1
            if stats.last_seen_at is None or ts > stats.last_seen_at:
                stats.last_seen_at = ts

            if verdict is None:
                continue

            v = verdict.get("verdict")
            if v == "success":
                stats.success += 1
                open_findings += 1
            elif v == "partial":
                stats.partial += 1
                open_findings += 1
                stats.partial_attempt_refs.append((attempt["attempt_id"], path))
            elif v == "fail":
                stats.fail += 1
            elif v == "judge_failed":
                stats.judge_failed += 1

    return CoverageReport(
        per_category=per_category,
        total_attempts=total_attempts,
        total_campaigns=total_campaigns,
        open_findings_count=open_findings,
        runs_scanned=runs_scanned,
        window_start=window_start,
        window_end=now,
    )
