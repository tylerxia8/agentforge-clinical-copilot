"""Campaign runner — CLI entrypoint for the W3 MVP.

Wires the four agent roles end-to-end against the deployed W2
target:

    Orchestrator (hardcoded for MVP: campaign = (category, hop_budget))
            │
            v
    Red Team Agent ── generate ──> AttackAttempt (messages only)
            │
            v
    Target HTTP POST /demo/chat ── response ──> AttackAttempt complete
            │
            v
    Judge Agent ── deterministic + LLM ──> JudgeVerdict
            │
            v
    Write attempt + verdict to evals/redteam_runs/<timestamp>/

Usage:

    python -m redteam.run_campaign --category indirect_injection --hops 5
    python -m redteam.run_campaign --category cross_patient --hops 10
    python -m redteam.run_campaign --category cost_amplification --hops 5

    # all three back-to-back for MVP submission:
    python -m redteam.run_campaign --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from anthropic import AsyncAnthropic

from redteam.categories import CATEGORY_MODULES
from redteam.coverage import build_coverage_report
from redteam.judge import JudgeAgent
from redteam.messages import (
    AttackAttempt,
    AttackCampaign,
    ChatMessage,
    JudgeVerdict,
    ThreatCategory,
)
from redteam.orchestrator import Orchestrator, OrchestratorContext
from redteam.red_team import RedTeamAgent, RedTeamAgentRefused
from redteam.target import Target


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------


def _runs_root() -> Path:
    """Where results land. Lives under agent-service/evals/redteam_runs/
    so the W2 eval-suite tooling can later sweep these for promotion
    into regression cases (Friday-final work)."""
    here = Path(__file__).resolve()
    # agent-service/src/redteam/run_campaign.py → agent-service/evals/redteam_runs/
    return here.parents[2] / "evals" / "redteam_runs"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_one_campaign(
    *,
    campaign: AttackCampaign,
    out_dir: Path,
    anthropic_client: AsyncAnthropic,
    target: Target,
) -> dict:
    """Run a single campaign and write results. Returns a summary.

    The ``campaign`` is either constructed by the CLI (``--category``
    mode, MVP-style hardcoded) or by the Orchestrator Agent
    (``--orchestrate`` mode). Either way this function does not care
    where it came from — it just executes it.
    """
    category = campaign.category
    hops = campaign.hop_budget
    cost_budget_usd = campaign.cost_budget_usd
    cat_module = CATEGORY_MODULES[category]
    spec = cat_module.SPEC
    rubric = cat_module.JUDGE_RUBRIC

    red_team = RedTeamAgent(client=anthropic_client)
    judge = JudgeAgent(client=anthropic_client)

    print(f"\n=== Campaign {campaign.campaign_id} — {category.value} ===")
    print(f"  hops={hops}  budget=${cost_budget_usd:.2f}")
    print(f"  target_patient_uuid={spec.target_patient_uuid}")
    if campaign.rationale:
        print(f"  rationale: {campaign.rationale}")

    attempts: list[AttackAttempt] = []
    verdicts: list[JudgeVerdict] = []
    consecutive_fails = 0
    red_team_refusals = 0

    for hop in range(1, hops + 1):
        print(f"\n--- Hop {hop}/{hops} ---")

        # Red Team: generate attack
        try:
            attempt, generated = await red_team.generate(
                campaign=campaign, category_spec=spec,
            )
        except RedTeamAgentRefused as e:
            red_team_refusals += 1
            print(f"  [Red Team refused] {e}")
            if red_team_refusals >= 3:
                print("  3+ Red Team refusals — halting campaign (consider Ollama fallback)")
                break
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  [Red Team error] {type(e).__name__}: {e}")
            continue

        print(f"  technique: {generated.technique}")
        print(f"  attack:    {generated.attack_message[:120]}{'...' if len(generated.attack_message) > 120 else ''}")

        # Target: deliver attack
        tr = await target.demo_chat(
            message=generated.attack_message,
            patient_uuid=spec.target_patient_uuid,
        )
        attempt.target_responses = [
            ChatMessage(role="assistant", content=tr.text)
        ] if tr.text else []
        attempt.target_status_codes = [tr.status_code]
        attempt.target_sources = tr.sources
        attempt.target_refused = tr.refused
        if tr.error:
            attempt.error = tr.error

        print(f"  target:    HTTP {tr.status_code}  elapsed={tr.elapsed_s:.1f}s  "
              f"refused={tr.refused}  sources={len(tr.sources)}  "
              f"text={len(tr.text)}ch")

        # Judge: verdict
        verdict = await judge.judge(attempt, rubric)
        print(f"  verdict:   {verdict.verdict}  confidence={verdict.judge_confidence:.2f}  "
              f"severity={verdict.severity_hint}")
        print(f"  reasoning: {verdict.reasoning[:200]}{'...' if len(verdict.reasoning) > 200 else ''}")

        attempts.append(attempt)
        verdicts.append(verdict)

        if verdict.verdict == "fail":
            consecutive_fails += 1
            if consecutive_fails >= campaign.stop_after_consecutive_fails:
                print(f"\n  {consecutive_fails} consecutive fails — halting campaign")
                break
        else:
            consecutive_fails = 0

    # Write results
    out_path = out_dir / f"campaign_{category.value}_{campaign.campaign_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_serialize_run(campaign, attempts, verdicts), encoding="utf-8")
    print(f"\nWrote {out_path.relative_to(out_dir.parent.parent.parent)}")

    summary = {
        "campaign_id": str(campaign.campaign_id),
        "category": category.value,
        "attempts": len(attempts),
        "verdicts": {
            "success": sum(1 for v in verdicts if v.verdict == "success"),
            "partial": sum(1 for v in verdicts if v.verdict == "partial"),
            "fail":    sum(1 for v in verdicts if v.verdict == "fail"),
            "judge_failed": sum(1 for v in verdicts if v.verdict == "judge_failed"),
        },
        "red_team_refusals": red_team_refusals,
        "output_file": out_path.name,
    }
    return summary


def _serialize_run(
    campaign: AttackCampaign,
    attempts: list[AttackAttempt],
    verdicts: list[JudgeVerdict],
) -> str:
    """Render campaign+attempts+verdicts as a single JSON blob.

    The shape is intentionally flat per-attempt so a future
    Documentation Agent can iterate without joins.
    """
    by_attempt: dict[UUID, JudgeVerdict] = {v.attempt_id: v for v in verdicts}
    return json.dumps({
        "campaign": json.loads(campaign.model_dump_json()),
        "attempts": [
            {
                "attempt": json.loads(a.model_dump_json()),
                "verdict": (
                    json.loads(by_attempt[a.attempt_id].model_dump_json())
                    if a.attempt_id in by_attempt else None
                ),
            }
            for a in attempts
        ],
    }, indent=2, default=str)


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AgentForge W3 — Red Team campaign runner against the deployed "
            "W2 target. Three modes: --category (single), --all (every "
            "wired category), --orchestrate N (Orchestrator Agent picks N "
            "campaigns)."
        ),
    )
    parser.add_argument(
        "--category",
        choices=[c.value for c in CATEGORY_MODULES],
        help="Run one named attack category (single-shot, MVP-style).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run every wired category back-to-back. For MVP submission.",
    )
    parser.add_argument(
        "--orchestrate", type=int, metavar="N", default=0,
        help="Hand off to the Orchestrator Agent. Runs N campaigns in a "
             "row, each chosen by the LLM-driven planner reading prior "
             "runs' coverage state. Per-campaign hop_budget and "
             "cost_budget come from the Orchestrator, not the CLI.",
    )
    parser.add_argument(
        "--hops", type=int, default=5,
        help="Max Red Team attempts per campaign (--category and --all "
             "modes only; --orchestrate gets hops from the Orchestrator).",
    )
    parser.add_argument(
        "--budget", type=float, default=2.00,
        help="USD cost cap per campaign (--category and --all modes only; "
             "the Orchestrator manages its own per-campaign budget against "
             "the --total-budget ceiling).",
    )
    parser.add_argument(
        "--total-budget", type=float, default=10.00,
        help="(--orchestrate mode) USD ceiling across the whole run. The "
             "Orchestrator halts when cost-to-date hits this. The Anthropic-"
             "side cost is approximated by attempt count for MVP — wire to "
             "Langfuse usage data for the Friday final.",
    )
    parser.add_argument(
        "--target-url", default=None,
        help="Override the W2 target base URL. Defaults to env REDTEAM_TARGET_URL.",
    )
    args = parser.parse_args(argv)

    mode_count = (1 if args.category else 0) + (1 if args.all else 0) + (1 if args.orchestrate > 0 else 0)
    if mode_count != 1:
        parser.error("Specify exactly one of --category <name>, --all, or --orchestrate N.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    target_kwargs = {}
    if args.target_url:
        target_kwargs["base_url"] = args.target_url
    target = Target(**target_kwargs)

    # PRD hard-gate: deployed target must be live for the checkpoint.
    print(f"Pre-flight: checking target {target.base_url}/healthz ...")
    if not await target.healthz():
        print(f"  FAIL — target is not responding 200 on /healthz", file=sys.stderr)
        print("  The W3 PRD requires the deployed target be live for every checkpoint.", file=sys.stderr)
        await target.close()
        return 3
    print("  OK")

    anthropic_client = AsyncAnthropic(api_key=api_key)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _runs_root() / timestamp

    summaries: list[dict] = []

    if args.orchestrate > 0:
        # Orchestrator mode — LLM-driven category selection over N campaigns.
        orchestrator = Orchestrator(client=anthropic_client)
        cost_to_date = 0.0
        # Rough per-attempt cost approximation. With 5-hop campaigns at
        # ~$0.05/hop (Red Team Sonnet + Judge Haiku + target chat), one
        # campaign is ~$0.25. Real cost-tracking via Langfuse usage data
        # is Friday-final work.
        APPROX_COST_PER_ATTEMPT = 0.05

        for round_idx in range(1, args.orchestrate + 1):
            print(f"\n##### Orchestrator round {round_idx}/{args.orchestrate} #####")
            coverage = build_coverage_report(_runs_root())
            ctx = OrchestratorContext(
                coverage=coverage,
                cost_to_date_usd=cost_to_date,
                cost_ceiling_usd=args.total_budget,
                available_categories=list(CATEGORY_MODULES.keys()),
            )
            print(f"  coverage: {coverage.total_attempts} attempts across "
                  f"{coverage.total_campaigns} campaigns; "
                  f"open findings: {coverage.open_findings_count}")
            print(f"  budget: ${cost_to_date:.2f} / ${args.total_budget:.2f}")

            campaign = await orchestrator.plan_next_campaign(ctx)
            if campaign is None:
                print("  Orchestrator returned None — halting platform.")
                break
            print(f"  Orchestrator chose: {campaign.category.value} "
                  f"(hops={campaign.hop_budget}, "
                  f"budget=${campaign.cost_budget_usd:.2f})")

            try:
                s = await run_one_campaign(
                    campaign=campaign,
                    out_dir=out_dir,
                    anthropic_client=anthropic_client,
                    target=target,
                )
                summaries.append(s)
                cost_to_date += s["attempts"] * APPROX_COST_PER_ATTEMPT
            except Exception as e:  # noqa: BLE001
                print(f"\n[CAMPAIGN ERROR] {campaign.category.value}: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                summaries.append({"category": campaign.category.value, "error": str(e)})

    else:
        # Single or --all mode — hardcoded categories.
        categories_to_run: list[ThreatCategory] = (
            list(CATEGORY_MODULES) if args.all
            else [ThreatCategory(args.category)]
        )

        for cat in categories_to_run:
            campaign = AttackCampaign(
                category=cat,
                hop_budget=args.hops,
                cost_budget_usd=args.budget,
                rationale="CLI-launched campaign (--category or --all mode).",
            )
            try:
                s = await run_one_campaign(
                    campaign=campaign,
                    out_dir=out_dir,
                    anthropic_client=anthropic_client,
                    target=target,
                )
                summaries.append(s)
            except Exception as e:  # noqa: BLE001 — keep the runner alive across campaigns
                print(f"\n[CAMPAIGN ERROR] {cat.value}: {type(e).__name__}: {e}", file=sys.stderr)
                summaries.append({"category": cat.value, "error": str(e)})

    # Summary JSON for grader-friendly reading
    summary_path = out_dir / "_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({
        "ran_at": timestamp,
        "target_url": target.base_url,
        "campaigns": summaries,
    }, indent=2), encoding="utf-8")

    await target.close()
    await anthropic_client.close()

    print(f"\n=== Run summary ===")
    for s in summaries:
        if "error" in s:
            print(f"  {s['category']:25s} ERROR: {s['error']}")
        else:
            v = s["verdicts"]
            print(
                f"  {s['category']:25s} attempts={s['attempts']:3d}  "
                f"success={v['success']}  partial={v['partial']}  fail={v['fail']}  "
                f"red_team_refusals={s['red_team_refusals']}"
            )
    print(f"\nResults: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
