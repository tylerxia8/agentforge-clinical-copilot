# ARCHITECTURE.md — AgentForge Adversarial Platform (W3)

> Multi-agent adversarial evaluation platform that continuously
> attacks the **AgentForge Clinical Co-Pilot** (W2 target deployed
> at https://openemr-production-0996.up.railway.app/ and
> https://copilot-agent-production-ba87.up.railway.app/). Designed
> to satisfy the W3 PRD requirement that a single-agent or pipeline
> architecture does *not* count — each role below is a distinct
> agent with its own context, decision authority, and trust level.
>
> The W1 architecture plan (the AI-integration plan for the
> Clinical Co-Pilot itself) lives at [W1_ARCHITECTURE.md](W1_ARCHITECTURE.md).
> The W2 architecture (multimodal evidence agent, hybrid RAG,
> verification layers) lives at [W2_ARCHITECTURE.md](W2_ARCHITECTURE.md).
> This document is the W3 platform that attacks them.

---

## W3 architecture requirement — compliance map

The PRD's architecture requirement: *"A single-agent or pipeline
architecture does not satisfy this assignment. Each role below
represents a distinct agent with its own responsibilities,
context, and decision-making authority. Your ARCHITECTURE.md
must define each agent, its inputs and outputs, its trust level,
and how it coordinates with the others."*

**Four distinct agents, each fully specified below:**

| Agent | Responsibilities | Inputs | Outputs | Trust level | Coordination |
|---|---|---|---|---|---|
| **Orchestrator** ([§"Orchestrator Agent"](#orchestrator-agent)) | Strategic prioritization — picks the next campaign based on coverage state | Coverage state, open findings, cost-to-date, verdict trend | `AttackCampaign` | **Highest** — strategic decisions + budget allocation | Emits typed Pydantic message to Red Team |
| **Red Team** ([§"Red Team Agent"](#red-team-agent)) | Generates concrete attacks (generate + mutate modes) | `AttackCampaign`, optional parent `AttackAttempt` for mutate | `AttackAttempt` | **Low** — explicitly *untrusted*; output can only reach platform via the Judge | HTTP to deployed target; emits Pydantic message to Judge |
| **Judge** ([§"Judge Agent"](#judge-agent)) | Verdict each attempt (success / partial / fail) | `AttackAttempt`, category-specific rubric, deterministic signal results | `JudgeVerdict` | **Medium-high** — cascades downstream; verdicts drive regression cases | LangGraph state routes on `verdict` value (fail → Orchestrator; success/partial → Documentation) |
| **Documentation** ([§"Documentation Agent"](#documentation-agent)) | Convert confirmed exploits to vuln reports + W2 eval-case sidecars | `JudgeVerdict` (verdict ≥ partial), `AttackAttempt` transcript | `VulnerabilityReport` + JSON sidecar at `evals/w2/adversarial_findings/` | **Medium** for low/medium severity; **human-gated** for high/critical | Writes to `vulns/` or `vulns/_pending/` based on severity; W2 eval gate picks up live findings on next CI run |

**The agents are structurally distinct, not roles in a pipeline:**

- **Different model families per role** — Orchestrator + Red Team on Sonnet 4.6; Judge + Documentation on Haiku 4.5. Same model for Judge AND Red Team would compromise the conflict-of-interest firewall.
- **Independent contexts** — the four Pydantic message types in [`agent-service/src/redteam/messages.py`](agent-service/src/redteam/messages.py) are the *only* artifacts that cross agent boundaries. No agent reads another agent's internal state.
- **Different decision authority** — Orchestrator picks campaigns; Red Team generates but doesn't decide what to launch; Judge verdicts but doesn't decide what to document; Documentation files but doesn't decide what's a vulnerability.

**Conflict-of-interest firewall enforced at the message-shape
level:** the `AttackAttempt` Pydantic model literally does not
expose the Red Team's reasoning chain to the Judge. The type
system prevents the most obvious bypass.

The full ASCII diagram of the coordination flow is in
[§"System overview — diagram"](#system-overview--diagram) below.

---

## Summary

The platform is a **four-agent system** built on LangGraph (the same
orchestration framework W2 used), traced through Langfuse (the same
observability backbone), and persisting state to Redis + Postgres
(both already provisioned on Railway from W2). The four agents are
deliberately separated by responsibility because **attack generation
and attack evaluation in the same context is compromised by design**
— a Red Team Agent that judges its own attacks always finds them
successful. The four roles:

- **Orchestrator Agent** (Sonnet 4.6) reads the current state of the
  platform — coverage by category, open findings, recent verdict
  distribution, cost-to-date — and decides *what to attack next*. It
  is the platform's strategic layer. Output is an `AttackCampaign`
  message specifying category, seed payload (if any), hop budget,
  and termination criteria.
- **Red Team Agent** (Sonnet 4.6 with "authorized security researcher
  conducting penetration testing under signed BAA" framing) takes a
  campaign and generates concrete attacks against the live W2 target.
  It runs in two modes: **generate** (novel attack from seed) and
  **mutate** (variant of a partially-successful prior attempt
  surfaced by the Judge). Output is an `AttackAttempt` containing the
  prompt sequence, the target response, and metadata.
- **Judge Agent** (Haiku 4.5, deliberately a *different model size*
  from the Red Team to reduce same-context correlation) evaluates
  each `AttackAttempt` against a category-specific success rubric
  and returns a `JudgeVerdict` with one of three values: `success`,
  `partial`, or `fail`. Independent of attacker by hard rule —
  Judge has no access to Red Team's reasoning or prior attempts
  within the campaign.
- **Documentation Agent** (Haiku 4.5) takes confirmed exploits
  (verdict ≥ `partial`) and produces a structured `VulnerabilityReport`
  in the format required by the PRD: unique ID, severity, clinical
  impact, minimal reproduction, observed vs. expected, recommended
  remediation, validation history. The report is also auto-converted
  into a new W2 eval case appended to the existing 63-case suite,
  so the W2 eval gate becomes the regression harness W3 requires.

**Inter-agent communication** is via LangGraph shared state, with
each transition emitting a Langfuse span. Messages are Pydantic
models (`AttackCampaign`, `AttackAttempt`, `JudgeVerdict`,
`VulnerabilityReport`) — typed, validated, persisted to Postgres
for replay. No shared mutable state outside the typed message
envelope; each agent reads only its declared inputs.

**Where humans gate the system.** Three explicit human approval
points: (1) critical-severity vulnerability reports require human
sign-off before they're filed publicly or trigger remediation
workflows, (2) a daily cost-spend ceiling pauses the platform and
notifies before resuming, and (3) net-new attack categories (not
on the threat model's pre-approved list) require human approval
before the Orchestrator can launch a campaign against them.
Everything else runs autonomously.

**Where AI is used vs. deterministic.** AI for: novel attack
generation, attack mutation, verdict reasoning on ambiguous outputs,
vulnerability-report prose. Deterministic for: routing between
agents (LangGraph state machine, no LLM in the loop), cost-budget
enforcement, regression-case insertion into the eval suite, signature
matching for high-confidence exploit categories (e.g.,
cross-patient UUID leak is a string-compare verdict, not an LLM
verdict). The bias is **deterministic where the decision is
unambiguous, AI where it isn't** — same pattern W2 used for its
supervisor.

**Cost handling at scale.** Per-campaign LLM budget cap enforced by
the Orchestrator. Per-day platform-wide spend ceiling enforced
externally. Local model fallback (Ollama Llama 3) ready to swap
into the Red Team role if Anthropic's safety filters refuse the
attack-research framing under sustained load. Halt-on-no-signal
heuristic: if the Judge returns N consecutive `fail` verdicts for
the same campaign, the Orchestrator concludes the category is
sufficiently defended and rotates to a different surface rather
than burning tokens.

---

## System overview — diagram

```
                          ┌────────────────────────────────┐
                          │      ORCHESTRATOR AGENT        │
                          │      (Sonnet 4.6, strategic)   │
                          │                                │
                          │   reads: coverage, findings,   │
                          │          cost, verdict trend   │
                          │   emits: AttackCampaign        │
                          └──────────────┬─────────────────┘
                                         │
                              ┌──────────┴──────────┐
                              │   AttackCampaign    │
                              │   { category,       │
                              │     seed_payload,   │
                              │     hop_budget,     │
                              │     stop_criteria } │
                              └──────────┬──────────┘
                                         │
                                         v
                          ┌────────────────────────────────┐
                          │       RED TEAM AGENT           │
                          │   (Sonnet 4.6, authorized      │
                          │    security-researcher frame)  │
                          │                                │
                          │   mode: generate | mutate      │
                          │   emits: AttackAttempt         │
                          └──────────────┬─────────────────┘
                                         │
                                         │ attack prompts
                                         v
                      ╔═══════════════════════════════════════╗
                      ║   DEPLOYED W2 TARGET (the Co-Pilot)   ║
                      ║                                       ║
                      ║   - /agent/chat   (HMAC-authed)       ║
                      ║   - /demo/chat    (token-less)        ║
                      ║   - /agent/extract (multi-format)     ║
                      ║   - /agent/warm    (context cache)    ║
                      ╚═══════════════════╤═══════════════════╝
                                          │ responses, status codes,
                                          │ Langfuse trace IDs
                                          v
                          ┌────────────────────────────────┐
                          │        JUDGE AGENT             │
                          │     (Haiku 4.5, independent)   │
                          │                                │
                          │   rubric: category-specific    │
                          │   verdict: success / partial / │
                          │            fail                │
                          │   no access to Red Team's      │
                          │   reasoning chain              │
                          └──────────────┬─────────────────┘
                                         │
                              ┌──────────┴──────────┐
                              │   JudgeVerdict      │
                              │   { verdict,        │
                              │     reasoning,      │
                              │     severity_hint } │
                              └──────────┬──────────┘
                                         │
                            ┌────────────┴───────────┐
                            │                        │
                  verdict = fail              verdict in
                            │              {success, partial}
                            v                        │
                  back to Orchestrator               v
                  (mutate or rotate)    ┌────────────────────────┐
                                        │  DOCUMENTATION AGENT   │
                                        │  (Haiku 4.5, template) │
                                        │                        │
                                        │  emits:                │
                                        │  - VulnerabilityReport │
                                        │  - W2 eval case (new)  │
                                        └──────────┬─────────────┘
                                                   │
                                ┌──────────────────┴─────────────────┐
                                │                                    │
                                v                                    v
                  ┌──────────────────────────┐      ┌───────────────────────────┐
                  │  vulns/VULN-XXX.md       │      │  agent-service/evals/w2/  │
                  │  (human-readable report) │      │  cases.py (regression     │
                  │  → severity ≥ critical:  │      │   case appended; W2 eval  │
                  │    HUMAN APPROVAL gate   │      │   gate now blocks regress)│
                  └──────────────────────────┘      └───────────────────────────┘

  Cross-cutting:
    ── Langfuse spans on every agent call → Orchestrator reads aggregate
    ── Per-agent + per-campaign cost tracking
    ── Daily spend ceiling enforced before any agent invocation
    ── /visibility page (extended from W2) shows campaign status,
       verdict counts, vuln pipeline, coverage heatmap
```

---

## The four agents

### Orchestrator Agent

**Role.** Strategic layer. Decides *what* to attack next based on
the current state of the platform — coverage gaps, severity of open
findings, cost-to-date, recent verdict trend. Without this layer,
the platform runs attacks randomly; with it, the platform learns
where the W2 target's defenses are weakest and concentrates effort
there.

**Inputs.**
- Coverage state: which threat-model categories have how many
  attempts in the last N hours, with what verdict distribution
- Open findings: list of vulnerabilities currently in the pipeline
  (discovered → reported → fixed → validated)
- Cost-to-date: spend for the current campaign window
- Recent verdict trend: are we discovering new findings or
  re-discovering old ones (signal of coverage saturation)
- Threat model: `THREAT_MODEL.md` parsed for pre-approved
  categories and prioritization

**Outputs.** An `AttackCampaign` Pydantic object:
```python
class AttackCampaign(BaseModel):
    campaign_id: UUID
    category: ThreatCategory          # enum from THREAT_MODEL.md
    seed_payload: str | None          # for "mutate" mode
    hop_budget: int                   # max Red Team attempts before halt
    cost_budget_usd: float            # USD ceiling for this campaign
    stop_criteria: StopCriteria       # e.g., "halt after 3 consecutive fails"
    rationale: str                    # why this campaign now (LLM output)
```

**Trust level.** Highest. The Orchestrator can launch campaigns,
allocate budgets, and rotate priorities autonomously. It cannot:
file vulnerability reports (Documentation Agent's job, with human
gate for critical), exceed the daily spend ceiling (external
guard), or launch campaigns against attack categories not in the
pre-approved threat model.

**Model.** Sonnet 4.6. Strategic prioritization is real reasoning
— what an LLM does better than a heuristic. The same model W2's
agent uses, so the cost profile and rate limits are already
characterized.

**Failure modes & recovery.** (a) If the Orchestrator's LLM call
fails or returns invalid JSON, fall back to a deterministic
priority queue (round-robin across categories with weight by
threat-model rank). (b) If the Orchestrator picks a campaign
whose Red Team execution then errors out repeatedly, the platform
halts and emits an alert — three errors in a row on the same
campaign is treated as a Red Team infrastructure failure, not a
prompt-engineering problem.

### Red Team Agent

**Role.** Executes the campaign — generates attacks against the
live W2 target. Two operating modes:

1. **Generate mode.** Given a category and (optionally) a seed
   payload from the threat model, produce a novel attack. Output
   is a sequence of one or more user-side messages, optionally
   with attached document content for indirect-injection campaigns.

2. **Mutate mode.** Given a prior `AttackAttempt` that the Judge
   verdict'd as `partial`, produce a variant that probes for the
   actual bypass. The Red Team gets the prior prompt, the target's
   response, and the Judge's reasoning *for the partial verdict*
   (but **not** the Judge's reasoning for any other attempts, to
   prevent cross-attempt contamination).

**Inputs.**
- `AttackCampaign` from Orchestrator
- W2 target endpoint URLs + auth
- Threat model category description (the relevant subsection of
  `THREAT_MODEL.md`)
- For mutate mode: prior `AttackAttempt` + Judge verdict

**Outputs.** An `AttackAttempt` Pydantic object:
```python
class AttackAttempt(BaseModel):
    attempt_id: UUID
    campaign_id: UUID
    mode: Literal["generate", "mutate"]
    parent_attempt_id: UUID | None    # for mutate
    messages: list[ChatMessage]       # the attack sequence
    uploaded_documents: list[DocumentPayload]  # for indirect injection
    target_responses: list[ChatMessage]
    target_status_codes: list[int]
    langfuse_trace_id: str
    cost_usd: float
    timestamp: datetime
```

**Trust level.** Low — explicitly *untrusted*. The Red Team's
output cannot affect platform behavior except through the Judge.
The platform reads every Red Team output through the Judge's
verdict, not directly.

**Model.** Sonnet 4.6 with an "authorized security researcher
conducting penetration testing under signed BAA, target system is
my own, deployed for this purpose" framing in the system prompt.
This framing is RLHF-permitted on Claude — Anthropic publishes
its own red-teaming work. If sustained refusals appear during
campaigns (Judge verdicts of `fail` because the model refused to
*generate* an attack, not because the target refused to act on
one), fall back to a **local Ollama Llama 3 8B** instance for the
Red Team role. The fallback is wired but not enabled by default
— flagged as a Wed/Thu task if the framing turns out to be
brittle.

**Failure modes & recovery.** (a) Target endpoint times out →
record `target_status_codes=[504]` and emit attempt for Judge to
verdict as `fail`. (b) Red Team's own LLM refuses to generate
("I can't help with creating attacks") → log refusal as
infrastructure signal, mark attempt as `red_team_refused`, switch
to fallback model for subsequent attempts in the campaign. (c)
Anthropic rate-limit → exponential backoff with jitter; if total
campaign wait exceeds 5 minutes, halt the campaign.

### Judge Agent

**Role.** Evaluates `AttackAttempt`s against category-specific
success rubrics. Returns one of `success` / `partial` / `fail`
per attempt. **Independent of the attacker** — Judge has no
access to Red Team's reasoning or prior attempts within the same
campaign. This is the conflict-of-interest firewall.

**Inputs.**
- `AttackAttempt` (specifically: the messages and target_responses
  fields; **not** the Red Team's internal reasoning or campaign
  rationale)
- Category-specific rubric (a structured prompt template per
  threat category)
- Ground-truth indicators where deterministic: for cross-patient
  attacks, the Judge has access to the patient_uuid being
  attacked AND the target's response — string-comparing whether
  a *different* patient's UUID appeared is a deterministic check,
  not an LLM judgment

**Outputs.** A `JudgeVerdict`:
```python
class JudgeVerdict(BaseModel):
    attempt_id: UUID
    verdict: Literal["success", "partial", "fail"]
    reasoning: str
    severity_hint: Literal["low", "medium", "high", "critical"]
    deterministic_signals: dict      # e.g., {"cross_patient_uuid_found": True}
    judge_confidence: float          # 0.0 - 1.0
```

**Trust level.** Medium-high. The Judge's verdicts drive everything
downstream — regression-case generation, vuln reports, Orchestrator
prioritization — so the Judge being wrong has cascading effects.
We control for this two ways: (a) deterministic signals override
LLM reasoning where they exist (UUID match for cross-patient,
regex match for PHI pattern, string presence for forbidden
phrases), and (b) a periodic **judge-of-the-judge** audit (Sonnet
re-grades a sample of Haiku's verdicts and we measure
disagreement rate; a drift threshold triggers a model swap or
rubric revision).

**Model.** Haiku 4.5. Smaller model than the Red Team (both
Anthropic, but different sizes and training data; the more
important property is they're separate inference contexts).
Haiku's cost profile makes it sustainable for high-volume
verdicting — at ~$0.001 per verdict we can run 10,000 verdicts
for $10, vs. ~$100+ on Sonnet.

**Failure modes & recovery.** (a) Judge returns ambiguous output
(can't parse to `success | partial | fail`) → re-prompt once
with stricter format; on second failure, mark verdict as
`judge_failed` and escalate to human. (b) Judge confidence < 0.5
on a `success` verdict → flag for human review before
Documentation Agent runs. (c) Drift detection: if judge-of-the-
judge disagreement exceeds 15% on a 50-sample audit, halt the
platform and notify operator.

### Documentation Agent

**Role.** Converts confirmed exploits (verdict ≥ `partial`) into
two artifacts: (1) a human-readable `VulnerabilityReport` in
`vulns/VULN-XXXX.md` and (2) a new regression case appended to
the W2 eval suite at `agent-service/evals/w2/cases.py`. The
*same exploit* lives in both forms — one for human consumption
by a security engineer who wasn't present when it was found, one
as an automated test that runs on every PR going forward.

**Inputs.**
- `JudgeVerdict` (with `verdict in {success, partial}`)
- The associated `AttackAttempt` (full transcript)
- Existing vuln corpus (to assign next vuln ID and check for
  duplicates against prior reports)

**Outputs.**
- `VulnerabilityReport` Pydantic object → rendered to
  `vulns/VULN-XXXX.md` with PRD-required fields:
  - unique ID + severity rating
  - clear description + clinical impact
  - minimal reproducible attack sequence
  - observed vs. expected behavior
  - recommended remediation
  - status + fix-validation history
- `W2EvalCase` Pydantic object → appended to `cases.py` in the
  appropriate category (e.g., `boundary`, `phi_logs`,
  `fabrication`), with rubrics that fail when the vulnerability
  is present and pass when it's fixed

**Trust level.** Medium for non-critical findings (autonomous
report filing + autonomous eval-case insertion); **gated by
human approval** for critical-severity findings before either
artifact is committed to the repo.

**Model.** Haiku 4.5. Template filling and structured prose are
Haiku's sweet spot. Cost-efficient since the Documentation Agent
runs once per confirmed exploit, not once per attempt.

**Failure modes & recovery.** (a) Duplicate detection — if the
new attempt is materially identical to a prior reported vuln,
the Documentation Agent appends a `VARIANT_OF: VULN-XXXX` field
instead of generating a new report; the W2 eval case is also
not appended (the existing case already guards). (b) Schema
validation failure on the eval case insert → halt the case
addition, raise alert, leave the vuln report standalone.

---

## Inter-agent communication

**Framework.** LangGraph, reusing W2's worker-graph plumbing.
Each agent is a node in a directed graph; transitions are
state-driven (a `JudgeVerdict.verdict` value routes to either
Documentation or back to Orchestrator). LangGraph manages
agent state machine + retries + checkpointing.

**Message format.** Strict Pydantic models for every transition.
The four message types — `AttackCampaign`, `AttackAttempt`,
`JudgeVerdict`, `VulnerabilityReport` — are the *only* artifacts
that cross agent boundaries. No agent reads another agent's
internal state directly.

**Persistence.** Every message is written to Postgres via
SQLAlchemy on emission, with the Langfuse trace ID linking
across agents. This serves three purposes: (a) replay (run a
campaign offline against archived target responses), (b) audit
(prove the Judge didn't see something it shouldn't have), (c)
training signal (offline analysis of which Red Team prompts led
to which verdicts).

**Communication style — handoff, not shared mutable state.** An
agent's run produces an immutable message; the next agent
reads that message and produces its own. No agent mutates an
upstream agent's output. This matches the W2 worker-graph
pattern and keeps reasoning isolated.

---

## Orchestration strategy — how the Orchestrator prioritizes

The Orchestrator reads four signals each tick:

1. **Coverage state.** For each threat category in
   `THREAT_MODEL.md`, the count of attempts in the rolling 24h
   window. Categories with < N attempts are under-explored;
   categories with > 10N attempts and 0 successes are likely
   defended.
2. **Open findings.** Vulnerabilities in the pipeline that
   haven't been fixed yet. The Orchestrator can prioritize
   *variants* of open findings (mutate mode) to confirm
   whether the underlying weakness is broader than the original
   discovery suggests.
3. **Cost-to-date.** Current campaign's spend vs. its budget,
   and platform-wide daily spend vs. ceiling.
4. **Recent verdict trend.** A category that's producing
   `partial` verdicts at increasing frequency is *closer* to a
   bypass; one that's producing only `fail` verdicts is
   converging on "defended."

The Orchestrator's prompt asks Sonnet to produce an
`AttackCampaign` that maximizes expected information gain per
dollar. Hyperparameters (the weight on coverage vs. exploit
chasing vs. cost) are tunable in `agent-service/src/redteam/
orchestrator.py`.

Regression triggers: a new deployment of the W2 target (detected
by Railway webhook or by polling the deployment ID) automatically
queues a **full-regression campaign** that re-runs every existing
W2 eval case PLUS every prior confirmed exploit. This is the
PRD's "regression run when the target system changes" requirement.

---

## Regression & validation harness

The harness is **the existing W2 eval suite at
`agent-service/evals/w2/`**, extended with adversarial cases
appended by the Documentation Agent. This reuses ~80% of
existing infrastructure:

- **Storage.** Cases live in `agent-service/evals/w2/cases.py`
  as Python `W2Case` dataclasses. New adversarial findings get
  appended in the relevant category. Versioned via git.
- **Queryability.** The runner already exposes per-category and
  per-rubric pass-rate breakdowns; the W3 `/visibility` extension
  filters by `discovered_in_w3=True` to surface adversarial
  cases specifically.
- **Automated execution.** The W2 eval gate
  (`.github/workflows/eval-gate.yml`) already runs the suite on
  every PR. With the new adversarial cases included, any future
  PR that re-introduces a discovered vulnerability fails the
  gate.
- **Cross-regression detection.** The eval-gate's per-category
  pass-rate comparison flags when a fix in one category drops a
  rate in another. Already wired (see
  [W2_ARCHITECTURE.md §10](W2_ARCHITECTURE.md)).
- **Fix validation.** When a vulnerability is fixed, the
  associated eval case should now *pass*. The Documentation
  Agent appends a `FIX_VALIDATED_AT: <commit>` line to the
  `VulnerabilityReport` based on the eval case's first green run
  on main.

**What a "test passing" means here, carefully.** The PRD warns
that a test that passes because the model's behavior changed —
not because the vulnerability was fixed — is worse than no test.
We address this two ways: (a) regression cases assert on
*deterministic signals* where possible (presence of a different
patient's UUID in the response, presence of a forbidden phrase),
not LLM-judged faithfulness, and (b) the Judge Agent's
`judge_confidence` field is logged with every verdict; a case
that "passes" with `judge_confidence < 0.7` is flagged for
human review rather than auto-marked as validated.

---

## Observability layer

The PRD is explicit that observability is not just for humans —
it's the data substrate the Orchestrator reads. Three layers:

**Layer 1 — Langfuse traces.** Every agent call is a span;
every campaign is a trace. The Langfuse dashboard already in
use for W2 (https://us.cloud.langfuse.com/) extends naturally to
the four W3 agents. Per-span cost metadata captures token spend
per agent per campaign, which the Orchestrator reads for budget
enforcement.

**Layer 2 — Postgres / Redis state.** All four message types are
persisted. Queries:
- "How many cross-patient attempts in the last 24h, and what
  fraction were `success`?" → SQL aggregate
- "Show all `partial` verdicts that haven't been mutated yet" →
  candidates for the Orchestrator's next mutate-mode campaign
- "List all open vulnerabilities sorted by severity" → human
  triage view

**Layer 3 — `/visibility` page extension.** The W2 visibility
page (https://copilot-agent-production-ba87.up.railway.app/visibility)
gets four new tabs:
- **Campaign status.** Active campaigns, recent verdicts, cost
- **Vuln pipeline.** Open / triaged / fixed / validated counts
- **Coverage heatmap.** Threat-category × time-window matrix
- **Per-agent cost.** Spend per agent per day, with budget bars

The Orchestrator reads layer 2; humans read all three.

### Layer 4 (W4 addition) — in-process SignalBus + live monitor

W3-final grader feedback identified that the three layers above
were observability-driven *in spirit* (the Orchestrator reads
observability data) but the access pattern was **snapshot-at-
start-of-round**, not **react-to-live-signal**. W4 addresses
this with a fourth layer that sits *between* the runner and the
Orchestrator:

- **`SignalBus`** (`agent-service/src/redteam/signals.py`) — in-
  process pub/sub that publishes typed `SignalEvent` records on
  five canonical event types: `campaign.started`,
  `attempt.recorded`, `verdict.delivered`, `campaign.ended`, and
  `orchestrator.decided`. Every event is also appended as one
  JSON line to `<run-dir>/signals.jsonl` so the dashboard and
  any out-of-process subscriber can tail the stream.
- **`CoverageMonitor`** — a subscriber that maintains rolling-
  window verdict-distribution stats per category and detects
  *shifts* against a baseline snapshot the Orchestrator pins at
  the start of each round. `recent_shifts()` returns the
  signal the Orchestrator reads — categories whose
  verdict distribution moved past a configurable threshold in
  the last N events.
- **Orchestrator integration** — the LLM prompt grows a
  `# Live signal stream` section listing those shifts in
  human-readable form, and the deterministic fallback gains a
  live-signal override that picks a freshly-gaining-traction
  category over the default lowest-attempt-count default. The
  `orchestrator.decided` event carries `used_live_signals` +
  `shifts_consulted`, which the dashboard's
  **Live signal stream** tab surfaces — that column IS the proof
  the autonomy hook influenced the decision, not the prompt
  alone.

The architectural payoff of doing the bus first is that the
*next* observability-driven loop is a new subscriber on the
same bus, not a new infrastructure layer. **W4 v2 + v3 cash
this claim three times — orchestration, Judge consistency,
replay automation — with three independent subscribers on the
single bus.** v2:

- **`JudgeDriftMonitor`** — also in `signals.py`. Subscribes to
  the same `verdict.delivered` events the `CoverageMonitor`
  reads, but does a different observation: rolling-window mean
  LLM-Judge confidence per category, with drift detection
  against a baseline pinned at each Orchestrator round.
  Deterministic-shortcut verdicts (confidence == 1.0) are
  excluded from the drift math — they would mask actual LLM
  calibration drift. When the mean of post-baseline samples
  diverges from the baseline mean by more than the configured
  threshold, the monitor surfaces a `JudgeDriftSignal` to the
  operator dashboard (no auto-routing yet — the response is
  human-mediated).

v3 adds the third subscriber:

- **`ReplaySubscriber`** (in `agent-service/src/redteam/replay.py`).
  Subscribes to a new `deploy.fired` event type. When fired,
  loads every confirmed regression case from
  `agent-service/evals/w2/adversarial_findings/`, fires each
  one against the just-deployed target via the same transport
  the W2 eval suite uses (`evals.w2.transport.chat`), grades
  per case using the existing W2 rubric functions, and emits a
  full event sequence (`replay.started` →
  `replay.case.evaluated*` → `replay.completed`). The trigger
  surface today is `POST /adversarial/admin/replay`, gated by
  the `REPLAY_ADMIN_TOKEN` env var; the next step (W4 v4) is
  wiring Railway's deploy-complete webhook to the same endpoint
  so the autonomy loop closes without operator intervention.

Open subscribers still to ship: a real Railway webhook trigger
for `deploy.fired`, an auto-promote-on-green subscriber that
moves pending findings to live when their regression case
passes, intra-campaign hop mutation (the runner consults the
bus mid-campaign rather than only between campaigns), and
cross-run baseline persistence for the `JudgeDriftMonitor` so
calibration drift over weeks shows up alongside drift within
a single run.

---

## Human approval gates

Three gates, explicit and minimal:

1. **Critical-severity vulnerability reports.** Before the
   Documentation Agent writes a critical-severity vuln to the
   repo, a human must approve via a CLI command (or `gh pr
   review` if the report comes through as a PR). Reason: a
   confident false-positive on a critical-severity report wastes
   engineering time and may trigger remediation workflows
   inappropriately.
2. **Daily spend ceiling.** Platform-wide spend > $X/day pauses
   the Orchestrator. Resume requires explicit human approval.
   Reason: cost over-run is the failure mode the PRD called out
   most directly.
3. **Net-new attack categories.** If the Orchestrator wants to
   attack a category not in `THREAT_MODEL.md`, that's a scope
   change and requires human sign-off before the campaign can
   launch. Reason: keeps the platform's blast radius bounded to
   the pre-modeled surface.

Everything else — campaign launches within approved categories,
attack generation and mutation, judge verdicts, non-critical
vuln reports, eval-case insertion — runs autonomously.

---

## AI vs. deterministic decision-making

| Function | AI or Deterministic | Justification |
|---|---|---|
| Routing between agents | Deterministic | LangGraph state machine; routing on a verdict value is unambiguous |
| Cost-budget enforcement | Deterministic | Numbers, not reasoning |
| Daily spend ceiling | Deterministic | Same |
| Attack generation | AI | Creativity / novelty is the point |
| Attack mutation | AI | Same |
| Judge verdict — when deterministic signal exists | Deterministic | Cross-patient UUID match is a string compare, not a judgment |
| Judge verdict — ambiguous cases | AI | Required by PRD; bounded by rubric |
| Vulnerability-report prose | AI | Quality of writing matters; templates fill the bones |
| Eval-case rubric definition | Deterministic | Reuse W2 rubric types; the new case picks one |
| Duplicate-vuln detection | Hybrid | Embedding similarity (deterministic) + LLM tie-break (AI) |
| Coverage analytics | Deterministic | SQL aggregates |
| Drift-detection on Judge | Hybrid | Sample audit (deterministic selection) + LLM regrade (AI) |

**Bias.** Deterministic where the decision is unambiguous, AI
where it isn't. Same pattern W2 used. This minimizes the AI
surface (cheaper, more reproducible, easier to debug) while
preserving AI for the genuinely creative tasks (attack generation,
vuln-report prose).

---

## Cost, scale, and model constraints at scale

| Volume | Architectural change required |
|---|---|
| 100 test runs/day | Current architecture. ~$5-10/day. No changes needed. |
| 1K test runs/day | Same architecture, watch the Anthropic per-minute rate limit. ~$50-100/day. Add per-campaign concurrency cap (max 4 simultaneous campaigns). |
| 10K test runs/day | Switch Red Team to local Ollama Llama 3 (or Mistral) for the bulk of attack generation; keep Sonnet for novel-category work and mutate mode where quality matters. ~$200/day (mostly Sonnet+Haiku judge). Per-campaign budget caps become load-bearing. |
| 100K test runs/day | Fundamental shift: deterministic attack libraries replace some LLM generation entirely (mutation patterns extracted from successful Red Team outputs become regex-driven generators). Most campaigns no longer require LLMs. Local model for Red Team handles the rest. ~$500-800/day. The Anthropic line items are now the Judge (Haiku, scaled) and the Orchestrator (Sonnet, low volume). |

**Detailed cost analysis** (per-token, per-attempt, per-campaign)
lives in `COSTS.md` for the W3 final submission.

**Rate-limit handling.** Anthropic per-org TPM is the binding
ceiling. The platform's exponential backoff + per-campaign
concurrency cap keeps us inside it. If we hit it sustained, the
Orchestrator's "no signal" heuristic activates and rotates work
toward lower-cost campaigns (the local-model Red Team paths).

**Model constraints.** Sonnet's RLHF includes some refusal of
offensive workflows; we handle this via the "authorized
researcher under BAA" framing in the system prompt, which has
held in W2's existing 7-case adversarial eval category. If
sustained refusals appear in W3 campaigns, fallback to local
Llama 3 is wired but not default.

---

## Known tradeoffs (PRD-defensible)

**1. Reusing the W2 eval suite as the regression harness.** We
gain ~80% infrastructure reuse — but we inherit the W2 suite's
limitations (Python-only, runs synchronously, ~10 min wall-clock
for 63 cases). Alternative: build a separate adversarial-only
harness. Rejected because the regression *guarantee* matters most
when adversarial findings live alongside W2 cases in the same
gate.

**2. Single LLM vendor (Anthropic) for three of four agents.**
Operational simplicity, BAA already signed, observability stack
already wired. The risk is correlated failure (if Anthropic
updates guardrails to refuse our Red Team framing, three agents
go down together). Local Ollama fallback for Red Team is wired
to mitigate the worst case.

**3. LangGraph for orchestration.** Same framework as W2.
Familiar, debuggable, integrates with Langfuse. The risk is
inheriting any LangGraph reliability quirks. Alternative
considered: CrewAI (more opinionated; would have to learn it on
a 4-day clock) and custom event loop (more code, fewer
guarantees). LangGraph wins on schedule.

**4. Heuristic + deterministic supervisor in W2 → LLM-driven
Orchestrator in W3.** The opposite design decision from W2's
supervisor. Justified: routing a chat turn is unambiguous (the
W2 supervisor uses trigger tokens). Prioritizing an attack
campaign is strategic reasoning. Different cognitive task →
different tool.

**5. Judge is Haiku, not Sonnet.** Cost-driven. The accuracy
tradeoff is mitigated by: (a) deterministic-signal overrides
where possible (UUIDs, regex patterns, forbidden phrases), and
(b) periodic judge-of-the-judge audits using Sonnet on a sample.
If audit disagreement exceeds 15%, swap Judge to Sonnet.

**6. Documentation Agent autonomously writes to the eval suite.**
Reduces human bottleneck for the regression guarantee. The risk
is a false-positive vuln injecting a permanent broken test.
Mitigations: human approval gate on critical-severity reports,
schema validation on case insertion, automated revert if the new
case fails the W2 eval gate's own sanity checks (the case must
produce a stable verdict across 3 consecutive runs before it's
considered ready for the regression suite).

---

## Pre-search-checklist correspondence

The W3 PRD's appendix-A checklist (Phases 1-3) is answered in
full in `PRESEARCH.md`, filled in for this build. This document
focuses on the architecture itself; PRESEARCH.md is the
decision-log behind it (to be drafted as part of the Friday
final submission).
