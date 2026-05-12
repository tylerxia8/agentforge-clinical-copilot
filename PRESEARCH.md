# Pre-Search Checklist — AgentForge W3 Adversarial Platform

> The PRD's appendix lays out an 11-section architectural-
> discovery checklist across 3 phases. This document answers each
> against what actually shipped: concrete files, concrete
> numbers, concrete model choices. Where a real fork in the road
> appeared, the rejected alternative and the reason are noted.
>
> Companion docs:
> [W2_PRESEARCH.md](W2_PRESEARCH.md) — the W2 architectural discovery
> (the target system this platform attacks).

---

## Phase 1: Define Your Constraints

### 1. Domain & Threat Selection

**Which attack categories will you target first?**
Three at MVP, six at final, per the priority ranking in
[THREAT_MODEL.md](THREAT_MODEL.md) §"Coverage Prioritization":

1. **Indirect prompt injection** — highest priority because the
   target's multi-format ingestion (PDF/HL7/DOCX/XLSX/TIFF) is
   broad surface with no current detection layer beyond Pydantic
   schema. The vision pipeline reads any text it can extract.
2. **Cross-patient PHI leakage** — catastrophic impact, the
   patient-context middleware is mature, probing it for bypass
   is high-signal.
3. **Cost amplification / DoS** — lowest defense maturity (per-IP
   rate limiting was triaged out during W2 calibration; never
   re-introduced).

Final-submission additions: state corruption (multi-turn history
forgery), tool misuse (parameter tampering, unicode UUIDs),
identity exploit (persona hijacking, fake system tags). Direct
injection deferred because the W2 eval suite already has 7
hand-authored cases in that category at 100% pass rate.

**What are the verification requirements for determining exploit
success?**
Two-layer Judge per ARCHITECTURE.md §"Judge Agent":
1. Deterministic signals run BEFORE the LLM. Cross-patient UUID
   match is a string compare. Injection-acknowledgment marker is
   a substring scan. Conclusive verdicts skip the LLM entirely.
2. LLM rubric (Haiku 4.5) for the genuinely ambiguous cases.

The deterministic layer's evidence is in
`agent-service/src/redteam/judge.py::_check_refused_with_scoped_data`
— the universal refused-with-scoped check that closed the
calibration bug class.

**What attack surfaces require human judgment versus automated
assessment?**
Critical-severity findings always escalate to the `_pending/`
trust gate. The PRD's "Where does your system stop and ask a
human?" answer is in ARCHITECTURE.md §"Human approval gates" —
three explicit gates: critical-severity vulns, daily spend
ceiling, net-new attack categories.

---

### 2. Scale & Performance

**Expected test run volume per day/week?**
- MVP (this week): ~10 campaigns/day × 5–8 hops = 50–80
  attempts/day. Actual observed: 92 attempts across 17
  campaigns over 2 days.
- Steady-state for a single target: 100 runs/day = ~$4/day.
- Enterprise scale (100 customer targets): 100K runs/day =
  ~$1.5K/day platform spend.

Detailed projection in [COSTS.md](COSTS.md) §"Projection at scale".

**Acceptable latency for attack generation and evaluation?**
- Red Team generation: ~5–8s per attack (Sonnet 4.6 with
  ~700-token system prompt). Acceptable; matches Anthropic's
  median Sonnet latency.
- Target response: 5–35s depending on whether vision/RAG fired.
- Judge verdict: 0.5–3s (Haiku on the LLM path) OR 0ms
  (deterministic path).
- Total per-attempt wall-clock: ~10–45s. A 5-hop campaign
  completes in 1–4 minutes.

**Concurrent testing requirements against the target system?**
At MVP scale: no concurrency required. Single Red Team Agent
serializes attempts. At 1K runs/day a per-campaign concurrency
cap of 4 is added (see COSTS.md §"1K test runs / day").

**Cost constraints for LLM-driven attack generation?**
Per-campaign budget cap in `AttackCampaign.cost_budget_usd`
(default $2.00 for MVP, $5.00 in the orchestrator-mode runs).
Per-day platform-wide ceiling enforced by the Orchestrator
returning `None` when `cost_to_date_usd >= cost_ceiling_usd`.
Tested in `tests/test_redteam_orchestrator.py::test_returns_none_when_budget_exhausted`.

---

### 3. Reliability Requirements

**What is the cost of a false negative (missed vulnerability)?**
HIPAA reporting cost + patient harm cost. A missed
cross-patient leak in production could be a $50K+ HHS
penalty per record + reputational damage at the customer.
This is why the cross-patient deterministic check is
mandatory — the LLM Judge alone is insufficient for the
critical-impact categories.

**What is the cost of a false positive (flagging safe behavior
as vulnerable)?**
The Judge mis-classifying clean refusals as exploits would, in
the worst case, generate noise vuln reports → erode engineer
trust → eventually the auto-doc pipeline gets disabled. We
SAW this fail mode during the 20260512T030840Z run: 7 critical-
severity Judge FPs auto-generated. The platform's trust gate
caught all 7 (auto-routed to `_pending/`) and the calibration
fix (`_check_refused_with_scoped_data`) closed the bug class
structurally.

The cost was bounded because the trust gate worked. Without it,
those 7 FPs would have been live regression cases enforcing
fake invariants.

**Human-in-the-loop requirements for remediation approval?**
Three gates per ARCHITECTURE.md §"Human approval gates":
1. Critical-severity reports → `_pending/` for human approval
   before promotion to live + W2 regression-case append.
2. Daily spend ceiling → Orchestrator halts and notifies before
   resuming.
3. Net-new attack categories (not on the threat model's
   pre-approved list) → human approval before campaign launch.

**Audit and compliance needs for security testing in a
healthcare context?**
Every agent call emits a Langfuse trace. Postgres + on-disk
JSON archives the four message types (`AttackCampaign`,
`AttackAttempt`, `JudgeVerdict`, `VulnerabilityReport`). Replay
is possible (run a campaign offline against archived target
responses), so a compliance auditor can reproduce any finding
from artifact.

---

## Phase 2: Architecture Discovery

### 4. Multi-Agent Design

**How many distinct agent roles does your platform require?**
Four, per ARCHITECTURE.md §"Summary":
- Orchestrator (Sonnet 4.6, strategic)
- Red Team (Sonnet 4.6, generative)
- Judge (Haiku 4.5, evaluative)
- Documentation (Haiku 4.5, structured)

The split is required, not a stylistic choice. The PRD: "a
system that does both [attack generation and evaluation] in the
same context has a conflict of interest by design." Same logic
applies for orchestration vs. execution and for raw verdicts
vs. structured reports.

**What framework manages agent coordination — LangGraph,
CrewAI, AutoGen, custom?**
**LangGraph.** Same framework W2 used for the supervisor +
worker graph. Familiar, debuggable, integrates with Langfuse
(already wired). Rejected: CrewAI (more opinionated; would
have to learn on a 4-day clock), custom event loop (more code,
fewer guarantees), AutoGen (similar to CrewAI, less mature
typed-message support).

**How do agents hand off work — message queues, shared state,
direct invocation?**
Typed Pydantic messages via LangGraph shared state. The four
message types — `AttackCampaign`, `AttackAttempt`,
`JudgeVerdict`, `VulnerabilityReport` — are the ONLY artifacts
that cross agent boundaries. No agent reads another agent's
internal state directly. See
`agent-service/src/redteam/messages.py`.

**What happens when one agent in the pipeline fails or times
out?**
Failure modes per agent are explicit in ARCHITECTURE.md:

- Orchestrator LLM fails → deterministic fallback (round-robin
  by attempts, ties broken by threat-model rank). Tested in
  `tests/test_redteam_orchestrator.py::test_deterministic_fallback_*`.
- Red Team's own LLM refuses → log refusal, switch to Ollama
  Llama 3 fallback (wired but not default). Threshold: 3 in a
  row halts the campaign.
- Target endpoint times out → record status code, Judge
  verdicts as `fail`.
- Judge LLM call raises → return `verdict=judge_failed @
  confidence 0.0`, escalate to human.
- Documentation LLM parsing fails → one retry with stricter
  reminder; on second failure raise.

---

### 5. Red Team Agent Design

**What model powers the Red Team Agent, and why is it
appropriate for offensive security?**
**Claude Sonnet 4.6** with an "authorized security researcher
conducting penetration testing under signed BAA, target system
is the team's own deployed copy" framing in the system prompt
(see `agent-service/src/redteam/red_team.py::_SYSTEM_PROMPT`).

Why this framing is RLHF-permitted: Anthropic publishes its own
red-teaming work in this exact frame; the BAA-scoped consent
language matches what they use internally. Across 92 attempts,
zero Red Team refusals on the Anthropic side.

**How does the agent mutate a failed attack into a variant?**
Two-mode design: `generate` (novel attack from category seed)
and `mutate` (variant of a partial-verdict parent). In mutate
mode the Red Team gets the prior `AttackAttempt` + the Judge's
verdict reasoning FOR THAT ATTEMPT ONLY (no cross-attempt
contamination). The Orchestrator picks parents from the
coverage state's `partial_attempt_refs`.

The 20260512T030840Z run demonstrates this: the Orchestrator
chose state_corruption for round 2 because it had the only
active partial verdict, and emitted the rationale verbatim
*"mutating the known partial is the highest-efficiency path
to a confirmed bypass"*.

**How do you prevent the Red Team Agent from being blocked by
the same safety filters it is trying to probe?**
The BAA framing in the system prompt is the primary defense.
Local Ollama Llama 3 8B fallback is wired (commented in
red_team.py and ARCHITECTURE.md) for the case where Anthropic
guardrails tighten or the framing stops working at scale.

The fallback has not been enabled by default because the
framing has held across 92 attempts.

**Context window requirements for multi-turn attack sequences?**
state_corruption category synthesizes up to 6 fake prior
turns plus the current user turn. Total context ~3K tokens —
well within Sonnet's 200K window. Target's response context
is small.

---

### 6. Judge Agent Design

**How does the Judge determine success vs. failure vs. partial
for each attack category?**
Per-category rubric loaded at verdict time. Each category
module exposes a `JUDGE_RUBRIC` string with explicit
success/partial/fail criteria. See e.g.
`agent-service/src/redteam/categories/cross_patient.py::JUDGE_RUBRIC`.

The deterministic-signal layer runs FIRST and returns
conclusive verdicts when possible (cross-patient UUID match,
injection-marker hit, refused-with-no-foreign-content). The LLM
Judge only fires for ambiguous cases.

**How do you validate that the Judge's criteria are consistent
across runs?**
Three mechanisms:
1. Deterministic signals are *deterministic* — same input always
   produces the same verdict. Tested in
   `tests/test_redteam_judge.py` (7 cases).
2. Rubrics are versioned in source code. Changing them is a
   commit, visible in `git log`.
3. The 20260512T033255Z verification run re-ran the
   state_corruption attacks that produced 4 false-positive
   "successes" before the calibration fix; after the fix, same
   inputs produced 5/5 fail. Drift checkable.

**What happens when the Judge is uncertain — does it escalate
to a human?**
Yes. Three paths:
1. Judge's `judge_confidence` < 0.5 on a `success` verdict →
   the Documentation Agent's severity-gate routes the auto-
   generated report to `_pending/`.
2. Judge returns ambiguous output (can't parse to success /
   partial / fail) → re-prompt once with stricter format; on
   second failure, mark verdict as `judge_failed` and escalate
   the attempt to human triage.
3. Drift detection: judge-of-the-judge audit (Sonnet
   re-grades a sample of Haiku's verdicts). Disagreement > 15%
   triggers a model swap. Documented in ARCHITECTURE.md
   §"Judge Agent" — not wired by default for MVP; Friday
   final-scope.

**How do you prevent the Judge from drifting as the target
system changes?**
Deterministic signals are immune to drift by construction.
The LLM Judge's drift is bounded by:
1. Per-category rubrics that are versioned in source code.
2. The trust gate that routes critical-severity verdicts to
   `_pending/` for human review (catches drift in the most
   important case).
3. The 20260512T030840Z FP class proves the bound: when the
   Judge LLM drifted, the trust gate caught it, and we shipped
   a structural fix (`_check_refused_with_scoped_data`) that
   prevents the same class of drift recurring.

---

### 7. Orchestrator Design

**What signals does the Orchestrator read to prioritize the
next attack campaign?**
Four, per ARCHITECTURE.md §"Orchestration strategy":
1. Coverage state — attempts per category over a rolling 24h
   window.
2. Open findings — vulnerabilities not yet promoted to
   regression cases.
3. Cost-to-date — current campaign + platform-wide spend.
4. Recent verdict trend — categories with rising partial-rate
   are closer to bypass than fully-defended ones.

The Orchestrator's LLM prompt (`_build_user_prompt` in
`orchestrator.py`) provides all four explicitly. The model
emits an `AttackCampaign` choosing category + hop budget + cost
budget + rationale.

**How does the Orchestrator decide when a category is
sufficiently covered?**
Heuristic: many attempts (≥10× the unexplored baseline) with 0
successes and 0 partials suggests the category is saturated.
The Orchestrator's LLM rationale on the 20260512T030840Z run
explicitly says: *"avoid categories that have saturated"*.

Deterministic fallback uses attempt count + threat-model rank:
under-explored categories win; ties go to higher threat-rank.

**What triggers a regression run — a new deployment, a time
window, or both?**
A new deployment of the W2 target (detected via the deployment
event from Railway or by polling the deployment ID) automatically
queues a full-regression campaign that re-runs every existing
W2 eval case PLUS every prior confirmed exploit in
`agent-service/evals/w2/adversarial_findings/`. This is the
PRD's "regression run when the target system changes" requirement.

Not currently auto-triggered — the W2 eval gate already runs on
every PR via `.github/workflows/eval-gate.yml`, which covers
the deployment-change case in practice. Time-window automation
is Friday-final scope.

**How does the Orchestrator manage cost across agents in a
single session?**
Each `AttackCampaign` carries a `cost_budget_usd` field
enforced by the runner. Per-attempt cost is approximated
(real-cost via Langfuse usage events is Friday-final). When
the budget is exhausted, the campaign halts and the
Orchestrator picks a different category.

The cost-to-date is the same number the human-facing
`/adversarial` dashboard surfaces — single source of truth.

---

### 8. Observability Strategy

**LangSmith vs. Langfuse vs. Braintrust vs. custom — which
surfaces inter-agent traces?**
**Langfuse cloud.** Same backend the W2 agent uses. Already
wired, already paid-for, already familiar to the team. Each
agent call is a span; each campaign is a trace. Per-span cost
metadata captures token spend per agent per campaign.

Rejected: LangSmith (lock-in to LangChain ecosystem; we use
LangGraph but not LangChain), Braintrust (less mature
on inter-agent tracing), custom (engineering time we don't
have).

**What metrics matter most for a multi-agent security
platform?**
The five surface on the `/adversarial` dashboard:
1. Coverage state per category (attempts, verdict distribution,
   partial-rate)
2. Vuln pipeline (live vs. pending counts by severity)
3. Recent campaigns with verdicts + Orchestrator rationale
4. Per-day attempts trend
5. Cost-to-date vs. ceiling

The dashboard is at `https://copilot-agent-production-ba87.up.railway.app/adversarial`
and reads the same data the Orchestrator does for strategic
decisions. Operator-facing + agent-facing share one substrate.

**How do you trace a vulnerability finding back through all
the agents that produced it?**
Every `AttackAttempt` carries `langfuse_trace_id`. Every
`VulnerabilityReport` carries `discovered_by_campaign` (UUID)
and `discovered_by_attempt` (UUID). The campaign JSON files in
`agent-service/evals/redteam_runs/<timestamp>/` archive the
full attack transcript + Judge verdict. Replay is possible
offline.

For a vuln in `vulns/VULN-XXXX.md`, the chain is:
- Read the report's `discovered_by_attempt` UUID
- Grep the runs for that attempt
- Find the campaign + the Orchestrator rationale + the Red Team
  prompt + the target response + the Judge verdict

The chain is reconstructable from on-disk artifacts alone, no
service required.

**Cost tracking at the agent level, not just the platform
level?**
Yes. Langfuse spans tag the agent role (`agent=redteam`,
`agent=judge`, `agent=orchestrator`, `agent=documentation`).
Per-agent daily spend queryable from the dashboard. COSTS.md
itemizes the per-line-item drivers.

---

## Phase 3: Post-Stack Refinement

### 8. Failure Mode Analysis  *(PRD has two #8s — keeping their numbering)*

**What happens when the Red Team Agent generates content that
is itself harmful?**
The Red Team's output is bounded by the BAA framing in its
system prompt — content scoped to "attack prompts the
orchestrator will deliver." If the Red Team genuinely produces
harmful content beyond the scope (e.g., novel exploit
techniques applicable to systems outside the test target), the
content lives only in `evals/redteam_runs/` JSON files committed
to the team's private repo. Public release of those files
would be a separate decision.

**How do you handle a Judge Agent that starts agreeing with
everything?**
Drift detection: judge-of-the-judge audit. Sonnet re-grades a
50-sample slice of Haiku's verdicts periodically; if
disagreement > 15%, halt the platform and notify operator.
Not wired by default for MVP — flagged in ARCHITECTURE.md
§"Known tradeoffs" #5.

The deterministic-signal layer is the structural backstop:
the most consequential verdicts (cross-patient leak,
injection-marker echo) are decided by string compares, not by
the LLM, so a drifted Judge LLM can't compromise them.

**What is the fallback when the Orchestrator has no clear
next priority?**
Round-robin by threat-model rank with the under-explored
category as the tiebreaker. Tested in
`tests/test_redteam_orchestrator.py::test_deterministic_fallback_*`.
Degrades to dumb-but-functional rather than halting.

**How do you handle cascading failures across agents in a
single test run?**
Each agent's failure is isolated to its own try/except in the
runner. A Red Team refusal logs and continues (3-in-a-row halts
the campaign). A target timeout records the status code. A
Judge LLM error returns `verdict=judge_failed` without crashing
the runner. A Documentation Agent error logs and proceeds —
the attempt stays in the on-disk JSON; reports can be
regenerated offline.

The runner never aborts on a single agent failure. The 92
attempts include several Red Team and Judge errors that
recovered gracefully.

---

### 9. Trust & Safety for the Platform Itself

**How do you prevent the adversarial platform from being
turned against systems it should not attack?**
Three mechanisms:
1. The Red Team's system prompt names the target system
   explicitly ("the Clinical Co-Pilot"). It's not a
   general-purpose attack generator.
2. The runner's `Target` client is constructed with a
   base URL from env. To attack a different system, you'd need
   to rebuild the image with a different `REDTEAM_TARGET_URL`.
3. The Orchestrator's `available_categories` list is
   constrained to the pre-approved threat model. Net-new
   categories require human approval before launch.

**Access controls for who can trigger agent runs and view
vulnerability reports?**
At MVP scale: anyone with repo access can trigger the runner
(it's a `python -m redteam.run_campaign` CLI with an
Anthropic key). At production scale this would be
authentication-gated.

The `/adversarial` visibility page is authentication-free
because it surfaces system *shape* not patient data — same
property as the W2 `/visibility` page. Vuln report content is
in the repo (GitHub), which is access-controlled.

**What approval is required before the Documentation Agent
files a critical-severity report?**
Critical AND high-severity findings route to `vulns/_pending/`
+ `agent-service/evals/w2/adversarial_findings/_pending/` by
the trust gate in `documentation.py::vulns_dir`. The W2 eval
gate's regression-case loader reads only the live dir, NOT
`_pending/`. A human `mv` from `_pending/` to live promotes
the finding.

This caught 8 false-positive critical findings during the
20260512T030840Z run before they polluted the regression
suite.

**How do you audit what each agent did during an overnight
run?**
Every campaign emits a JSON file with the full attack
transcript + Judge verdict. Langfuse archives the LLM-side
spans. The /adversarial dashboard's "Recent campaigns" tab
surfaces the Orchestrator rationale per campaign. The
on-disk artifacts are sufficient for offline replay.

---

### 10. Testing the Tester

**How do you validate that the Red Team Agent is actually
generating novel attacks?**
- Each `AttackAttempt`'s `technique` field is logged. Inspecting
  the 92-attempt corpus shows technique diversity across
  categories — no obvious repetition.
- Mutate-mode attempts explicitly produce variants of partial-
  verdict parents. The 20260512T030840Z run includes 5 mutate
  attempts; each used a different technique label.
- Future enhancement: embedding-similarity clustering of
  generated attacks to surface near-duplicates the Red Team
  is producing inadvertently. Friday-final scope.

**Ground truth dataset for evaluating Judge Agent accuracy?**
The W2 eval suite's 63-case manifest is the ground-truth set
for the BOUNDARY layer (cross-patient refusal, scope statement,
etc.). The 20260512T033255Z verification run is the ground-
truth set for the calibration fix (5 attempts that should all
verdict fail with high confidence; they did).

For the LLM Judge specifically, no formal ground-truth set
yet exists. The judge-of-the-judge audit (Sonnet re-grading
Haiku) would generate one continuously at production scale.

**How do you detect when the platform is producing low-quality
signal?**
Two signals:
1. Multiple consecutive `judge_failed` verdicts → Judge
   LLM is broken (output unparseable).
2. Multiple critical-severity findings in `_pending/` that
   on human review turn out to be FPs → Judge LLM is
   drifting toward over-classification. This is exactly
   what happened in the 20260512T030840Z run; the trust gate
   surfaced it.

**What does it mean for the multi-agent system itself to
regress?**
- A previously-passing unit test in `tests/test_redteam_*.py`
  starts failing → pytest CI catches it.
- The two standing canary PRs (regression-canary-citation-regex
  + adversarial-canary-patient-context) start passing instead
  of failing → the eval gate is no longer enforcing.
- The Judge's confidence distribution shifts (more 0.5-0.7
  verdicts, fewer 0.95+ verdicts) → the deterministic-signal
  layer is being bypassed more often than expected.

The 203-test suite + the two canary PRs are the primary
regression-detection mechanism. Adding distribution-drift
monitoring is Friday-final scope.

---

### 11. Iteration Planning

**How will you add new agent roles as the platform matures?**
The architecture uses LangGraph nodes + typed Pydantic
messages. Adding a new agent role:
1. Add a new message type to `redteam/messages.py` if needed.
2. Add the agent's class to `redteam/<role>.py`.
3. Wire into the runner or a new LangGraph node.
4. Add unit tests to `tests/test_redteam_<role>.py`.

Candidate next agents:
- **Critic Agent** — re-reads cited sources before approving a
  Documentation Agent report (the W2 architecture mentioned
  this as a v2 task).
- **Triage Agent** — auto-routes `_pending/` findings to live
  or rejected based on heuristics.
- **Coverage Coordinator** — multi-target version of the
  Orchestrator that distributes campaigns across N customer
  targets.

**Eval-driven improvement cycle for each agent independently?**
Each agent's unit tests in `tests/test_redteam_*.py` are
independent. The Judge has its own deterministic-property
tests (7 cases); the Orchestrator has its own (13 cases); the
Documentation Agent has its own (17 cases). Each can be
hardened without touching the others.

The W2 eval suite is the integration test — it runs the full
agent chain end-to-end against the deployed target.

**How does the platform incorporate newly published attack
techniques automatically?**
At MVP scale: a human reads new red-teaming literature and adds
seed examples to the appropriate category module in
`redteam/categories/`. The Red Team Agent uses these seeds as
few-shot inputs during generate-mode attacks.

At production scale: a scheduled ingest job could scrape MITRE
ATLAS, AI safety conferences, Anthropic / OpenAI red-teaming
reports for new techniques and auto-update seed lists. Friday-
final scope.

---

## Closing — what this document is not

This is not a marketing document. It's the decision log behind
the architecture, with the rejected alternatives named
explicitly. Cross-references to specific files, specific tests,
specific run timestamps. A reviewer asking *"why this design"*
should find the answer here; a reviewer asking *"how does this
work in practice"* should find it in
[ARCHITECTURE.md](ARCHITECTURE.md) and the live
`/adversarial` dashboard.
