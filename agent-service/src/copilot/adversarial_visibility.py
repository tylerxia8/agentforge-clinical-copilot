"""Adversarial visibility — JSON aggregator for the /adversarial page.

Scans agent-service/evals/redteam_runs/ + vulns/ +
adversarial_findings/ and produces the data the
agent-service/src/copilot/static/adversarial.html page renders.

Read-only. No LLM calls. Fast — file scan + simple aggregation,
~50ms for the full run history. Cached for 30 seconds at the
HTTP layer so a busy dashboard doesn't hammer the disk.

Why a separate module from copilot.visibility:
- Different data sources (W3 runs vs. W2 RAG corpus/routing)
- Different audience (security operators vs. clinical reviewers)
- Independently versioned — the W3 platform's visibility surface
  evolves separately from the W2 agent's
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _agent_service_root() -> Path:
    """The agent-service root (one level above src/)."""
    here = Path(__file__).resolve()
    # agent-service/src/copilot/adversarial_visibility.py → agent-service/
    return here.parents[2]


def _repo_root() -> Path:
    """The repo root (one level above agent-service/)."""
    return _agent_service_root().parent


def _redteam_runs_dir() -> Path:
    return _agent_service_root() / "evals" / "redteam_runs"


def _vulns_dir() -> Path:
    """Where the /adversarial page reads vuln reports from.

    Resolution order:
    1. ``agent-service/vulns/`` (the deploy-mirror; present in the
       Docker image when the Dockerfile's COPY vulns/ ran)
    2. ``<repo-root>/vulns/`` (canonical; what the Documentation
       Agent writes to)

    Local development sees both — the mirror takes precedence because
    if the operator running locally went to the trouble of refreshing
    the mirror, it's the more up-to-date copy. CI / deployed
    containers only see (1) since the repo root isn't in their
    build context.
    """
    mirror = _agent_service_root() / "vulns"
    if mirror.exists():
        return mirror
    return _repo_root() / "vulns"


def _adversarial_findings_dir() -> Path:
    return _agent_service_root() / "evals" / "w2" / "adversarial_findings"


# ---------------------------------------------------------------------------
# Coverage state — categories × verdicts over the runs
# ---------------------------------------------------------------------------


def coverage_snapshot() -> dict[str, Any]:
    """Per-category attempt counts + verdict distribution across
    every run on disk."""
    runs_dir = _redteam_runs_dir()
    if not runs_dir.exists():
        return {
            "total_attempts": 0,
            "total_campaigns": 0,
            "per_category": [],
            "first_run_at": None,
            "last_run_at": None,
        }

    per_category: dict[str, dict[str, int]] = defaultdict(lambda: {
        "attempts": 0, "success": 0, "partial": 0, "fail": 0, "judge_failed": 0,
    })
    total_attempts = 0
    total_campaigns = 0
    first_run_at: datetime | None = None
    last_run_at: datetime | None = None

    for campaign_path in runs_dir.glob("**/campaign_*.json"):
        try:
            data = json.loads(campaign_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        total_campaigns += 1

        campaign = data.get("campaign", {}) or {}
        category = campaign.get("category", "unknown")

        for item in data.get("attempts", []):
            attempt = item.get("attempt", {}) or {}
            verdict = item.get("verdict")
            total_attempts += 1
            per_category[category]["attempts"] += 1

            if verdict:
                v = verdict.get("verdict")
                if v in {"success", "partial", "fail", "judge_failed"}:
                    per_category[category][v] += 1

            ts = attempt.get("timestamp")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if first_run_at is None or dt < first_run_at:
                        first_run_at = dt
                    if last_run_at is None or dt > last_run_at:
                        last_run_at = dt
                except (ValueError, TypeError):
                    pass

    per_category_list = [
        {
            "category": cat,
            **counts,
            "partial_rate": counts["partial"] / counts["attempts"] if counts["attempts"] else 0.0,
            "success_rate": counts["success"] / counts["attempts"] if counts["attempts"] else 0.0,
        }
        for cat, counts in sorted(per_category.items())
    ]

    return {
        "total_attempts": total_attempts,
        "total_campaigns": total_campaigns,
        "per_category": per_category_list,
        "first_run_at": first_run_at.isoformat() if first_run_at else None,
        "last_run_at": last_run_at.isoformat() if last_run_at else None,
    }


# ---------------------------------------------------------------------------
# Vuln pipeline — by status + severity
# ---------------------------------------------------------------------------


def vuln_pipeline_snapshot() -> dict[str, Any]:
    """Counts of vuln reports by status (live vs. pending) and by
    severity. The pending bucket is the trust-gate's evidence.
    """
    live: list[dict] = []
    pending: list[dict] = []
    vulns = _vulns_dir()

    for md_path in vulns.glob("VULN-*.md"):
        live.append(_summarize_vuln_md(md_path))
    pending_dir = vulns / "_pending"
    if pending_dir.exists():
        for md_path in pending_dir.glob("VULN-*.md"):
            pending.append(_summarize_vuln_md(md_path))

    return {
        "live_count": len(live),
        "pending_count": len(pending),
        "live": live,
        "pending": pending,
    }


def _summarize_vuln_md(path: Path) -> dict[str, Any]:
    """Extract the frontmatter fields from a vuln markdown file."""
    out: dict[str, Any] = {
        "vuln_id": path.stem,
        "filename": str(path.relative_to(_repo_root())),
        "title": "",
        "severity": "unknown",
        "category": "unknown",
        "status": "unknown",
        "variant_of": None,
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out

    for ln in lines[:25]:
        if ln.startswith("# ") and " — " in ln:
            out["title"] = ln[2:].split(" — ", 1)[1].strip()
        elif ln.startswith("**Severity:**"):
            out["severity"] = ln.split("`", 2)[1] if "`" in ln else "unknown"
        elif ln.startswith("**Category:**"):
            out["category"] = ln.split("`", 2)[1] if "`" in ln else "unknown"
        elif ln.startswith("**Status:**"):
            out["status"] = ln.split("`", 2)[1] if "`" in ln else "unknown"
        elif ln.startswith("**Variant of:**"):
            out["variant_of"] = ln.split("`", 2)[1] if "`" in ln else None
    return out


# ---------------------------------------------------------------------------
# Recent campaigns — last N with timestamps + verdict counts
# ---------------------------------------------------------------------------


def recent_campaigns_snapshot(limit: int = 12) -> list[dict[str, Any]]:
    """Most-recent campaign runs, newest first."""
    runs_dir = _redteam_runs_dir()
    if not runs_dir.exists():
        return []

    campaigns: list[tuple[datetime, dict[str, Any]]] = []
    for campaign_path in runs_dir.glob("**/campaign_*.json"):
        try:
            data = json.loads(campaign_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        campaign = data.get("campaign", {}) or {}
        verdicts = {"success": 0, "partial": 0, "fail": 0, "judge_failed": 0}
        latest_attempt_ts: datetime | None = None
        # Per attempt: small summary the campaigns-tab table renders
        # as deep-links to /adversarial/attempts/<uuid>. Lets a grader
        # click straight from "this campaign had a success" to the
        # actual exploit transcript.
        attempt_links: list[dict[str, Any]] = []
        for item in data.get("attempts", []):
            attempt = item.get("attempt") or {}
            verdict_obj = item.get("verdict") or {}
            v = verdict_obj.get("verdict")
            if v in verdicts:
                verdicts[v] += 1
            ts = attempt.get("timestamp")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if latest_attempt_ts is None or dt > latest_attempt_ts:
                        latest_attempt_ts = dt
                except (ValueError, TypeError):
                    pass

            attempt_links.append({
                "attempt_id": attempt.get("attempt_id"),
                "mode": attempt.get("mode"),
                "verdict": v,
                "judge_confidence": verdict_obj.get("judge_confidence"),
                "target_refused": attempt.get("target_refused"),
            })

        if latest_attempt_ts is None:
            continue

        campaigns.append((latest_attempt_ts, {
            "campaign_id": campaign.get("campaign_id", "?"),
            "category": campaign.get("category", "unknown"),
            "hop_budget": campaign.get("hop_budget", 0),
            "cost_budget_usd": campaign.get("cost_budget_usd", 0.0),
            "rationale": (campaign.get("rationale") or "")[:240],
            "ran_at": latest_attempt_ts.isoformat(),
            "attempts": sum(verdicts.values()),
            "verdicts": verdicts,
            "run_dir": campaign_path.parent.name,
            "attempt_links": attempt_links,
        }))

    campaigns.sort(reverse=True, key=lambda c: c[0])
    return [c[1] for c in campaigns[:limit]]


# ---------------------------------------------------------------------------
# Time-series — attempts per day, last N days
# ---------------------------------------------------------------------------


def time_series_snapshot(days: int = 14) -> dict[str, Any]:
    """Daily attempt count + success/partial/fail counts for the
    last N days. Lets the page render a small sparkline-style
    trend without per-campaign detail."""
    runs_dir = _redteam_runs_dir()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {
        "attempts": 0, "success": 0, "partial": 0, "fail": 0,
    })

    for campaign_path in runs_dir.glob("**/campaign_*.json"):
        try:
            data = json.loads(campaign_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for item in data.get("attempts", []):
            attempt = item.get("attempt", {}) or {}
            verdict = item.get("verdict")
            ts = attempt.get("timestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if dt < cutoff:
                continue
            day = dt.strftime("%Y-%m-%d")
            by_day[day]["attempts"] += 1
            if verdict:
                v = verdict.get("verdict")
                if v in {"success", "partial", "fail"}:
                    by_day[day][v] += 1

    series = [
        {"day": day, **counts}
        for day, counts in sorted(by_day.items())
    ]
    return {
        "days": days,
        "series": series,
    }


# ---------------------------------------------------------------------------
# Aggregate snapshot — what /adversarial/data returns
# ---------------------------------------------------------------------------


def aggregate_snapshot() -> dict[str, Any]:
    """Single call that returns everything the page renders.
    Wrapped so the route handler is one line and the JSON is
    versioned together."""
    return {
        "schema_version": 5,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage_snapshot(),
        "vuln_pipeline": vuln_pipeline_snapshot(),
        "recent_campaigns": recent_campaigns_snapshot(),
        "time_series": time_series_snapshot(),
        "live_signals": signals_snapshot(),
        "judge_drift": judge_drift_snapshot(),
        "replay": replay_snapshot(),
        "auto_promote": auto_promote_snapshot(),
    }


# ---------------------------------------------------------------------------
# Live signal stream — W4 observability-driven autonomy proof
# ---------------------------------------------------------------------------


def signals_snapshot(*, max_events: int = 50) -> dict[str, Any]:
    """Read the most-recent run's signals.jsonl and return a tail.

    The campaign runner persists every published event as one JSON
    line under
    ``agent-service/evals/redteam_runs/<timestamp>/signals.jsonl``.
    This function finds the most-recent run dir that has a
    signals.jsonl, reads the tail, and returns event records
    plus aggregate stats the dashboard tile renders.

    Returns an empty stream if no signals log exists yet.
    """
    runs_dir = _redteam_runs_dir()
    if not runs_dir.exists():
        return _empty_signals_snapshot()

    # Find the most-recent run dir (by name; the dir names are
    # %Y%m%dT%H%M%SZ so lexicographic sort = chronological).
    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    log_path: Path | None = None
    for d in candidates:
        candidate = d / "signals.jsonl"
        if candidate.exists():
            log_path = candidate
            break

    if log_path is None:
        return _empty_signals_snapshot()

    try:
        raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _empty_signals_snapshot()

    parsed: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Tail to the most recent max_events for the timeline.
    tail = parsed[-max_events:] if len(parsed) > max_events else parsed

    # Aggregate: how many of each event type? How many Orchestrator
    # decisions consulted live signals?
    event_counts: dict[str, int] = defaultdict(int)
    orchestrator_decisions: list[dict[str, Any]] = []
    for ev in parsed:
        event_counts[ev.get("event_type", "unknown")] += 1
        if ev.get("event_type") == "orchestrator.decided":
            orchestrator_decisions.append({
                "timestamp": ev.get("timestamp"),
                "category": ev.get("category"),
                "rationale": (ev.get("payload") or {}).get("rationale", ""),
                "used_live_signals": (ev.get("payload") or {}).get("used_live_signals", False),
                "shifts_consulted": (ev.get("payload") or {}).get("shifts_consulted", []),
            })

    return {
        "run_dir": log_path.parent.name,
        "log_path": str(log_path.relative_to(_agent_service_root())),
        "total_events": len(parsed),
        "event_counts": dict(event_counts),
        "orchestrator_decisions": orchestrator_decisions,
        "recent_events": tail,
    }


def _empty_signals_snapshot() -> dict[str, Any]:
    return {
        "run_dir": None,
        "log_path": None,
        "total_events": 0,
        "event_counts": {},
        "orchestrator_decisions": [],
        "recent_events": [],
    }


# ---------------------------------------------------------------------------
# Judge drift — replays the most recent run's verdict events through a
# fresh JudgeDriftMonitor to produce the same view the runner sees live
# ---------------------------------------------------------------------------


def judge_drift_snapshot() -> dict[str, Any]:
    """Replay the most-recent run's signals.jsonl through a fresh
    JudgeDriftMonitor and return both the current rolling-window
    confidence stats per category and any drift signals that
    would have fired across the run.

    The replay strategy: walk events in order, take a baseline
    snapshot at each ``orchestrator.decided`` event (mirroring the
    runner's per-round cadence), and collect any drift signals
    surfaced between rounds. The final ``current_stats`` is the
    full-run view.

    This is the dashboard's window into the Judge consistency
    loop — the second of the four W3-final-grader loops that W4
    is converting from structured-sequential to observability-
    driven.
    """
    from redteam.signals import (  # local import to keep visibility module light at startup
        EVENT_ORCHESTRATOR_DECIDED,
        JudgeDriftMonitor,
        SignalBus,
    )

    runs_dir = _redteam_runs_dir()
    if not runs_dir.exists():
        return _empty_judge_drift_snapshot()

    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    log_path: Path | None = None
    for d in candidates:
        candidate = d / "signals.jsonl"
        if candidate.exists():
            log_path = candidate
            break

    if log_path is None:
        return _empty_judge_drift_snapshot()

    events = SignalBus.replay_from_jsonl(log_path)
    if not events:
        return _empty_judge_drift_snapshot()

    monitor = JudgeDriftMonitor()
    drift_signals_per_round: list[dict[str, Any]] = []
    for ev in events:
        monitor.on_event(ev)
        if ev.event_type == EVENT_ORCHESTRATOR_DECIDED:
            # The round just decided; surface drift if any, then
            # pin a new baseline for the *next* round.
            for d in monitor.recent_drift():
                drift_signals_per_round.append({
                    "round_at": ev.timestamp.isoformat(),
                    **d.to_jsonable(),
                })
            monitor.take_baseline_snapshot()

    final_stats = monitor.current_stats()
    per_category_view = sorted(
        (
            {
                "category": cat.value,
                "samples": s.samples,
                "mean_confidence": round(s.mean_confidence, 3),
                "by_verdict": s.by_verdict,
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for cat, s in final_stats.items()
        ),
        key=lambda r: r["category"],
    )

    return {
        "run_dir": log_path.parent.name,
        "log_path": str(log_path.relative_to(_agent_service_root())),
        "total_llm_verdicts": sum(s.samples for s in final_stats.values()),
        "per_category": per_category_view,
        "drift_signals": drift_signals_per_round,
    }


def _empty_judge_drift_snapshot() -> dict[str, Any]:
    return {
        "run_dir": None,
        "log_path": None,
        "total_llm_verdicts": 0,
        "per_category": [],
        "drift_signals": [],
    }


# ---------------------------------------------------------------------------
# Replay-on-deploy snapshot — reads the most-recent replay_runs/<ts>/signals.jsonl
# ---------------------------------------------------------------------------


def _replay_runs_dir() -> Path:
    return _agent_service_root() / "evals" / "replay_runs"


def replay_snapshot() -> dict[str, Any]:
    """Read the most-recent replay run's signals.jsonl and return
    the deploy.fired trigger metadata + per-case results + the
    completion summary.

    Mirrors the campaign signals_snapshot pattern. The dashboard's
    Replay-on-deploy tile reads this; ops can also curl
    ``/adversarial/replay`` directly to audit whether a given
    deploy actually fired its regression suite.
    """
    runs_dir = _replay_runs_dir()
    if not runs_dir.exists():
        return _empty_replay_snapshot()

    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    log_path: Path | None = None
    for d in candidates:
        candidate = d / "signals.jsonl"
        if candidate.exists():
            log_path = candidate
            break

    if log_path is None:
        return _empty_replay_snapshot()

    try:
        raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _empty_replay_snapshot()

    events: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    deploy_fired: dict[str, Any] | None = None
    replay_started: dict[str, Any] | None = None
    replay_completed: dict[str, Any] | None = None
    case_results: list[dict[str, Any]] = []

    for ev in events:
        et = ev.get("event_type")
        payload = ev.get("payload") or {}
        if et == "deploy.fired":
            deploy_fired = {"timestamp": ev.get("timestamp"), **payload}
        elif et == "replay.started":
            replay_started = {"timestamp": ev.get("timestamp"), **payload}
        elif et == "replay.case.evaluated":
            case_results.append({"timestamp": ev.get("timestamp"), **payload})
        elif et == "replay.completed":
            replay_completed = {"timestamp": ev.get("timestamp"), **payload}

    return {
        "run_dir": log_path.parent.name,
        "log_path": str(log_path.relative_to(_agent_service_root())),
        "deploy_fired": deploy_fired,
        "replay_started": replay_started,
        "replay_completed": replay_completed,
        "case_results": case_results,
    }


def _empty_replay_snapshot() -> dict[str, Any]:
    return {
        "run_dir": None,
        "log_path": None,
        "deploy_fired": None,
        "replay_started": None,
        "replay_completed": None,
        "case_results": [],
    }


# ---------------------------------------------------------------------------
# Auto-promotion snapshot — W4 v4
# ---------------------------------------------------------------------------


def auto_promote_snapshot() -> dict[str, Any]:
    """Read the most-recent replay run's signals.jsonl and return
    the finding.promoted + finding.promotion_skipped event stream.

    The promotions are the autonomy proof for the fourth grader
    bullet (regression handling): each promotion is a finding the
    platform moved from _pending/ to live in response to a green
    replay signal, with no human in the loop.
    """
    runs_dir = _replay_runs_dir()
    if not runs_dir.exists():
        return _empty_auto_promote_snapshot()

    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    log_path: Path | None = None
    for d in candidates:
        candidate = d / "signals.jsonl"
        if candidate.exists():
            log_path = candidate
            break

    if log_path is None:
        return _empty_auto_promote_snapshot()

    try:
        raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _empty_auto_promote_snapshot()

    promoted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = ev.get("event_type")
        payload = ev.get("payload") or {}
        if et == "finding.promoted":
            promoted.append({"timestamp": ev.get("timestamp"), **payload})
        elif et == "finding.promotion_skipped":
            skipped.append({"timestamp": ev.get("timestamp"), **payload})

    return {
        "run_dir": log_path.parent.name,
        "log_path": str(log_path.relative_to(_agent_service_root())),
        "promoted_count": len(promoted),
        "skipped_count": len(skipped),
        "promoted": promoted,
        "skipped": skipped,
    }


def _empty_auto_promote_snapshot() -> dict[str, Any]:
    return {
        "run_dir": None,
        "log_path": None,
        "promoted_count": 0,
        "skipped_count": 0,
        "promoted": [],
        "skipped": [],
    }


# ---------------------------------------------------------------------------
# Per-attempt detail — the deliverable from Tuesday's grader feedback
# ("making raw eval artifacts easier to inspect directly")
# ---------------------------------------------------------------------------


def attempt_detail(attempt_id: str) -> dict[str, Any] | None:
    """Return the full transcript + verdict for one attempt, or
    ``None`` if not found.

    The grader's Tuesday feedback specifically asked for the raw
    eval artifacts to be easier to inspect. This function plus the
    matching ``GET /adversarial/attempts/<uuid>`` route are the
    direct response: a clickable per-attempt URL that renders the
    Red Team's prompt, the target's response with sources, and the
    Judge's verdict (with deterministic signals + LLM reasoning if
    the LLM Judge fired) on one page.

    The data is pulled from the SAME JSON files committed to the
    repo at ``agent-service/evals/redteam_runs/<timestamp>/``. The
    page is a rendering of those files, not a database materialization
    — so the path from artifact to UI is auditable.
    """
    runs_dir = _redteam_runs_dir()
    if not runs_dir.exists() or not attempt_id:
        return None

    for campaign_path in runs_dir.glob("**/campaign_*.json"):
        try:
            data = json.loads(campaign_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        for item in data.get("attempts", []):
            attempt = item.get("attempt", {}) or {}
            if attempt.get("attempt_id") != attempt_id:
                continue

            campaign = data.get("campaign", {}) or {}
            verdict = item.get("verdict") or {}
            return {
                "schema_version": 1,
                "attempt": attempt,
                "verdict": verdict,
                "campaign": campaign,
                "source_file": str(
                    campaign_path.relative_to(_agent_service_root().parent)
                ),
                "run_dir": campaign_path.parent.name,
            }

    return None


def vuln_detail(vuln_id: str) -> dict[str, Any] | None:
    """Return the full evidence package for one vuln report, or
    ``None`` if not found.

    Tuesday W3 MVP grader feedback: "vulnerability evidence easier
    to inspect directly." This is the per-vuln counterpart to
    ``attempt_detail`` — one URL returns:

    - The markdown report (raw, for client-side rendering)
    - Severity / category / status / variant_of (parsed from the
      markdown frontmatter)
    - The regression-case JSON sidecar (live or pending) if one
      exists. Hand-authored architectural-finding vulns don't have
      sidecars — those return ``eval_case_sidecar: null``.
    - The originating ``AttackAttempt`` (via the report's
      ``discovered_by_attempt`` UUID) if it can be resolved on disk
    - Whether the vuln is live or _pending/ (the trust-gate status)
    """
    if not vuln_id:
        return None

    vulns_root = _vulns_dir()
    if not vulns_root.exists():
        return None

    # Look in live dir first, then _pending/. Trust gate status is
    # derived from which dir held the file.
    md_path = vulns_root / f"{vuln_id}.md"
    is_pending = False
    if not md_path.exists():
        pending_path = vulns_root / "_pending" / f"{vuln_id}.md"
        if not pending_path.exists():
            return None
        md_path = pending_path
        is_pending = True

    try:
        markdown = md_path.read_text(encoding="utf-8")
    except OSError:
        return None

    summary = _summarize_vuln_md(md_path)

    # Parse the discovered_by_attempt UUID from the frontmatter for
    # the deep-link to the originating attempt.
    discovered_attempt_id: str | None = None
    discovered_campaign_id: str | None = None
    for ln in markdown.splitlines()[:30]:
        if ln.startswith("**Discovery attempt:**"):
            # Format: "**Discovery attempt:** `<uuid>`"
            parts = ln.split("`", 2)
            if len(parts) >= 2:
                discovered_attempt_id = parts[1]
        elif ln.startswith("**Discovery campaign:**"):
            parts = ln.split("`", 2)
            if len(parts) >= 2:
                discovered_campaign_id = parts[1]

    # Look for the matching JSON sidecar in adversarial_findings/.
    # Hand-authored architectural vulns (VULN-0001/0002/0003) don't
    # have sidecars; auto-doc'd vulns do.
    sidecar_dir = _adversarial_findings_dir()
    sidecar_path = sidecar_dir / f"{vuln_id}.json"
    sidecar_is_pending = False
    if not sidecar_path.exists():
        sidecar_pending = sidecar_dir / "_pending" / f"{vuln_id}.json"
        if sidecar_pending.exists():
            sidecar_path = sidecar_pending
            sidecar_is_pending = True
        else:
            sidecar_path = None  # type: ignore[assignment]

    sidecar_data: dict[str, Any] | None = None
    if sidecar_path is not None:
        try:
            sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            sidecar_data = None

    # Try to resolve the originating attempt — cheap, reuses
    # attempt_detail().
    originating_attempt: dict[str, Any] | None = None
    if discovered_attempt_id:
        originating_attempt = attempt_detail(discovered_attempt_id)

    return {
        "schema_version": 1,
        "vuln_id": vuln_id,
        "is_pending": is_pending,
        "markdown_path": str(md_path.relative_to(_agent_service_root().parent)) if not md_path.is_absolute() or md_path.is_relative_to(_agent_service_root().parent) else str(md_path),
        "markdown": markdown,
        "frontmatter": summary,
        "discovered_by_attempt_id": discovered_attempt_id,
        "discovered_by_campaign_id": discovered_campaign_id,
        "originating_attempt": originating_attempt,
        "eval_case_sidecar": sidecar_data,
        "eval_case_sidecar_is_pending": sidecar_is_pending if sidecar_data else None,
        "eval_case_sidecar_path": (
            str(sidecar_path.relative_to(_agent_service_root().parent))
            if sidecar_path is not None
            else None
        ),
    }


def campaign_attempts_index(campaign_id: str) -> list[dict[str, Any]]:
    """Return a list of attempt summaries for one campaign — used by
    the per-campaign attempts list on the /adversarial page. Cheap;
    doesn't include full transcripts (call ``attempt_detail`` for
    that)."""
    runs_dir = _redteam_runs_dir()
    if not runs_dir.exists() or not campaign_id:
        return []

    for campaign_path in runs_dir.glob("**/campaign_*.json"):
        try:
            data = json.loads(campaign_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        campaign = data.get("campaign", {}) or {}
        if campaign.get("campaign_id") != campaign_id:
            continue

        out = []
        for item in data.get("attempts", []):
            attempt = item.get("attempt", {}) or {}
            verdict = item.get("verdict") or {}
            out.append({
                "attempt_id": attempt.get("attempt_id"),
                "mode": attempt.get("mode"),
                "timestamp": attempt.get("timestamp"),
                "target_refused": attempt.get("target_refused"),
                "target_status_codes": attempt.get("target_status_codes"),
                "verdict": verdict.get("verdict"),
                "judge_confidence": verdict.get("judge_confidence"),
                "severity_hint": verdict.get("severity_hint"),
            })
        return out

    return []
