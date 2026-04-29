"""Structural verification — does every claim in the response cite a
source we actually returned from a tool?

See ARCHITECTURE.md §4.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `[prescriptions#244]`, `[lists#588]`, `[encounter#1042]` — etc.
CITATION_RE = re.compile(r"\[([a-z_]+)#([A-Za-z0-9_-]+)\]")

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

    # 2. Substantive sentences must cite.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    uncited: list[str] = []
    for sentence in sentences:
        if not sentence.strip():
            continue
        if not SUBSTANTIVE_HINTS.search(sentence):
            continue
        if not CITATION_RE.search(sentence):
            uncited.append(sentence.strip())

    if uncited:
        return Verdict(
            passed=False,
            reason=(
                "the following clinical claims have no source citation; "
                "either cite the tool row id or remove the claim:\n"
                + "\n".join(f"  - {s}" for s in uncited)
            ),
            cited_ids=cited_ids,
            uncited_sentences=uncited,
        )

    return Verdict(passed=True, cited_ids=cited_ids)
