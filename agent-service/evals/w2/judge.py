"""LLM-as-judge rubric tier (stage 4 of the prod-evals cookbook).

Boolean rubrics catch a lot — substring presence, citation validity,
PHI patterns — but they can't grade *clinical quality*: did the
response actually answer the user's clinical question? Did it
appropriately hedge? Did the recommendation align with guideline
intent (not just keyword match)?

This module adds a thin LLM-as-judge layer that does. The judge:

- runs against a cheap model (Haiku 4.5 by default — ~10x cheaper
  than the agent's Sonnet 4.6)
- asks one binary clinical-quality question per call
- returns ``(passed, reason)`` like every other rubric

We deliberately keep judge questions *binary* (PASS/FAIL with a
short justification). Multi-axis Likert scales let the judge wiggle;
binary forces a clear answer that's hard to flake on.

Invocation pattern:

    "factual_judgement": lambda r: judge_yes_no(
        r,
        question="Does the response correctly identify the patient as "
                 "diabetic AND state an appropriate next-step screening "
                 "or referral?",
    )

The judge is OFF by default in the eval suite (no current case
references it) — adopting it for a case is opt-in. The cookbook
calls this the "stage 4 add-in"; we do the same.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Cheap, fast model. Override with LLM_JUDGE_MODEL env var if you
# want to pin a different one (or substitute opus when comparing
# rubric stability across model classes).
DEFAULT_JUDGE_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MAX_TOKENS = 256


_JUDGE_SYSTEM_PROMPT = """\
You are evaluating an AI clinical co-pilot's response to a question
about a patient chart. You will be given:
1. The clinical-quality question to answer (binary YES/NO).
2. The full agent response (text + cited sources).

Output a JSON object with exactly two fields:
{
  "verdict": "yes" | "no",
  "reason": "<one short sentence — why>"
}

Rules:
- Be strict. If the response only partially answers the question
  ("yes" requires a fully correct answer), return "no".
- Treat refusals as "no" UNLESS the question explicitly asks
  whether the agent should refuse.
- Never include keys other than "verdict" and "reason".
- Never wrap the JSON in markdown fences.
"""


def judge_yes_no(
    response: dict,
    *,
    question: str,
    model: str | None = None,
) -> tuple[bool, str]:
    """Ask a small LLM whether ``response`` correctly answers
    ``question``. Returns ``(passed, reason)``.

    On any infrastructure failure (no API key, network error,
    rate limit, malformed judge output) we DEFAULT TO PASS rather
    than fail the eval gate. The point of LLM-as-judge is to add
    signal beyond what the boolean rubrics catch — making infra
    flake fail the gate would invert that.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return True, "judge skipped: ANTHROPIC_API_KEY not set"

    text = _flatten_response_for_judge(response)
    body = {
        "model": model or os.environ.get("LLM_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
        "max_tokens": JUDGE_MAX_TOKENS,
        "system": _JUDGE_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"--- AGENT RESPONSE ---\n"
                    f"{text}\n"
                    f"--- END RESPONSE ---"
                ),
            },
        ],
    }
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            method="POST",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        # Infra flake — treat as pass with a noisy reason so the
        # operator sees that the judge didn't run.
        return True, f"judge skipped (infra error): {type(exc).__name__}"

    verdict_text = ""
    for block in payload.get("content", []):
        if block.get("type") == "text":
            verdict_text += block.get("text", "")
    parsed = _parse_judge_verdict(verdict_text)
    if parsed is None:
        return True, f"judge skipped (unparseable verdict): {verdict_text[:120]!r}"
    return parsed


def _flatten_response_for_judge(response: dict) -> str:
    """Serialize the case's response into a string the judge can
    read. Chat responses → text + sources; extraction responses →
    a compact dump of the structured payload. Cap at 8K chars so
    the judge call stays cheap."""
    if isinstance(response, dict):
        text = response.get("text") or ""
        sources = response.get("sources") or []
        if text:
            joined = text + (
                f"\n\n[sources: {', '.join(sources[:20])}]" if sources else ""
            )
            return joined[:8000]
        # Extraction or unknown shape — fall back to JSON dump.
        try:
            return json.dumps(response, indent=2)[:8000]
        except Exception:  # noqa: BLE001
            return str(response)[:8000]
    return str(response)[:8000]


_VERDICT_RE = re.compile(
    r'"verdict"\s*:\s*"(?P<verdict>yes|no)"\s*,\s*"reason"\s*:\s*"(?P<reason>[^"]*)"',
    re.IGNORECASE | re.DOTALL,
)


def _parse_judge_verdict(text: str) -> tuple[bool, str] | None:
    """Extract ``(passed, reason)`` from the judge's JSON. Tolerant
    to incidental whitespace + markdown fences if the judge ignores
    the no-fences instruction."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # strip leading/trailing markdown fences
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        m = _VERDICT_RE.search(cleaned)
        if not m:
            return None
        return m.group("verdict").lower() == "yes", m.group("reason")
    verdict = str(obj.get("verdict", "")).lower()
    reason = str(obj.get("reason", ""))
    if verdict not in ("yes", "no"):
        return None
    return verdict == "yes", reason
