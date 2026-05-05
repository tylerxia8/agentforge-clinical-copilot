"""Pure routing logic for the supervisor.

No LangGraph imports. The supervisor node calls
:func:`route_decision` on the current state and writes the result
into ``state["next"]``; the graph's conditional edge reads that
field to dispatch.

Decision tree:

1. If ``hops`` ≥ ``HOP_LIMIT`` → ``answer``. Hard escape against
   routing loops; the architecture promises Langfuse alerts when
   this fires (see W2_ARCHITECTURE.md §2).
2. If an attachment is present and not yet extracted →
   ``intake_extractor``. The intake extractor pops the attachment
   off the state when done so the supervisor never re-routes to it.
3. If the user message contains an evidence-trigger token (any of
   :data:`EVIDENCE_TRIGGER_TOKENS`) AND no evidence has been
   retrieved yet this turn → ``evidence_retriever``.
4. Otherwise → ``answer``.

Heuristic, not LLM-driven. Two reasons:

- **Determinism for the eval gate.** Routing decisions are part of
  what the eval grades; an LLM-based supervisor would add a noisy
  per-turn variable. Heuristics let the rubric assert that a
  guideline-shaped question always routes through evidence
  retrieval before answering.
- **Latency.** A small LLM call per supervisor pass would cost ~1s
  per hop. The full graph runs the supervisor 2-3 times per turn,
  so heuristic routing saves 2-3s of wall time without sacrificing
  correctness on the cases the eval covers.

Adding an LLM-supervisor extension later is a one-function swap;
the graph builder takes a routing function as a parameter so a
custom supervisor can be plugged in.
"""

from __future__ import annotations

from typing import Literal

from copilot.workers.state import WorkerState

HOP_LIMIT = 5
"""Maximum supervisor passes per chat turn before forcing the
``answer`` node. Tuned to allow at most: extract → retrieve →
answer (3 hops in steady state) plus 2 slack hops for unusual
flows. Anything more is almost certainly a loop."""

EVIDENCE_TRIGGER_TOKENS: frozenset[str] = frozenset({
    # explicit mentions of guideline bodies
    "uspstf", "aafp", "ada", "acip", "cdc", "guideline", "guidelines",
    # screening / preventive vocabulary
    "screen", "screening", "screen?",
    # recommendation vocabulary
    "recommend", "recommended", "recommendation", "recommends",
    "should", "shouldn", "indicated", "contraindicated",
    # dosing / target vocabulary
    "target", "threshold", "goal", "intensity",
    # concrete preventive-care queries
    "vaccine", "vaccination", "immunization", "immunizations",
    "statin", "asa", "aspirin",
    # question of standard of care
    "standard",
})
"""User-message tokens that signal the question wants guideline
grounding rather than just chart data. Lower-cased; the matcher
splits on non-alphanum."""

RouteDecision = Literal["intake_extractor", "evidence_retriever", "answer"]


def route_decision(state: WorkerState) -> RouteDecision:
    """Decide the next worker for this state. Pure; no I/O."""

    if state.get("hops", 0) >= HOP_LIMIT:
        return "answer"

    if state.get("attachment") is not None:
        return "intake_extractor"

    if _wants_evidence(state.get("message", "")) and not state.get("evidence"):
        return "evidence_retriever"

    return "answer"


def _wants_evidence(message: str) -> bool:
    """True if the message contains any token that signals a
    guideline-grounded question."""
    if not message:
        return False
    tokens = _tokenize(message)
    return any(tok in EVIDENCE_TRIGGER_TOKENS for tok in tokens)


def _tokenize(text: str) -> list[str]:
    """Lowercase + split on whitespace + strip surrounding punct.
    Same idea as the matcher's normalization, lighter touch — we're
    only checking for vocabulary presence, not aligning text spans."""
    out: list[str] = []
    for raw in text.lower().split():
        # Strip leading/trailing non-alphanum but keep internal ones
        # so "uspstf" survives "uspstf?" intact.
        stripped = raw.strip(".,;:!?'\"()[]{}<>")
        if stripped:
            out.append(stripped)
    return out
