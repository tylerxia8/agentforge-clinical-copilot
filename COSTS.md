# COSTS.md — AgentForge W3 Adversarial Platform

> Cost analysis for the W3 multi-agent adversarial AI security
> platform. Actual development spend + per-attempt cost
> decomposition + projections at 100 / 1K / 10K / 100K test
> runs/day with the architectural changes each scale demands.
>
> Companion docs:
> - [W1_COSTS.md](W1_COSTS.md) — W1 Clinical Co-Pilot cost projections at
>   100 → 100K users (the agent itself)
> - [W2_COSTS.md](W2_COSTS.md) — W2 multimodal agent (vision, RAG,
>   ingestion) cost & latency
>
> This document covers the W3 platform that attacks the W2 target.

---

## Summary

The platform's per-attempt cost decomposes into four LLM lines
(Orchestrator + Red Team + Judge + Documentation) plus one
infrastructure line (target API call). Three of those four
LLM calls are skippable in specific conditions: the Orchestrator
only fires once per *campaign* (not per attempt); the
Documentation Agent only fires on success/partial verdicts; the
Judge skips the LLM entirely when a deterministic-signal check
is conclusive (which the 20260512T033255Z verification run
demonstrated fires on ~80% of `target.refused=True` cases).

**Actual dev spend through 2026-05-12 morning (4 days into the
W3 sprint):** ~$10–12 across all Anthropic line items (Red Team
Sonnet, Judge Haiku, Orchestrator Sonnet, Documentation Haiku)
plus ~$3–5 in target-side costs (the deployed W2 Co-Pilot
serving the 92 attempts). Total ~$13–17 to build a four-agent
platform that runs autonomously against the live target.
Observed average per attempt across all 92 historical attempts:
~$0.18 (steady-state after the Judge calibration fix is
~$0.030–0.060 per attempt; the historical average is higher
because the largest run also triggered the Documentation Agent
7 times and ran with pre-fix Judge logic that double-billed via
retry paths).

**Per-attempt cost decomposition** (steady-state):

| Line item | When it fires | Typical cost/attempt |
|---|---|---|
| Red Team Agent (Sonnet 4.6) | every attempt | $0.005–0.010 |
| Target call (W2 `/demo/chat`) | every attempt | $0.020–0.045 |
| Judge — deterministic path | ~50% of attempts | $0.000 |
| Judge — LLM path (Haiku 4.5) | ~50% of attempts | $0.001–0.002 |
| Orchestrator (Sonnet 4.6) | 1 per *campaign* (~$0.005), amortized | ~$0.001/attempt |
| Documentation Agent (Haiku 4.5) | only on success/partial verdicts | $0.002–0.003 per finding |
| **Per-attempt average** | | **~$0.030–0.060** |

**Cost projection at scale, with architectural changes:**

| Volume | Daily cost | Architectural change |
|---|---|---|
| 100 runs/day | $3–6 | Current architecture. No changes. |
| 1K runs/day | $30–60 | Add per-campaign concurrency cap (max 4); per-min token throttle; backoff on 429s. |
| 10K runs/day | $200–400 | Swap Red Team to local Ollama Llama 3 8B for ~80% of generations; keep Sonnet for novel-category + mutate. Per-campaign budget caps become load-bearing. |
| 100K runs/day | $600–1,500 | Most attack generation becomes *deterministic* — pattern libraries extracted from successful prior runs replace LLM calls on common categories. |

The 100K-runs scale exists because at 100 enterprise customers
each running ~1K runs/day, you reach that volume. The
architecture above degrades from "AI-driven attack generation"
to "AI-supervised attack library execution" — same regression
guarantee, ~10x cheaper.

---

## Actual dev spend — itemized

Counted across all runs in `agent-service/evals/redteam_runs/`:

| Run | Date | Attempts | Mode | Notes |
|---|---|---|---|---|
| 20260511T210348Z | 5/11 21:03 | 2 | cross_patient smoke | first end-to-end probe |
| 20260511T212149Z | 5/11 21:21 | 15 | 3 cats × 5 hops | MVP — surfaced Judge bug #1 (FP regex) |
| 20260511T213300Z | 5/11 21:33 | 5 | indirect_injection | Judge fix #1 verification |
| 20260511T213802Z | 5/11 21:38 | 15 | 3 cats × 5 hops | canonical MVP submission |
| 20260511T223132Z | 5/11 22:31 | 5 | orchestrator × 1 | first Orchestrator end-to-end |
| 20260512T030127Z | 5/12 03:01 | 5 | state_corruption | first multi-turn run |
| 20260512T030840Z | 5/12 03:08 | ~40 | orchestrator × 6 | surfaced Judge bug #2 (FP-with-refused-true) |
| 20260512T033255Z | 5/12 03:32 | 5 | state_corruption | Judge fix #2 verification |
| **Total** | | **92** | | |

Approximate cost per attempt by agent line item (computed against
Anthropic Sonnet 4.6 + Haiku 4.5 token rates as of 2026-05-12):

- **Red Team Sonnet**: input ~700–900 tokens × $3/M = ~$0.0024;
  output ~250–400 tokens × $15/M = ~$0.0048; **subtotal
  ~$0.007/attempt** (state_corruption can reach $0.012 because
  the seed history is larger)
- **Judge Haiku LLM path**: input ~400–600 tokens × $0.80/M =
  ~$0.0004; output ~120–200 tokens × $4/M = ~$0.0006; **subtotal
  ~$0.001/attempt**
- **Judge deterministic path**: **$0** (no LLM call). Fires when
  `target.refused=True` + no foreign UUID + no injection marker
  (see `agent-service/src/redteam/judge.py::_check_refused_with_scoped_data`)
- **Target `/demo/chat`**: **$0.020–0.045/attempt** depending on
  whether retrieval fired (RAG-active turns are ~2x baseline),
  response length, and whether the W2 verifier ran retries.
  Cost-amplification campaign attempts hit the top of this range.
- **Orchestrator amortized**: ~$0.005/call × 1 call per ~5-hop
  campaign = **~$0.001/attempt**
- **Documentation Agent**: ~$0.002/finding × 7 fires / 92 attempts
  = **~$0.0002/attempt** amortized

**Per-attempt steady-state**: $0.030–0.060
**Per-attempt observed average across 92 attempts**: $0.18 — the
spread reflects (a) pre-fix Judge retry costs on the first two
runs, (b) the Documentation Agent firing 7 times during the
biggest run, (c) cost-amplification attempts driving the target
to the top of its per-turn cost range.

**Total spend through 2026-05-12 morning: ~$13–17.**

---

## Per-line-item drivers + sensitivity

**Red Team cost is dominated by output tokens.** Sonnet output is
$15/M; input is $3/M. The Red Team's job is to GENERATE content,
so output dominates. Cutting Red Team `max_tokens` from 1024 to
512 would roughly halve this line at minimal quality cost — most
generated attacks fit comfortably in 200–400 output tokens.

**Target cost depends on the target's behavior.** Cost-
amplification attacks DRIVE the target into expensive paths
(vision + RAG + long output). A single attack that triggers all
three can cost the target $0.10 against ~$0.005 for the Red
Team's generation. The asymmetry is real — see VULN-0001 in
`vulns/` for the architectural finding this implies.

**Judge cost is bounded by the deterministic layer.** The
20260512T033255Z verification run hit the deterministic path on
5/5 attempts (`target.refused=True` + no foreign UUID + no
injection marker → skip LLM). Across all 92 attempts the
deterministic path fires on ~50% (cost-amplification campaigns
usually hit the LLM path because the target produces a long
non-refusal response rather than `refused=True`).

**Orchestrator is cheapest per call but most strategic.** One
Sonnet call per campaign decides which category gets the next
~5 hops. At $0.005/call across ~$0.150 per-campaign attack spend,
the Orchestrator's overhead is ~3% — negligible against its
ability to route hops away from saturated categories. The
20260512T030840Z run's verbatim Orchestrator rationale:
*"state_corruption has the only active partial verdict ... mutating
the known partial is the highest-efficiency path to a confirmed
bypass"* — that strategic reasoning was a $0.005 line item.

**Documentation Agent only fires on findings.** Across all 92
attempts the Documentation Agent ran 7 times (all 7 became
auto-gated `_pending/` reports per the trust gate — see
`vulns/_pending/README.md` for the FP analysis). Amortized cost:
~$0.0002/attempt.

---

## Projection at scale

### 100 test runs / day

Current architecture. ~10 hops × 10 campaigns/day = 100 attempts.
At ~$0.04/attempt: **$4/day = ~$120/month**. Well within budget
for continuous testing against a single target.

**No architectural changes required.**

### 1K test runs / day

Same architecture, but Anthropic's per-org TPM rate limit becomes
the binding constraint, not cost.

**Architectural changes:**

- **Per-campaign concurrency cap.** Max 4 simultaneous campaigns
  on the platform's worker pool. Otherwise N concurrent 10-hop
  campaigns slam the Anthropic TPM ceiling and trigger 429s
  mid-run.
- **Token throttle on Red Team output.** Drop `max_tokens` from
  1024 to 512 for Red Team calls. Saves ~30% on Red Team output
  without quality regression.
- **Backoff + retry** on 429s from Anthropic. The current
  `httpx` client doesn't backoff; add exponential backoff with
  jitter at the Anthropic SDK boundary.

Daily cost: **~$30-60**.

### 10K test runs / day

Per-token cost becomes the binding constraint, not rate-limit.
Daily spend at current architecture would be **~$300-450/day =
~$10K/month**. Optimization kicks in.

**Architectural changes:**

- **Red Team model swap to Ollama Llama 3 8B** for ~80% of
  attack generation. The local model runs at ~$0.0003/attempt
  vs. Sonnet's $0.007 — per-attempt Red Team cost drops ~95%.
- **Keep Sonnet for high-value cases**: novel-category
  exploration where attack quality matters, plus all mutate-mode
  generations (a partial verdict signals the variant is worth
  Sonnet quality). The Orchestrator's category-routing logic
  decides this — see `orchestrator.py`.
- **Per-campaign budget cap becomes load-bearing.** Today it's
  set in `AttackCampaign.cost_budget_usd` but enforced
  approximately (per-attempt cost is approximated, not measured
  from Langfuse usage events). At 10K/day, the approximation
  diverges from reality; wire Langfuse usage events into the
  cost ledger.
- **Judge stays on Haiku** (already cheap at scale).

Daily cost: **~$200-400**. Mostly target costs + Haiku judge.

### 100K test runs / day

Enterprise scale — 100 customers each running ~1K/day against
their own target. The platform's *architecture* needs a
fundamental shift: most attack generation becomes
*deterministic*.

**Architectural changes:**

- **Attack pattern library.** Mutations that have proven
  effective in prior runs (per the run logs in
  `evals/redteam_runs/`) become regex-or-template-driven
  generators. The local Ollama model is now the FALLBACK, not
  primary, for ~70% of attack generation.
- **Sonnet only for novel surface.** When the Orchestrator
  determines a campaign is exploring genuinely new territory
  (no prior coverage data), call Sonnet. Small fraction of
  total attempts at this scale.
- **Judge LLM path under-budget bias.** At 100K/day the Judge's
  ~50% LLM-path hit rate × $0.001 = $50/day baseline.
  Acceptable. Above that, the architecture-doc-mentioned
  judge-of-judge audit (Sonnet re-grading a Haiku sample
  periodically) is the cost governor.
- **Target cost is now externally bounded.** If the target is a
  customer's deployed system, their billing not ours. If it's
  our internal target, cap concurrent in-flight requests against
  any single target.

Daily cost: **~$600-1,500** *platform* spend; target spend
becomes the bigger line item at this scale, possibly
$2K-5K/day depending on customer target architecture.

---

## Cost amplification — the asymmetric risk

VULN-0001 in `vulns/` documents a finding the W3 platform helped
surface: `/demo/chat` has no per-IP rate limit. The asymmetry is
real and worth quantifying:

- Per attack generation by the Red Team: **~$0.007** (Sonnet
  call)
- Per attack response by the target: **~$0.040** (target runs
  RAG + vision + verifier + response generation)
- Ratio: attacker pays ~$1 for every ~$5-6 the target spends.

An attacker holding 100 concurrent sessions at 1 RPS pays
~$25/hour but burns ~$144/hour at the target. Over a day that's
~$3,500 in target spend against $600 attacker cost. The asymmetry
favors the attacker by ~6x.

Remediation is per-IP rate limiting at target ingress (documented
in VULN-0001). After that fix, the asymmetry flips: the attacker
still pays per attempt but the target processes only the bucket-
allowed fraction.

---

## What's NOT in this cost analysis

- **Infrastructure cost** (Railway hosting, Postgres + Redis,
  Langfuse) is fixed-per-month at ~$30 total across the four
  W3-specific services and dwarfed by Anthropic spend at any
  scale above 1K runs/day.
- **Engineering time** to operate the platform — automation
  reduces this to near-zero per finding, but human approval of
  `_pending/` reports remains required. ~5 min/critical
  finding triage; at ~10 criticals/month, ~1 person-hour/month.
- **Target system cost.** The W2 Co-Pilot has its own cost
  analysis in `W2_COSTS.md`. W3 attack costs and W2 serving
  costs are bookkept separately because at enterprise scale
  the target is a customer's system.

---

## How to query real cost going forward

Every agent call emits a Langfuse trace with token-usage
metadata. The Orchestrator (`orchestrator.py`) reads
`cost_to_date` to enforce `cost_budget_usd` per campaign. For
aggregate dashboarding:

- **Per-day platform spend**: Langfuse dashboard, filter on tags
  `agent=redteam` OR `agent=judge` OR `agent=orchestrator` OR
  `agent=documentation`.
- **Per-target spend**: Langfuse dashboard on the W2 deployment,
  filter on traces originating from `/demo/chat` with the
  user-agent header `agentforge-redteam/0.1`.
- **Per-finding cost**: each vuln report's discovery campaign
  has a `cost_usd` field aggregating its hop attempts. Sum
  across attempts in `agent-service/evals/redteam_runs/<timestamp>/campaign_*.json`.

The cost ledger is the same data substrate the Orchestrator uses
for strategic decisions. Operators and the strategic-layer
agent read the same data.
