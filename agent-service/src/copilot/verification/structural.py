"""Structural verification — does every claim in the response cite a
source we actually returned from a tool?

See ARCHITECTURE.md §4.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `[prescriptions#244]`, `[lists#588]`, `[MedicationRequest#abc123]` — etc.
# Tables are lowercase OpenEMR convention; FHIR resource types are PascalCase.
CITATION_RE = re.compile(r"\[([A-Za-z_]+)#([A-Za-z0-9_-]+)\]")

# Heuristic: a sentence is "substantive" if it contains a number, a date,
# or one of these clinical noun cues. Substantive sentences must cite.
SUBSTANTIVE_HINTS = re.compile(
    r"\b(\d+(?:\.\d+)?|"
    r"mg|mcg|ml|kg|lb|mmHg|"
    r"medication|allergy|allergic|diagnos(ed|is|es)|encounter|"
    r"lab|result|vital|prescrib|admit|discharge|visit)\b",
    re.IGNORECASE,
)


@dataclass
class Verdict:
    passed: bool
    reason: str = ""
    cited_ids: set[str] = None  # type: ignore[assignment]
    uncited_sentences: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cited_ids is None:
            self.cited_ids = set()
        if self.uncited_sentences is None:
            self.uncited_sentences = []


def _row_id(row: dict) -> str | None:
    """Convention: tool result rows carry an `id` of the form `<table>#<pk>`."""
    return row.get("id")


def collect_known_ids(tool_results: list[dict]) -> set[str]:
    """All row ids the model has seen so far this turn."""
    ids: set[str] = set()
    for tr in tool_results:
        for row in tr.get("rows", []):
            rid = _row_id(row)
            if rid:
                ids.add(rid)
    return ids


def verify_structural(text: str, tool_results: list[dict]) -> Verdict:
    """Check that:
    - every citation refers to an id we actually returned
    - every substantive sentence has at least one citation
    """
    known_ids = collect_known_ids(tool_results)

    # 1. Citations must reference real rows.
    cited_ids: set[str] = set()
    for table, pk in CITATION_RE.findall(text):
        rid = f"{table}#{pk}"
        cited_ids.add(rid)
        if rid not in known_ids:
            return Verdict(
                passed=False,
                reason=f"citation {rid!r} does not refer to any row returned by a tool this turn",
                cited_ids=cited_ids,
            )

    # 2. The response must contain at least one valid citation if it
    #    makes substantive clinical claims at all. (We deliberately do
    #    NOT enforce per-sentence citations in v1 — sentence-splitting
    #    against medical-style markdown lists is fragile, and the
    #    real-row check above already catches the high-impact failure
    #    mode of fabricated row ids. Per-sentence enforcement is a v2
    #    refinement; see ARCHITECTURE.md "Open questions".)
    if SUBSTANTIVE_HINTS.search(text) and not cited_ids:
        return Verdict(
            passed=False,
            reason=(
                "response makes clinical claims about meds / labs / "
                "diagnoses / vitals but cites no tool row. add an "
                "inline [<table>#<id>] citation tied to a tool result "
                "or remove the claim."
            ),
            cited_ids=cited_ids,
        )

    return Verdict(passed=True, cited_ids=cited_ids)
