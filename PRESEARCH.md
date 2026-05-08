# Pre-Search Checklist — AgentForge Clinical Co-Pilot

> The architectural-discovery checklist (16 questions across 3 phases),
> filled in after-the-fact with the answers that match what shipped.
> Each answer is concrete: actual files, actual numbers, actual
> services. Where there was a real fork in the road, the rejected
> alternative and the reason are noted.
>
> Reads as a working architectural reference. The matching detailed
> docs are linked inline.

---

## Phase 1 — Constraints

### 1. Domain Selection

- **Domain:** Healthcare. Specifically, a clinical co-pilot for
  primary-care physicians inside an OpenEMR fork.
- **Use cases supported** (from [USERS.md](USERS.md)):
  - UC-1 — 90-second pre-visit briefing for the open chart
  - UC-2 — Document ingestion (lab PDFs, HL7 ORU lab feeds, DOCX
    referrals, XLSX workbooks, TIFF fax packets, intake forms)
    with extraction → citation → writeback
  - UC-3 — Evidence-grounded recommendations (USPSTF / ADA / ACIP /
    ACC-AHA / CDC) for screening, prevention, and management
  - UC-4 — Defensible refusal of cross-patient queries, prompt
    injection, write attempts, and out-of-scope requests
- **Verification requirements** for the domain (driven by clinical
  liability, not arbitrary rigor):
  - Every clinical claim in agent prose must inline-cite a real
    chart row or guideline chunk
  - Cross-patient access is fail-closed (a code path, not a prompt
    rule)
  - PHI tokenized before reaching any LLM, rehydrated after
  - Bounding-box-validated source lookup for every PDF-extracted
    fact
- **Data sources accessed:**
  - OpenEMR's existing FHIR R4 API: `Patient`, `Condition`,
    `AllergyIntolerance`, `MedicationRequest`, `Encounter`,
    `CareTeam`, `Observation`, `Immunization`
  - HL7 v2 messages (ORU_R01, ADT_A08) — parsed structurally, not via LLM
  - Document ingestion: PDF (vision), DOCX (text), XLSX (CSV-flatten),
    TIFF (PDF conversion → vision)
  - 24-chunk hand-curated guideline corpus

### 2. Scale & Performance

- **Query volume:** Per W1 cost projection — ~65 turns/day per PCP
  × 22 working days = ~1,430 turns / physician / month. Bursty,
  not steady — chart open + 5-minute clinical window per encounter.
- **Latency targets** (measured against staging — see [W2_COSTS.md §4](W2_COSTS.md)):
  - Chat turn (plain): p50 5.5s, p95 11s
  - Chat turn (RAG-routed): p50 7.5s, p95 14s
  - Lab PDF extraction (1 page): p50 9s, p95 16s
  - Bbox click-through: p50 0.4s, p95 0.9s
- **Concurrency:** 4-slot global `chat_concurrency_slot` semaphore
  in the agent service. Tuned to Anthropic's 30K-TPM per-org rate
  limit (4 concurrent turns × ~3-4K input tokens × 10s wall ≈ 7K
  TPM → safe).
- **Cost constraints:** Per [W2_COSTS.md §7](W2_COSTS.md):
  - $0.020 / plain chat turn
  - $0.030 / RAG-grounded chat turn
  - $0.020-$0.040 / extraction
  - $0.000 / HL7 v2 ingestion (zero-LLM-cost — pure parse)
  - **Per-physician monthly: ~$5-8 over W1** (10-20% relative);
    inside $25/month at 10K user tier

### 3. Reliability Requirements

- **Cost of a wrong answer:** Clinical liability. A confidently
  hallucinated medication, dose, or allergy can land in a chart
  the doctor signs and acts on. Quantitatively bounded by the
  verifier — every claim must point at a row that exists.
- **Non-negotiable verification:**
  - Structural citation enforcement
    ([`copilot/verification/structural.py`](agent-service/src/copilot/verification/structural.py))
  - Patient-context middleware on every tool call (fail-closed
    UUID comparison)
  - Token-map PHI redaction before the LLM sees anything
- **Human in the loop:**
  - Extracted facts ALWAYS render as cited, clickable proof —
    bbox-overlay on PDFs, source links on guidelines — before the
    doctor decides to act
  - The writeback layer surfaces partial / low-confidence
    extractions as warnings, not as accepted chart data
- **Audit / compliance:**
  - Every chat turn is a Langfuse trace with `session_id =
    patient_uuid`, `user_id = OE user_id`, supervisor decisions +
    worker spans + LLM generation + cost details
  - PHP writeback uses OpenEMR's existing `EventAuditLogger`
  - **Known compliance gaps** (tracked in [AUDIT.md §1.5](AUDIT.md)):
    no BAA with Anthropic yet, Langfuse Cloud not self-hosted,
    password-grant for the FHIR bridge

### 4. Team & Skill Constraints

- **Single engineer** (Tyler), four-week sprint cadence.
- **Framework familiarity:** Python + FastAPI strong; TypeScript +
  Next.js learned this sprint for the W2 surprise dashboard;
  LangGraph new this sprint; Pydantic + Anthropic SDK comfortable;
  PHP just enough to write the OpenEMR module shim.
- **Domain experience:** Not a clinician. Compensated by sticking
  rigorously to published USPSTF / ADA / ACIP / ACC-AHA / CDC
  guidelines for the corpus, and using the W2 PRD's stated use
  cases instead of inventing new ones.
- **Eval / testing comfort:** Built the 63-case eval suite + the
  three cookbook-shaped extensions (replay harness, LLM-as-judge,
  A/B experiments) from scratch this sprint. Unit tests via pytest;
  CI via GitHub Actions; baselines via the calibration workflow.

---

## Phase 2 — Architecture Discovery

### 5. Agent Framework Selection

- **Choice: LangGraph.** Three-node graph (supervisor →
  evidence_retriever / intake_extractor → answer) with explicit
  state reducers. See [W2_ARCHITECTURE.md §2](W2_ARCHITECTURE.md).
- **Multi-agent**, supervisor-led. The supervisor is a 30-line pure
  Python heuristic, **not** an LLM call — chosen for determinism
  (the eval gate's routing assertions need 100% repeatability),
  latency (saves ~2-3s/turn), and testability (16 unit tests on
  `route_decision()`).
- **State management:** TypedDict `WorkerState` with explicit
  reducers — `extractions` and `evidence` append, `attachment` and
  `hops` replace. Disabled checkpoint serialization (LangGraph's
  default) because our state carries Pydantic models that don't
  always JSON-roundtrip cleanly and we don't need resumability.
- **Tool integration complexity:** Two flavors. Patient-context
  tools (7 of them — meds, problems, allergies, encounters, labs,
  vitals, immunizations) for the chat path. Document-ingestion
  paths (5 — PDF lab, PDF intake, HL7 ORU/ADT, DOCX, XLSX, TIFF)
  for `/agent/extract`. Each goes through the patient-context
  middleware pre- and post-call.
- **Rejected:** LangChain (chain mental model is wrong for back-
  edges); CrewAI (too opinionated about role-playing agents);
  custom (paid off when we added the back-edge from worker to
  supervisor — would have rewritten our dispatcher).

### 6. LLM Selection

- **Primary: Claude Sonnet 4.6** (`claude-sonnet-4-6`). Used for:
  chat, vision PDF extraction, text DOCX/XLSX extraction.
  - Native tool-use with forced `tool_choice` for structured
    output — Pydantic `LabPdfExtraction` / `IntakeFormExtraction`
    schemas convert to Anthropic input_schema directly
  - Vision PDF input (up to 32 MB / 100 pages)
  - Prompt caching native (5-minute TTL) — main cost lever
- **Judge tier: Claude Haiku 4.5** (`claude-haiku-4-5-20251001`).
  Used for: stage-4 LLM-as-judge clinical-quality questions.
  ~10× cheaper than Sonnet ($1/$5 per M vs $3/$15).
- **Function calling support:** Anthropic tool-use with
  `tool_choice: {type: "tool", name: ...}` to force structured
  output. JSON-Schema enforced server-side via Pydantic.
- **Context window:** 200K tokens (Sonnet). Typical turn fits in
  ~50K (system prompt + 7-tool patient bundle + history + RAG
  augmentation).
- **Cost per query acceptable:** Yes, see [W2_COSTS.md §3](W2_COSTS.md):
  $0.020 plain / $0.030 RAG turn at the per-physician volume
  projected.
- **Rejected:** GPT-5 (no PDF input + harder structured output);
  open-source local (no clinic-grade BAA story; latency unacceptable).

### 7. Tool Design

- **Patient-context tools (7),** wired in `agent-service/src/copilot/tools/__init__.py`:
  - `GetActiveMedicationsTool` — `MedicationRequest?status=active`
  - `GetActiveProblemsTool` — `Condition?clinicalStatus=active`
  - `GetAllergiesTool` — `AllergyIntolerance?patient=…`
  - `GetRecentEncountersTool` — `Encounter?patient=…&_count=5`
  - `GetLabHistoryTool` — `Observation?category=laboratory`
  - `GetVitalHistoryTool` — `Observation?category=vital-signs`
  - `GetImmunizationsTool` — `Immunization?status=completed`
- **Document ingestion (5 routes** through `/agent/extract`):
  `lab_pdf`, `intake_form`, `hl7v2_oru` / `hl7v2_adt` (zero-LLM,
  pure parse), `docx_referral`, `xlsx_workbook`, `tiff_fax` (TIFF
  → in-process PDF → existing vision pipeline).
- **External API:** OpenEMR FHIR R4 over OAuth2 password grant.
  Retry-on-5xx with exponential backoff (3 retries, capped at 5s)
  in [`bridge/openemr.py`](agent-service/src/copilot/bridge/openemr.py).
- **Mock vs real:** Real, against the deployed OpenEMR with the
  14-patient AgentForge demo seed (`sql/agentforge_demo_seed.sql`).
  No mocks. The eval suite hits the live agent.
- **Error handling per tool:** `cache.warm()` does
  `asyncio.gather(..., return_exceptions=True)` — one tool's
  failure can't block the bundle. Failed slots get
  `{rows: [], warnings: ["fetch failed"]}`. If ALL tools fail,
  `warm()` skips the cache write so the next call retries fresh
  (cache-poison fix).

### 8. Observability Strategy

- **Langfuse** (Cloud today; self-hosted in v2 plan). Chosen over
  LangSmith because it's open-source + can self-host for HIPAA
  parity, and it integrates with LangGraph natively.
- **Metrics that matter:**
  - Per-turn cost split: input / output / cache_write / cache_read
    tokens — cache-hit rate is the W2 cost lever, not raw token
    volume
  - Latency p50/p95 per route
  - Supervisor decisions (how often does evidence_retriever route?)
  - Verification retry count + fallback rate (proxy for "how
    often does the model wobble?")
  - Refusal rate vs total turns
- **Real-time monitoring:** Langfuse dashboard tagged with
  `session_id = patient_uuid`. Surface filtered by user_id for
  per-clinician views.
- **Cost tracking:** `cost_details` populated on every Langfuse
  generation via `_compute_cost(model, usage)` in
  [`anthropic_client.py`](agent-service/src/copilot/llm/anthropic_client.py).
  Per-extraction breakdowns + p50/p95 in [W2_COSTS.md](W2_COSTS.md).

### 9. Eval Approach

- **63 cases × 11 categories × 6 boolean rubrics.**
  - Categories: `extraction_lab`, `extraction_intake`, `evidence`,
    `citation`, `boundary`, `missing_data`, `phi_logs`,
    `fabrication`, `golden`, `multistep`, `adversarial`
  - Rubrics: `schema_valid`, `citation_present`,
    `factually_consistent`, `safe_refusal`, `no_phi_in_logs`,
    `every_turn_passes`
- **Ground truth:** Hand-labeled per case — expected substrings
  for chat cases, expected resource IDs for citation_present,
  expected schema validation for extractions.
- **Automated** via `evals/w2/runner.py`. PR-blocking
  GitHub Action ([`.github/workflows/eval-gate.yml`](.github/workflows/eval-gate.yml))
  fires the suite against the deployed agent on every PR to
  `agent-service/**` or the OpenEMR module.
- **CI integration:**
  - Per-PR full suite run (10 min; gated by Anthropic 30K-TPM)
  - Compare against [`baseline.json`](agent-service/evals/w2/baseline.json) —
    fail if any category drops >5pp OR below 90% floor
  - **Current locked baseline: 100% across every category and
    every rubric** (63/63 pass)
- **Cookbook stages 3-5** layered on top:
  - Stage 3 — `--record` / `--replay` JSONL harness:
    [`evals/w2/replay.py`](agent-service/evals/w2/replay.py)
  - Stage 4 — LLM-as-judge tier (Haiku binary verdicts):
    [`evals/w2/judge.py`](agent-service/evals/w2/judge.py)
  - Stage 5 — A/B experiment diff:
    [`evals/w2/experiments.py`](agent-service/evals/w2/experiments.py)

### 10. Verification Design

- **Two layers:**
  1. **Structural** — citations must exist + reference real rows
     in `seen_tool_results`. Extracts every `[Resource#uuid]` via
     regex; if any cited UUID isn't a row we returned this turn,
     verification fails.
  2. **Substantive prose without citations** — if the response
     contains clinical-keyword tokens (med names, diagnosis terms,
     numeric values + units, screening / recommend / diagnosis
     verbs) but has zero citation markers, that's a refusal trigger.
- **Fact-checking sources:**
  - Patient context bundle (7-tool warm output)
  - Worker-fetched rows (RAG chunks → `Guideline#…`, extraction
    citations → `DocumentReference#…`)
  - Tool-call results within the chat turn
- **Confidence thresholds:**
  - Vision extraction: per-fact `extraction_confidence` field
    (`high` / `medium` / `low`) — demoted to `low` if the bbox-
    match step can't find the cited quote in the pdfplumber word
    output. Low-confidence facts surface as warnings, not as
    chart writes.
  - Chat verification: pass / fail boolean (no soft scoring) —
    matches the cookbook stage-1 boolean-rubric ethos
- **Escalation triggers:**
  - 3 retry attempts with corrective re-prompt
    (`"Your previous answer didn't pass verification. Cite a
    real row or remove the claim."`)
  - On final failure: refusal fallback emitting only verified
    facts grouped by FHIR resource type (`_verified_facts_only`)
    — the doctor sees the chart facts directly with their
    citations, never a confabulated answer

---

## Phase 3 — Post-Stack Refinement

### 11. Failure Mode Analysis

- **Tool failures:** caught per-tool in `cache.warm()` with
  `return_exceptions=True`. Empty rows + warning in the bundle
  slot. The orchestrator continues with whatever succeeded; the
  fallback path renders only what we have rows for.
- **Ambiguous queries:** the heuristic supervisor routes to
  `evidence_retriever` when the message contains any of
  ~25 trigger tokens (`uspstf`, `recommend`, `should`,
  `screen`, etc. — full list at
  [`workers/routing.py`](agent-service/src/copilot/workers/routing.py)
  and the [/visibility page](https://copilot-agent-production-ba87.up.railway.app/visibility)).
  No LLM-based query understanding for ambiguity — that would be
  non-deterministic. Hop counter caps at 5 to break loops.
- **Rate limiting & fallback:**
  - Anthropic 529 OverloadedError: retry-with-backoff (4 tries,
    1.5s base, 12s cap) on `_call_with_retry` in
    [`anthropic_client.py`](agent-service/src/copilot/llm/anthropic_client.py)
  - OpenEMR FHIR 5xx: 3 retries on `_fhir_get` + `_post_token_with_retry`
    in [`bridge/openemr.py`](agent-service/src/copilot/bridge/openemr.py)
  - Per-IP rate limit on token-less `/demo/chat` only (NOT on
    HMAC-authed `/agent/chat` — discovered the hard way that this
    gates the eval suite, see Q4 in [INTERVIEW_PREP.md](INTERVIEW_PREP.md))
- **Graceful degradation:**
  - No Voyage key → BM25-only retriever with reciprocal-rank
    fusion (eval evidence category: 100% → ~85%)
  - No Cohere key → BM25 ∪ dense without rerank
  - No agent service / Anthropic outage → embedded panel surfaces
    a friendly error with Retry button (post-MVP UX harden)
  - All FHIR tools fail → `(no tool data was retrieved)` honest
    fallback rather than confabulation

### 12. Security Considerations

- **Prompt injection:** structural verifier rejects any response
  whose citations don't reference real rows. 7 adversarial test
  cases (`adversarial_*` in [`evals/w2/cases.py`](agent-service/evals/w2/cases.py))
  exercise: DAN-style role-play, authority impersonation,
  hypothetical framing, system-prompt extraction, tool-spec
  poisoning, citation forgery, multi-turn slow-boil. All currently
  pass at 100%.
- **Data leakage:**
  - Token-map PHI redaction on every chart bundle entering the
    agent (`PT_NAME_*`, `MRN_*`, `DOB_*`, `PHONE_*`, `EMAIL_*`).
    Map lives only in RAM, never persisted.
  - `no_phi_in_logs` rubric scans every response for un-rehydrated
    tokens + MRN / SSN / phone / email regex patterns
  - Server components on the dashboard keep access tokens off the
    browser entirely (see
    [PATIENT_DASHBOARD_MIGRATION.md §3a](PATIENT_DASHBOARD_MIGRATION.md))
- **API key management:**
  - Railway env vars for Anthropic, Voyage, Cohere, Auth.js
    secrets, OAuth client_id/secret
  - Per-env `AUTH_SECRET` (Auth.js JWT cookie encryption)
  - OpenEMR encrypts stored OAuth `client_secret` in the DB
- **Audit logging:**
  - Langfuse trace per turn (patient_uuid + user_id + spans + tokens + cost)
  - OE's `EventAuditLogger` for chart writes
  - HMAC bearer with embedded `issued_at` so token replay is bounded
  - PKCE + state + nonce on the dashboard's OAuth flow

### 13. Testing Strategy

- **Unit tests** ([`agent-service/tests/`](agent-service/tests)):
  - Tool dispatch + each tool's FHIR Bundle parse
  - `route_decision()` supervisor heuristic (16 cases)
  - HL7 v2 segment tokenizer + ORU/ADT parser
  - Pydantic schema validation (lab + intake)
  - Citation regex + structural verifier
  - PHI redaction round-trip
- **Integration tests** (eval suite is the integration test):
  - 16 PDF-extraction cases (vision + bbox match + writeback)
  - 10 evidence-retrieval cases (BM25 + dense + rerank +
    structural verification)
  - 6 multi-step conversation cases
- **Adversarial:** 7 dedicated cases covering jailbreak,
  prompt injection, citation forgery, etc.
- **Regression:** locked `baseline.json` + 5pp delta + 90% floor.
  Synthetic regression unit test
  ([`test_eval_runner.py:test_synthetic_regression_canary`](agent-service/tests/test_eval_runner.py))
  asserts the comparison logic flags a 12.5pp drop independent of
  any specific deploy.

### 14. Open Source Planning

- **License:** Inherits OpenEMR's GPL-3 (since this is a fork).
  The agent-service and dashboard could in principle be licensed
  separately (they're distinct codebases) but are kept under the
  same umbrella for sprint scope simplicity.
- **What released:** Full repository at
  https://github.com/tylerxia8/agentforge-clinical-copilot
- **Documentation:**
  - [`README.md`](README.md) — entry point with deploy URLs
  - [`AUDIT.md`](AUDIT.md) — security / performance audit of OpenEMR
  - [`USERS.md`](USERS.md) — target user + use cases
  - [`ARCHITECTURE.md`](ARCHITECTURE.md) — W1 integration plan
  - [`W2_ARCHITECTURE.md`](W2_ARCHITECTURE.md) — W2 multimodal + worker graph
  - [`PATIENT_DASHBOARD_MIGRATION.md`](PATIENT_DASHBOARD_MIGRATION.md) — Next.js port defense
  - [`COSTS.md`](COSTS.md) / [`W2_COSTS.md`](W2_COSTS.md) — economics
  - [`W2_DEMO_SCRIPT.md`](W2_DEMO_SCRIPT.md) — 5-min demo walkthrough
  - [`INTERVIEW_PREP.md`](INTERVIEW_PREP.md) — talking points
  - This document — pre-search reflection
- **Community engagement:** Sprint project; not pursuing an active
  community fork. The repo is public for reviewer access.

### 15. Deployment & Operations

- **Hosting:** Railway, 5 services in one project:
  - `agentforge-clinical-copilot` — OpenEMR (PHP + Apache + persistent volume at `sites/default/documents/`)
  - `copilot-agent` — Python FastAPI (agent service)
  - `openemr-dashboard` — Next.js 15 (modern dashboard)
  - `MySQL` — MariaDB
  - `Redis` — context cache + rate-limit buckets
- **CI/CD:**
  - GitHub Actions: `eval-gate.yml` blocks merge on regression;
    `eval-baseline-calibrate.yml` is a workflow_dispatch tool for
    refreshing `baseline.json`
  - OpenEMR + dashboard auto-deploy on push to main; agent
    service deploys via `railway up` (its repo source isn't
    GitHub-linked, by historical accident — documented in the
    deploy notes)
- **Monitoring & alerting:**
  - Langfuse for AI / chat metrics + cost
  - Railway dashboard for infra (CPU / mem / restart count)
  - GitHub Actions email + bot comment on eval-gate failure
- **Rollback:**
  - `git revert` + push triggers a redeploy
  - Last-known-good `baseline.json` is committed; reverting it
    re-locks the gate at the prior level
  - Volume-backed OAuth keys persist across redeploys (after a
    one-time setup pain documented in
    [`PATIENT_DASHBOARD_MIGRATION.md §6`](PATIENT_DASHBOARD_MIGRATION.md))

### 16. Iteration Planning

- **User feedback collection:** Not yet — sprint MVP without real
  clinician users. The eval suite is the proxy; failure cases get
  added to it as new use cases surface.
- **Eval-driven improvement cycle:**
  - Every PR runs the full suite
  - On intentional rubric / prompt change, re-calibrate baseline
    via `eval-baseline-calibrate.yml`
  - Stage 3 replay harness lets us iterate rubrics offline at
    zero cost (re-grade JSONL recordings instead of re-running the
    full suite)
  - Stage 5 A/B harness compares variants when changing models /
    prompts
- **Feature prioritization** (in order):
  1. AUDIT.md findings (security / data quality) — never skipped
  2. PRD-listed use cases — must-haves
  3. Reviewer / grader feedback — direct response (e.g., MVP
     grader's visibility note → `/visibility` page)
  4. Roadmap items below — opportunistic
- **Long-term maintenance plan** (tracked in
  [AUDIT.md §1.5](AUDIT.md) and [W2_ARCHITECTURE.md §11](W2_ARCHITECTURE.md)):
  - Move to AWS Bedrock for Claude (BAA + region pinning)
  - Self-host Langfuse (BAA + audit trail control)
  - JWT bearer auth for the FHIR bridge (replace password-grant)
  - ColQwen2 multi-vector retrieval once corpus > 500 chunks
  - Critic-agent worker (re-reads cited source for faithfulness —
    closes the structural-verifier gap that stage-4 LLM-as-judge
    addresses partially)
  - Dashboard ACL parity with OpenEMR's per-row check
  - Replace OpenEMR's single-replica Apache as the FHIR layer
    (HAPI FHIR or similar) for multi-hospital scale

---

## Architectural shape (for the reasoning-engine / tool-registry / verification-layer frame)

The Pre-Search cover image breaks the architecture into three
spheres. Mapped to this build:

| Sphere | Implementation in this repo |
|---|---|
| **Reasoning engine** | LangGraph supervisor + worker nodes + W1 orchestrator (Claude Sonnet 4.6) |
| **Tool registry** | 7 patient-context FHIR tools + 5 ingestion paths + hybrid retriever (BM25 + Voyage + Cohere) |
| **Verification layer** | Structural verifier + 3-retry corrective loop + verified-facts-only fallback + 63-case eval gate + cookbook stages 3-5 |

Three things would NOT be obvious from the code without this
framing:

1. The supervisor is **not** the reasoning engine — it's a
   deterministic router. The reasoning engine is the LLM call
   inside the answer node.
2. The verification layer **outranks** the reasoning engine. If
   verification fails, the LLM's actual prose is discarded and
   the user gets the fallback. The model can't override the
   verifier — by design.
3. The tool registry has two halves: **synchronous** (chat tools)
   and **asynchronous** (ingestion paths). Both flow into the
   same `seen_tool_results` set the verifier checks against, so
   citations from extraction can be referenced by chat turns later.
