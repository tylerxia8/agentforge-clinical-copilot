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
from copilot.observability import langfuse_client, observe
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

    @observe(name="chat_turn")
    async def run_turn(
        self,
        ctx: PatientContext,
        message: str,
        history: list[dict[str, Any]],
    ) -> ChatResponse:
        # Tag the trace with patient + user identifiers so traces are
        # filterable in Langfuse by chart / user.
        if langfuse_client is not None:
            try:
                langfuse_client.update_current_trace(
                    user_id=str(ctx.user_id),
                    session_id=ctx.patient_uuid,
                    metadata={
                        "patient_uuid": ctx.patient_uuid,
                        "encounter_uuid": ctx.encounter_uuid,
                    },
                    input={"message": message, "history_len": len(history)},
                )
            except Exception:  # noqa: BLE001
                pass  # never let observability fail the turn

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

                # Dispatch. (Per-tool spans are a v2 refinement — the
                # @observe on run_turn already gives us a root trace, and
                # @observe on llm.complete gives us LLM generations under
                # it. Tool calls are visible in the trace's output via
                # seen_tool_results.)
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
            refused_response = ChatResponse(
                text=(
                    "I'm not confident in part of that answer. Here's what "
                    "I can defend with sources:\n\n"
                    + tokens.rehydrate(_verified_facts_only(seen_tool_results))
                ),
                sources=sorted(verdict.cited_ids),
                refused=True,
                refusal_reason=verdict.reason,
            )
            _record_trace_output(refused_response)
            return refused_response

        # 7. Rehydrate PHI tokens for the UI.
        ok_response = ChatResponse(
            text=tokens.rehydrate(final_text),
            sources=sorted(verdict.cited_ids),
        )
        _record_trace_output(ok_response)
        return ok_response

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


def _record_trace_output(response: ChatResponse) -> None:
    """Annotate the current Langfuse trace with the final response shape.
    Silent no-op when Langfuse isn't configured."""
    if langfuse_client is None:
        return
    try:
        langfuse_client.update_current_trace(
            output={
                "text": response.text,
                "sources": response.sources,
                "refused": response.refused,
                "refusal_reason": response.refusal_reason,
            },
            tags=["refused"] if response.refused else ["accepted"],
        )
    except Exception:  # noqa: BLE001
        pass


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def _verified_facts_only(tool_results: list[dict[str, Any]]) -> str:
    """Last-resort fallback when verification fails 3x. Emits one
    human-readable line per row so the doctor sees the chart facts
    even when the model's summary couldn't be verified — without
    dumping raw JSON in the chat panel.

    Rows are grouped by resource type (Medications, Problems,
    Allergies, Encounters, Vitals, Labs, Immunizations) for
    scan-readability. Empty groups are skipped.
    """
    by_type: dict[str, list[str]] = {}

    for tr in tool_results:
        for row in tr.get("rows", []):
            rid = row.get("id") or ""
            kind = rid.split("#", 1)[0] if "#" in rid else "Other"
            label = _format_row(rid, kind, row)
            if label:
                by_type.setdefault(kind, []).append(label)

    if not by_type:
        return "  (no tool data was retrieved)"

    # Section order roughly matches the UC-1 briefing order
    section_order = [
        ("MedicationRequest", "Medications"),
        ("Condition",         "Problems"),
        ("AllergyIntolerance", "Allergies"),
        ("Encounter",         "Recent encounters"),
        ("Observation",       "Vitals & labs"),
        ("Immunization",      "Immunizations"),
    ]
    seen_kinds: set[str] = set()
    sections: list[str] = []
    for kind, heading in section_order:
        if kind in by_type:
            sections.append(f"**{heading}**")
            sections.extend(by_type[kind])
            sections.append("")
            seen_kinds.add(kind)
    # Anything we didn't have a heading for, dump under a generic bucket
    for kind, lines in by_type.items():
        if kind in seen_kinds:
            continue
        sections.append(f"**{kind}**")
        sections.extend(lines)
        sections.append("")

    return "\n".join(line for line in sections if line is not None).rstrip()


def _format_row(rid: str, kind: str, row: dict[str, Any]) -> str:
    """One-line human label for a tool row, by resource kind. Used by
    the fallback to render facts the verifier rejected as a tidy
    bullet list rather than raw JSON."""

    # MedicationRequest: "Lisinopril 20 mg PO daily [MedicationRequest#…]"
    if kind == "MedicationRequest":
        drug = row.get("drug_display") or "(unknown drug)"
        dose = row.get("dosage_text") or ""
        suffix = f" — {dose}" if dose else ""
        return f"  - {drug}{suffix} [{rid}]"

    # Condition: "Hypertension (I10), unconfirmed [Condition#…]"
    if kind == "Condition":
        display = row.get("display")
        if not display:
            codes = row.get("codes") or []
            if codes:
                display = codes[0].get("display") or codes[0].get("code") or "Unknown"
            else:
                display = "Unknown condition"
        verif = row.get("verification_status")
        verif_suffix = f", {verif}" if verif and verif != "confirmed" else ""
        return f"  - {display}{verif_suffix} [{rid}]"

    # AllergyIntolerance: "Penicillin (medication; confirmed) [Allergy…#…]"
    if kind == "AllergyIntolerance":
        substance = row.get("substance") or "(unknown substance)"
        cats = row.get("category") or []
        cat_str = "/".join(cats) if cats else ""
        verif = row.get("verification_status") or ""
        bits = [b for b in (cat_str, verif) if b]
        suffix = f" ({'; '.join(bits)})" if bits else ""
        return f"  - {substance}{suffix} [{rid}]"

    # Encounter: "Diabetes follow-up (2026-04-15) [Encounter#…]"
    if kind == "Encounter":
        types = row.get("type") or []
        reason = row.get("reason_code") or []
        label = (reason[0] if reason else (types[0] if types else "Visit")) or "Visit"
        date = (row.get("period_start") or "")[:10]
        suffix = f" ({date})" if date else ""
        return f"  - {label}{suffix} [{rid}]"

    # Observation (vitals + labs): show value + unit when present
    if kind == "Observation":
        name = row.get("vital_name") or row.get("test_name") or "Observation"
        value = row.get("value")
        unit = row.get("unit") or ""
        date = (row.get("effective_datetime") or "")[:10]
        # Composite (BP) stores values under .components
        if value is None and row.get("components"):
            comps = [
                f"{c.get('value')}{c.get('unit') or ''}"
                for c in row["components"] if c.get("value") is not None
            ]
            if comps:
                return f"  - {name}: {' / '.join(comps)} ({date}) [{rid}]"
            # Composite with no values (stub) — skip rather than emit noise
            return ""
        if value is None:
            # Stub reading with no value — skip rather than dumping name only
            return ""
        unit_str = f" {unit}" if unit else " (units not recorded)"
        date_str = f" ({date})" if date else ""
        return f"  - {name}: {value}{unit_str}{date_str} [{rid}]"

    # Immunization: "Influenza vaccine (2024-10-12) [Immunization#…]"
    if kind == "Immunization":
        name = row.get("vaccine_name") or "Vaccine"
        date = (row.get("occurrence_datetime") or "")[:10]
        suffix = f" ({date})" if date else ""
        return f"  - {name}{suffix} [{rid}]"

    # Unknown kind — minimal tag so the user at least sees the citation id
    return f"  - {rid}"
