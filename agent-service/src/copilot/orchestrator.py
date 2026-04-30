"""The agent loop. One method does the work of one chat turn:

    load context → redact → LLM (with tool-use loop) → verify → rehydrate

Everything else in this package exists to support this function.
See ARCHITECTURE.md §1 for the diagram this implements.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from copilot.context.cache import ContextCache
from copilot.context.patient import PatientContext
from copilot.llm.anthropic_client import LLM
from copilot.llm.prompts import SYSTEM_PROMPT
from copilot.middleware import patient_context as middleware
from copilot.redaction.tokens import TokenMap
from copilot.settings import settings
from copilot.tools import all_tool_specs, get_tool
from copilot.verification import rules as domain_rules
from copilot.verification.structural import Verdict, verify_structural

logger = logging.getLogger(__name__)


class ChatResponse(BaseModel):
    text: str
    sources: list[str]
    refused: bool = False
    refusal_reason: str | None = None


class Orchestrator:
    def __init__(self) -> None:
        self.llm = LLM()
        self.cache = ContextCache()

    async def run_turn(
        self,
        ctx: PatientContext,
        message: str,
        history: list[dict[str, Any]],
    ) -> ChatResponse:
        # 1. Load (or warm) the per-patient context bundle.
        bundle = await self.cache.get_or_warm(ctx)

        # 2. Tokenize PHI before anything goes to the model.
        tokens = TokenMap()
        redacted_bundle = tokens.tokenize_dict(bundle)
        redacted_message = tokens.tokenize_text(message)

        # 2b. Register the bundle's tool results with the verifier — without
        # this, citations to rows the model SAW (in the bundle) but didn't
        # FETCH (via tool_use) get rejected as invented. The bundle is a
        # latency optimization, not a way to bypass verification.
        seen_tool_results: list[dict[str, Any]] = []
        for key in ("medications", "problems", "allergies", "encounters",
                    "vitals", "labs", "immunizations"):
            tr = redacted_bundle.get(key)
            if isinstance(tr, dict) and "rows" in tr:
                seen_tool_results.append(tr)

        # 3. Build the message sequence the model sees.
        messages: list[dict[str, Any]] = list(history)
        messages.append({
            "role": "user",
            "content": (
                "Patient context bundle (already loaded; treat as ground truth):\n"
                f"{json.dumps(redacted_bundle, indent=2)}\n\n"
                f"Question: {redacted_message}"
            ),
        })

        # 4. Tool-use loop. We append every tool result so the verifier
        #    can check that citations refer to rows we actually returned.
        #    (seen_tool_results was pre-populated from the bundle in step 2b.)
        for round_n in range(settings.max_tool_rounds):
            response = await self.llm.complete(
                messages=messages,
                system=SYSTEM_PROMPT,
                tools=all_tool_specs(),
            )

            if response.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_results_for_user_msg: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool = get_tool(block.name)
                if tool is None:
                    tool_results_for_user_msg.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"unknown tool: {block.name}",
                        "is_error": True,
                    })
                    continue

                # Boundary check #1: pre-call.
                try:
                    middleware.enforce_tool_call(ctx, tool, block.input)
                except (middleware.CrossPatientAccessError,
                        middleware.UntargetedToolError) as exc:
                    logger.warning("blocked tool call: %s", exc)
                    tool_results_for_user_msg.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"refused: {exc}",
                        "is_error": True,
                    })
                    continue

                # Dispatch.
                try:
                    raw = await tool.run(ctx, block.input)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("tool %s raised", tool.name)
                    tool_results_for_user_msg.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"tool error: {exc}",
                        "is_error": True,
                    })
                    continue

                # Boundary check #2: post-call.
                cleaned = middleware.enforce_tool_result(ctx, tool, raw)
                redacted = tokens.tokenize_dict(cleaned.model_dump())
                seen_tool_results.append(redacted)
                tool_results_for_user_msg.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(redacted),
                })

            messages.append({"role": "user", "content": tool_results_for_user_msg})
        else:
            logger.warning("max_tool_rounds (%d) hit without final answer", settings.max_tool_rounds)

        # 5. Extract the final text and verify it.
        final_text = _extract_text(response.content)
        verdict = self._verify(final_text, seen_tool_results)

        # 6. Retry up to N times with a corrective message.
        for retry in range(settings.max_verification_retries):
            if verdict.passed:
                break
            logger.info("verification failed (retry %d): %s", retry, verdict.reason)
            messages.append({"role": "assistant", "content": final_text})
            messages.append({
                "role": "user",
                "content": (
                    "Your previous answer didn't pass verification. Please "
                    "revise it. Do not invent new facts; either cite a tool "
                    f"row or remove the claim.\n\nProblem: {verdict.reason}"
                ),
            })
            response = await self.llm.complete(messages=messages, system=SYSTEM_PROMPT)
            final_text = _extract_text(response.content)
            verdict = self._verify(final_text, seen_tool_results)

        if not verdict.passed:
            # Final-failure refusal — the verified-facts-only response from
            # ARCHITECTURE.md §4.1.
            return ChatResponse(
                text=(
                    "I'm not confident in part of that answer. Here's what "
                    "I can defend with sources:\n\n"
                    + _verified_facts_only(seen_tool_results)
                ),
                sources=sorted(verdict.cited_ids),
                refused=True,
                refusal_reason=verdict.reason,
            )

        # 7. Rehydrate PHI tokens for the UI.
        return ChatResponse(
            text=tokens.rehydrate(final_text),
            sources=sorted(verdict.cited_ids),
        )

    def _verify(self, text: str, tool_results: list[dict[str, Any]]) -> Verdict:
        structural = verify_structural(text, tool_results)
        if not structural.passed:
            return structural
        domain = domain_rules.run_all(text, tool_results)
        if not domain.passed:
            return Verdict(passed=False, reason=domain.reason, cited_ids=structural.cited_ids)
        # TODO(thursday): if structural+domain pass but the response
        # contains nuanced clinical reasoning (e.g. UC-3 reconciliation),
        # invoke verification.judge for an LLM-as-judge approval.
        return structural


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def _verified_facts_only(tool_results: list[dict[str, Any]]) -> str:
    """Last-resort fallback when verification fails 3x. Lists the raw
    rows so the doctor sees the underlying data even if the model's
    summary couldn't be verified.
    """
    lines: list[str] = []
    for tr in tool_results:
        for row in tr.get("rows", []):
            rid = row.get("id", "?")
            lines.append(f"  - [{rid}] {json.dumps({k: v for k, v in row.items() if not k.startswith('_')})}")
    return "\n".join(lines) if lines else "  (no tool data was retrieved)"
