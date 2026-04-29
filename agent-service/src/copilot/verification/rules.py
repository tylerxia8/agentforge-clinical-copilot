"""Domain rules — deterministic guardrails on what the agent can claim.

Each rule is a function that takes the response text and the tool results,
and returns a Verdict. They run in order; the first failure short-circuits.

See ARCHITECTURE.md §4.2 for the full set. Two rules sketched here; the
remaining three (allergy verification state, problem-list dedup,
refusal-when-empty) follow the same pattern.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from copilot.verification.structural import CITATION_RE, Verdict


def _rows_by_id(tool_results: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for tr in tool_results:
        for row in tr.get("rows", []):
            rid = row.get("id")
            if rid:
                out[rid] = row
    return out


# ─── Rule: vitals must report units or say "units not recorded" ────────

_VITAL_NUMERIC = re.compile(
    r"\b(weight|height|temperature|temp|bp|blood pressure|pulse|hr|spo2|"
    r"oxygen saturation)\b[^.]*?\b\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)

def vitals_unit_rule(text: str, tool_results: list[dict]) -> Verdict:
    rows = _rows_by_id(tool_results)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        if not _VITAL_NUMERIC.search(s):
            continue
        # The sentence states a vital with a number. It must either:
        # (a) include "units not recorded" / "unit unknown", OR
        # (b) cite a row whose unit_verified flag is True.
        if re.search(r"units?\s+(not\s+recorded|unknown|unspecified)", s, re.I):
            continue
        cited = [rows.get(f"{t}#{p}") for t, p in CITATION_RE.findall(s)]
        if any((r or {}).get("unit_verified") for r in cited):
            continue
        return Verdict(
            passed=False,
            reason=(
                "claim about a vital sign does not specify units, and the "
                "cited rows have no verified unit metadata. either cite a "
                "row with unit_verified=true or add '(units not recorded)':\n"
                f"  {s.strip()}"
            ),
        )
    return Verdict(passed=True)


# ─── Rule: "currently on" / "active" claims must cite an active row ────

_CURRENT_MED = re.compile(
    r"\b(currently on|active(?:ly)?\s+(?:taking|on|prescribed)|on\s+(?:active\s+)?"
    r"(?:therapy|treatment|prescription))\b",
    re.IGNORECASE,
)

def med_active_state_rule(text: str, tool_results: list[dict]) -> Verdict:
    rows = _rows_by_id(tool_results)
    today = date.today().isoformat()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        if not _CURRENT_MED.search(s):
            continue
        cited = [rows.get(f"{t}#{p}") for t, p in CITATION_RE.findall(s)]
        if not any(_is_active_med(r, today) for r in cited if r):
            return Verdict(
                passed=False,
                reason=(
                    "claim describes a medication as currently active, but no "
                    "cited row has active=true and an open end_date:\n"
                    f"  {s.strip()}"
                ),
            )
    return Verdict(passed=True)


def _is_active_med(row: dict, today: str) -> bool:
    if not row.get("active"):
        return False
    end_date = row.get("end_date")
    if end_date and end_date < today:
        return False
    return True


# ─── TODO(thursday): wire the remaining three rules ───────────────────
# - allergy_verification_rule: every allergy claim names the verification
#   state (confirmed / unconfirmed / refuted / entered-in-error).
# - problem_dedup_rule: if two cited rows describe the same condition under
#   ICD-9 and ICD-10, the response must surface the dedup, not double-count.
# - refusal_when_empty_rule: if a tool returns no rows, the response cannot
#   make affirmative claims about that data type.


RULES: tuple[Callable[[str, list[dict]], Verdict], ...] = (
    vitals_unit_rule,
    med_active_state_rule,
)


def run_all(text: str, tool_results: list[dict]) -> Verdict:
    for rule in RULES:
        v = rule(text, tool_results)
        if not v.passed:
            return v
    return Verdict(passed=True)
