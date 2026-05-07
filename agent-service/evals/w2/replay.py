"""Record/replay harness for the W2 eval suite.

Two modes:

- **Record** — wraps the case's ``fire()`` in :func:`record_case`,
  dumps the request shape + raw response into a JSONL file. Each
  line is one case execution.
- **Replay** — :func:`load_recorded_responses` reads the JSONL and
  returns a dict keyed by ``case_id``; :func:`replay_case` swaps the
  case's ``fire`` with a lookup so the eval runner produces the same
  rubric results without hitting the deployed agent (and without
  spending Anthropic tokens).

Why bother:

- **Iterating on rubrics is free.** Tweaking a substring expectation
  or adding a new rubric used to require re-running the full 63-case
  suite (~10 min, ~$2.50). With replay, rubric changes evaluate in
  <1s against the recorded responses.
- **Reproducibility.** A flake in Anthropic, OpenEMR, or our own
  warm() can shift a single case from PASS to FAIL. Recording the
  response on a known-good run lets the calibration baseline reflect
  reality, and lets any later regression re-grade the SAME bytes.
- **Cost trace for the demo.** ``record`` writes the per-call
  response time and (if the agent surfaces it) the cost — handy for
  the W2_COSTS.md updates.

Storage: JSONL, one file per recording session, default at
``agent-service/evals/w2/recordings/<timestamp>.jsonl``. Easy to
diff between recordings; trivial to commit one as a "reference"
recording for replay-based CI.

Stage 3 of the prod-evals cookbook (cohort 5 reference) maps to
this file. We deliberately keep the harness small — one
record/replay file pair, no fancy storage backend, no vendor SDK.
The eval gate cookbook calls out the same minimum-viable shape.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.w2.cases import W2Case


@dataclass
class RecordedResponse:
    """One recorded case execution. The response dict is whatever
    the case's ``fire()`` returned (chat body, extraction body,
    multi-turn transcript)."""

    case_id: str
    category: str
    description: str
    response: dict[str, Any]
    elapsed_seconds: float
    recorded_at: float = field(default_factory=time.time)
    # Optional: the case's request shape, for debugging mismatches
    # between the recording and the live agent later. Kept loose
    # because each fire path packs its inputs differently (chat vs
    # extract vs multiturn).
    request_summary: dict[str, Any] | None = None

    def to_json(self) -> str:
        # JSONL line — one record per line, no trailing newline (the
        # writer appends "\n").
        return json.dumps(
            {
                "case_id": self.case_id,
                "category": self.category,
                "description": self.description,
                "response": self.response,
                "elapsed_seconds": self.elapsed_seconds,
                "recorded_at": self.recorded_at,
                "request_summary": self.request_summary,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, line: str) -> "RecordedResponse":
        d = json.loads(line)
        return cls(
            case_id=d["case_id"],
            category=d["category"],
            description=d["description"],
            response=d["response"],
            elapsed_seconds=d["elapsed_seconds"],
            recorded_at=d.get("recorded_at", 0.0),
            request_summary=d.get("request_summary"),
        )


def record_case(case: W2Case, *, request_summary: dict[str, Any] | None = None) -> RecordedResponse:
    """Run ``case`` against the live agent and capture the response.

    Mirrors :func:`evals.w2.runner.run_case` so the recording path
    matches the production grading path exactly (same exception
    handling, same elapsed measurement). Doesn't run the rubrics —
    those happen later, either inline by the runner or after-the-
    fact via :func:`replay_case`."""
    started = time.time()
    try:
        response = case.fire()
    except Exception as exc:  # noqa: BLE001 — never crash the recorder
        response = {"_status": -1, "_error": f"{type(exc).__name__}: {exc}"}
    elapsed = time.time() - started
    return RecordedResponse(
        case_id=case.case_id,
        category=case.category,
        description=case.description,
        response=response,
        elapsed_seconds=round(elapsed, 2),
        request_summary=request_summary,
    )


def write_recordings(records: list[RecordedResponse], out_path: Path) -> None:
    """Append a recording session as a single JSONL file at
    ``out_path``. Caller decides the path; convention is
    ``recordings/<UTC ISO timestamp>.jsonl``."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(rec.to_json())
            fh.write("\n")


def load_recorded_responses(in_path: Path) -> dict[str, RecordedResponse]:
    """Load a JSONL recording into a ``{case_id: RecordedResponse}``
    dict for fast lookup by replay_case. Last-write-wins on duplicate
    case_ids (a rerun will overwrite the previous record)."""
    out: dict[str, RecordedResponse] = {}
    with in_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = RecordedResponse.from_json(line)
            out[rec.case_id] = rec
    return out


def replay_case(case: W2Case, recordings: dict[str, RecordedResponse]) -> tuple[dict[str, Any], float]:
    """Look up the case's recorded response. Returns
    ``(response, elapsed_seconds)``.

    Raises ``KeyError`` if the case has no recording — caller can
    decide whether to fall back to a live fire (degrade gracefully)
    or fail the run (strict mode)."""
    rec = recordings[case.case_id]
    return rec.response, rec.elapsed_seconds
