"""Auto-promote-on-green subscriber — W4 v4.

Closes the fourth W3-final-grader bullet ("regression handling").
The ReplaySubscriber from v3 runs the regression suite when
``deploy.fired`` arrives but doesn't act on the results — the
operator still has to manually move green-replay findings from
``_pending/`` to live. This subscriber does that move
autonomously, with two safety properties:

1. **Green-only.** Promotion only fires when the replay run's
   ``replay.completed`` event reports ``failed_count == 0`` AND
   ``error_count == 0``. One bad case anywhere in the run
   suppresses every promotion — the operator's manual review
   becomes the failure mode rather than silent half-promotion.
2. **Critical stays pending.** Findings with
   ``severity == "critical"`` in their sidecar JSON are never
   auto-promoted, regardless of replay outcome. This preserves
   the trust gate's design intent: critical findings require
   human review before they enter the live eval-gate.

The subscriber accumulates ``replay.case.evaluated`` events
keyed by ``replay_id`` and dispatches on the matching
``replay.completed`` event. Per-replay state is bounded — once
a replay completes (or the subscriber is destroyed) the buffer
is dropped.

What this does NOT ship (W4 v5+ candidates):

- **Reversal on later failure.** Today a promoted finding stays
  live forever. If a future deploy regresses and the case fails,
  we emit a per-case-failed event but don't auto-demote. A
  symmetric demoter would close the loop.
- **Persistent dedup.** Re-running the same replay would
  re-process the same cases. The promotion is idempotent at the
  filesystem level (move-or-skip-if-already-live) but emits the
  ``finding.promoted`` event each time. For low-frequency
  operator-triggered runs this is fine; a high-frequency
  webhook-driven future would want a ``replay_id`` dedup table.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from redteam.signals import (
    EVENT_REPLAY_CASE_EVALUATED,
    EVENT_REPLAY_COMPLETED,
    SignalBus,
    SignalEvent,
    emit_finding_promoted,
    emit_finding_promotion_skipped,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------


def _agent_service_root() -> Path:
    # ``agent-service/src/redteam/auto_promote.py`` → ``agent-service/``
    return Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    return _agent_service_root().parent


def _default_findings_dir() -> Path:
    return _agent_service_root() / "evals" / "w2" / "adversarial_findings"


def _default_vulns_dir() -> Path:
    """Where vuln markdown files live. Prefer the deploy-mirror
    ``agent-service/vulns/`` if it exists (matches the dashboard's
    resolution), otherwise fall back to the repo-root ``vulns/``."""
    mirror = _agent_service_root() / "vulns"
    if mirror.exists():
        return mirror
    return _repo_root() / "vulns"


# ---------------------------------------------------------------------------
# Severity lookup
# ---------------------------------------------------------------------------


def _read_severity(findings_dir: Path, vuln_id: str) -> str | None:
    """Read the severity from the sidecar JSON. Tries the
    ``_pending/`` dir first (where unpromoted findings live), then
    the live dir as a fallback (idempotency: re-promoting a
    live-already finding shouldn't crash on missing sidecar)."""
    for path in (
        findings_dir / "_pending" / f"{vuln_id}.json",
        findings_dir / f"{vuln_id}.json",
    ):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("severity")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("auto_promote: failed to read %s: %s", path, e)
    return None


# ---------------------------------------------------------------------------
# The actual move
# ---------------------------------------------------------------------------


def _promote_one_finding(
    vuln_id: str,
    *,
    findings_dir: Path,
    vulns_dir: Path,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Move the sidecar JSON and the markdown report from
    ``_pending/`` to the live dir. Returns
    ``(sidecar_from, sidecar_to, markdown_from, markdown_to)``
    with ``None`` for any artifact that didn't exist (and so
    wasn't moved). At least one of the four return slots is non-
    None on a successful promotion.
    """
    sidecar_from: str | None = None
    sidecar_to: str | None = None
    markdown_from: str | None = None
    markdown_to: str | None = None

    sidecar_pending = findings_dir / "_pending" / f"{vuln_id}.json"
    sidecar_live = findings_dir / f"{vuln_id}.json"
    if sidecar_pending.exists() and not sidecar_live.exists():
        sidecar_live.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sidecar_pending), str(sidecar_live))
        sidecar_from = str(sidecar_pending)
        sidecar_to = str(sidecar_live)

    md_pending = vulns_dir / "_pending" / f"{vuln_id}.md"
    md_live = vulns_dir / f"{vuln_id}.md"
    if md_pending.exists() and not md_live.exists():
        md_live.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(md_pending), str(md_live))
        markdown_from = str(md_pending)
        markdown_to = str(md_live)

    return sidecar_from, sidecar_to, markdown_from, markdown_to


# ---------------------------------------------------------------------------
# AutoPromoteSubscriber
# ---------------------------------------------------------------------------


class AutoPromoteSubscriber:
    """Subscribes to replay events. On a green ``replay.completed``,
    promotes each passed-pending case from ``_pending/`` to live.

    The subscriber is opt-in: the admin endpoint constructs it
    with ``enabled=True`` only when the operator explicitly
    requests promotion (``auto_promote: true`` in the request
    body). When ``enabled=False`` the subscriber still observes
    events and emits ``finding.promotion_skipped`` so the
    dashboard can show what *would have been promoted*, but
    doesn't touch the filesystem.
    """

    def __init__(
        self,
        *,
        bus: SignalBus,
        enabled: bool = False,
        findings_dir: Path | None = None,
        vulns_dir: Path | None = None,
        critical_stays_pending: bool = True,
    ) -> None:
        self._bus = bus
        self._enabled = enabled
        self._findings_dir = findings_dir or _default_findings_dir()
        self._vulns_dir = vulns_dir or _default_vulns_dir()
        self._critical_stays_pending = critical_stays_pending
        self._cases_by_replay: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def on_event(self, event: SignalEvent) -> None:
        if event.event_type == EVENT_REPLAY_CASE_EVALUATED:
            replay_id = (event.payload or {}).get("replay_id")
            if replay_id:
                self._cases_by_replay[replay_id].append(event.payload)
            return
        if event.event_type == EVENT_REPLAY_COMPLETED:
            self._handle_replay_completed(event)
            return

    # -----------------------------------------------------------------
    # Replay-completed dispatch
    # -----------------------------------------------------------------

    def _handle_replay_completed(self, event: SignalEvent) -> None:
        payload = event.payload or {}
        replay_id = payload.get("replay_id")
        if not replay_id:
            return
        cases = self._cases_by_replay.pop(replay_id, [])

        if int(payload.get("failed_count", 0)) > 0 or int(payload.get("error_count", 0)) > 0:
            # Not green; nothing to promote. Emit one skipped event
            # per pending case so the dashboard shows what was
            # considered but bypassed.
            for case in cases:
                if not case.get("is_pending"):
                    continue
                vuln_id = case.get("vuln_id")
                if not vuln_id:
                    continue
                severity = _read_severity(self._findings_dir, vuln_id)
                emit_finding_promotion_skipped(
                    self._bus,
                    vuln_id=vuln_id,
                    severity=severity,
                    replay_id=replay_id,
                    reason="replay had failures or errors; no promotions on non-green runs",
                )
            return

        # Green run — consider each case for promotion.
        for case in cases:
            self._maybe_promote(case, replay_id=replay_id)

    def _maybe_promote(self, case: dict[str, Any], *, replay_id: str) -> None:
        if not case.get("passed"):
            return  # failed cases never promote
        if not case.get("is_pending"):
            return  # already-live cases don't need promotion
        vuln_id = case.get("vuln_id")
        if not vuln_id:
            return

        severity = _read_severity(self._findings_dir, vuln_id)

        if self._critical_stays_pending and severity == "critical":
            emit_finding_promotion_skipped(
                self._bus,
                vuln_id=vuln_id,
                severity=severity,
                replay_id=replay_id,
                reason="critical-severity findings stay pending (trust gate preserved)",
            )
            return

        if not self._enabled:
            emit_finding_promotion_skipped(
                self._bus,
                vuln_id=vuln_id,
                severity=severity,
                replay_id=replay_id,
                reason="auto_promote disabled; would have promoted on green replay",
            )
            return

        try:
            (sidecar_from, sidecar_to,
             markdown_from, markdown_to) = _promote_one_finding(
                vuln_id,
                findings_dir=self._findings_dir,
                vulns_dir=self._vulns_dir,
            )
        except OSError as e:
            emit_finding_promotion_skipped(
                self._bus,
                vuln_id=vuln_id,
                severity=severity,
                replay_id=replay_id,
                reason=f"filesystem error during promotion: {e}",
            )
            return

        if all(x is None for x in (sidecar_from, sidecar_to, markdown_from, markdown_to)):
            emit_finding_promotion_skipped(
                self._bus,
                vuln_id=vuln_id,
                severity=severity,
                replay_id=replay_id,
                reason="no _pending/ artifacts found (already-promoted or missing)",
            )
            return

        emit_finding_promoted(
            self._bus,
            vuln_id=vuln_id,
            severity=severity,
            replay_id=replay_id,
            sidecar_moved_from=sidecar_from,
            sidecar_moved_to=sidecar_to,
            markdown_moved_from=markdown_from,
            markdown_moved_to=markdown_to,
        )
