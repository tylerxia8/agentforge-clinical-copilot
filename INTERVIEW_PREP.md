# Interview prep — MVP defense

> Required within 24h of MVP submission. Case study page 9 lists the
> question areas verbatim; answers below are tight talking points
> grounded in our actual deliverables, not generic answers.
>
> **How to use this document.** Read it twice the morning of the
> interview. Don't memorize — internalize the arc of each answer
> (problem → finding → decision → tradeoff). Have [AUDIT.md](AUDIT.md),
> [USERS.md](USERS.md), and [ARCHITECTURE.md](ARCHITECTURE.md) open
> in tabs so you can point.
>
> **Two rules.** Lead every answer with the one-sentence version;
> let them ask for depth. And when you don't know, say so —
> "I haven't decided yet" is a better answer than improvisation.

---

## Your audit

### Q: Walk us through your most important finding.

> "The most important finding is that OpenEMR's authorization is
> role-based, not patient-based. The `PatientService`, the
> `EncounterService`, the FHIR controllers — they all gate on ACL
> permissions like `patients/med`. None of them filter by the
> caller's care-team relationship to the patient. Any clinician
> with medical-records access can read any patient via the API.
> The web interface enforces what screen you're on; the API
> surface doesn't."
>
> "It matters more for an agent than for the existing UI because
> a chat interface invites broader queries than a chart click does.
> A doctor can type 'show me everyone on metformin' and the API
> would happily return it. So the agent needs a per-turn
> patient-context boundary that the existing ACL doesn't provide.
> That's the patient-context middleware in
> [ARCHITECTURE.md §3](ARCHITECTURE.md) — it lives above the
> service layer and fails closed."

**Follow-ups to be ready for.**
- *"How do you know that's the most important?"* → "Three reasons.
  One, it can't be fixed at deploy time — it's a code change.
  Two, it's the gap that's worst for the kind of UI we're adding.
  Three, the case study explicitly calls out that 'multi-user
  environments are the norm in clinical settings'."
- *"Why didn't OpenEMR build patient-level filtering?"* → "It's
  not specific to OpenEMR — most EHRs treat patient access as a
  policy concern (HR / credentialing / break-the-glass) rather
  than a code-enforced one. That's defensible at hospital scale
  where every user is a credentialed clinician. It's not enough
  for an agent."

### Q: What would you have missed if you had skipped the audit and gone straight to building?

> "Three things, all of which would have made themselves known the
> hard way."
>
> "One — the data quality landmines. `form_vitals` has no unit
> column. `prescriptions.drug` is free text where strength and
> frequency are sometimes embedded. `lists.diagnosis` mixes ICD-9
> and ICD-10 in the same row. If I'd just wired tools to those
> tables and trusted the model, the first 'is she on the right
> dose?' question would have surfaced a hallucination — confidently
> stated, hard to detect."
>
> "Two — the patient boundary issue. I would have built a
> single-patient agent and only later realized the API would let
> the model wander."
>
> "Three — the latency floor. The audit found there's no
> application-level cache and several agent-critical queries are
> unindexed. Without that, the architecture would have over-promised
> on response time and under-delivered."

### Q: How did the audit change your AI integration plan?

> "Five concrete changes — each is a numbered decision in the
> [ARCHITECTURE.md](ARCHITECTURE.md) summary."
>
> "The patient-context middleware is the audit §1.2 finding. The
> redaction layer is the audit §5.4 finding. The deterministic
> verification rules with explicit unit-unknown handling come
> straight from the data-quality findings in §4.6 and §4.2. The
> encounter-open cache is the §2.5 latency-floor finding. And the
> recommendation to deploy on AWS Bedrock with PrivateLink in
> production is the §5.6 BAA finding."
>
> "Without the audit, I'd have built a tool-using LLM. With it,
> I'm building a tool-using LLM with patient boundaries, PHI
> redaction, citation-required verification, schema-aware data
> handling, and a defensible BAA story."

---

## Your architecture

### Q: Why did you design the verification layer the way you did?

> "Because verification can't be one thing. It has to be cheap on
> the common case, expensive on the hard case, and refuse rather
> than guess on the impossible case."
>
> "So it's three layers. First, a deterministic structural check —
> regex over the response, every clinical claim must inline-cite
> a row id, every cited id must refer to a row a tool actually
> returned this turn. That catches the bulk of failures for free."
>
> "Second, a small set of deterministic domain rules — vitals
> must declare units or say 'units not recorded', 'currently on'
> claims must cite an active prescription row, allergy claims must
> name the verification state. These come straight from the
> audit's data-quality findings."
>
> "Third, an LLM-as-judge call to Claude Opus 4.7 for nuanced
> cases — UC-3 reconciliation, free-text note summaries — where
> the deterministic rules can't tell. About a penny per turn,
> bypassed when the cheap checks already approve."
>
> "And on three failures, the agent doesn't fabricate. It refuses
> with a verified-facts-only fallback — the raw rows the doctor
> can read herself."

**Be ready to admit:** "What verification doesn't catch is a
subtle misreading of a correctly-cited row. The judge catches the
obvious cases; the eval set targets the rest."

### Q: What does your agent do when a tool fails or a record is missing?

> "Tool fails — one retry, then the model continues without that
> tool's data and tells the user. We don't silently swallow it.
> Record missing — the agent says so explicitly. 'No prior
> cardiology consult on file' is allowed; 'no cardiac issues' is
> not. That's actually a domain rule — the refusal-when-empty
> rule — so it's enforced, not vibes."
>
> "There's a table of every failure mode in
> [ARCHITECTURE.md §7](ARCHITECTURE.md), and the principle that
> runs through all of them is 'degrade transparently' — if a fact
> is missing, say so. And 'refuse before fabricating' — worse to
> be confidently wrong than honestly silent."

### Q: Where are the trust boundaries in your system, and how are they enforced?

Three boundaries. Be specific:

> "First boundary — the OpenEMR session to the agent service. The
> PHP module verifies the user's session, reads the open patient
> from the session, and mints an HMAC-signed token containing
> user_id and patient_uuid. The agent service verifies the
> signature and uses what's in the token, never what's in the
> request body. Five-minute TTL, replay-safe via nonce."
>
> "Second boundary — the agent's tool calls. Every tool that takes
> a patient handle is required to receive a patient_uuid that
> matches the token. The middleware checks this before dispatch
> and drops any returned row whose patient_uuid doesn't match.
> Defense in depth — even if the OpenEMR REST bridge has a bug,
> the middleware catches it."
>
> "Third boundary — the LLM. PHI is tokenized before the prompt
> is built. The model never sees real names, MRNs, full DOBs,
> phone, or email. Token map lives in request scope, never gets
> persisted, never crosses the process boundary."

---

## Your evaluation

> Note: the eval framework is a Thursday deliverable. For the MVP
> interview, frame this as 'here's what's tested today, here's
> what's coming Thursday.'

### Q: What does your eval suite test that a happy-path demo would not reveal?

> "Three categories of cases that a happy-path demo would never
> hit."
>
> "One — adversarial cases. Cross-patient queries, prompt
> injection attempts, requests for data the user shouldn't see.
> The boundary tests in `tests/test_patient_context_middleware.py`
> cover the unit level today; the agent-level adversarial cases
> are in the Thursday eval batch."
>
> "Two — schema landmine cases. A patient with vitals stored
> without units. A patient with both ICD-9 and ICD-10 hypertension
> on the active problem list. A medication that exists in
> `prescriptions` at one dose and in `lists` at another. The
> verification rules I've already implemented are tested against
> these in `tests/test_verification.py`. The agent-level versions
> are in the Thursday batch."
>
> "Three — empty-record cases. Patient with no prior encounters.
> Chart with no allergies documented. The agent has to handle
> 'no record' as a first-class case and not invent."

### Q: What did you find when you ran it?

> "The structural verification catches every test case I wrote for
> it. The domain rules catch the unit-units and active-meds cases.
> The middleware blocks every cross-patient call I throw at it
> and drops every cross-patient row from a result."
>
> "What I haven't found yet — and this is where the Thursday eval
> matters most — is what fails on real-shaped data. OpenEMR demo
> data is too clean. I'm seeding Synthea-derived patients into a
> separate eval database and writing 20 hand-crafted adversarial
> patients on top. That's where I'll find the failures the unit
> tests don't see."

### Q: What would you add to it next?

> "Three things, in this order."
>
> "One — note-summarization fidelity. The judge call covers it
> today, but I want a structured note-segmentation pre-pass so
> the agent can cite specific clinical statements rather than
> 'pnotes#812'."
>
> "Two — regression testing across prompt versions. Every prompt
> change should run the full eval and surface a diff. Langfuse
> supports this; it's wiring."
>
> "Three — a 'real doctor in the loop' sanity track. The case
> study's bar is whether Dr. M would actually use this. Pass
> rates against synthetic cases are necessary but not sufficient
> — I want a five-doctor walkthrough with a think-aloud script
> before I'd recommend any production deployment."

---

## Production thinking

### Q: How would you scale this to a 500-bed hospital with 300 concurrent clinical users?

> "Three things change at that scale."
>
> "Infrastructure — switch the LLM call from direct-Anthropic to
> AWS Bedrock with PrivateLink. Most hospitals already have AWS
> BAAs and existing procurement paths; PrivateLink keeps PHI off
> the public internet. The Anthropic SDK and the Messages API
> are the same surface, so it's a base-URL swap, not a rewrite."
>
> "Capacity — horizontally scale the agent service behind a load
> balancer; introduce a queue for the warm-on-chart-open path so
> it can't compete with synchronous turns; per-clinic Redis
> namespacing on the prompt cache."
>
> "Operations — observability has to graduate from 'I look at
> Langfuse' to 'on-call rotation with SLOs'. Every prompt change
> gates on the eval suite in CI. SSO for the OpenEMR side, since
> 300 clinicians don't get usernames-in-a-database. And I'd want
> a per-user query budget so a leaked agent token can't quietly
> exfiltrate the practice — that's the §164.404 anomaly detection
> piece in [AUDIT.md §5.5](AUDIT.md)."

### Q: What would you need to change before you'd be comfortable with a real physician relying on this?

Honest list. Don't soft-pedal:

> "Six things, ordered by importance."
>
> "One — care-team-level patient filtering, not just open-chart
> filtering. Today my boundary is 'the chart you're looking at',
> which is correct for a single agent turn but doesn't enforce
> 'your panel only' across sessions. That requires a custom
> provider-patient table OpenEMR doesn't have."
>
> "Two — the eval suite has to be real. Not 80 cases — a few
> hundred, refreshed from Synthea regularly, with a sustained
> pass rate I can defend."
>
> "Three — a five-doctor pilot with think-aloud. Synthetic eval
> tells you what the agent does; only doctors tell you what they
> do with it."
>
> "Four — the audit log integrity gap from
> [AUDIT.md §1.6](AUDIT.md). Today the log integrity checksum
> lives in the same DB as the log. For production I'd push logs
> to an external syslog with HMAC under a separately-held key."
>
> "Five — the redaction layer needs a real PII scrub on free-text
> fields, not just the schema-known PHI columns I cover today.
> Notes and lab text contain incidental identifiers."
>
> "Six — a clinical-safety review by an actual MD. I'm a
> software engineer; I can build the verification layer, but the
> rules in it should be reviewed by someone with prescribing
> authority."

### Q: What failure mode worries you most, and why?

> "Confidently wrong on a medication. Specifically: the agent
> reads `prescriptions#244` correctly and cites it correctly, but
> summarizes the dose by misreading the embedded text — '20 mg'
> becomes '200 mg'. The structural verifier passes because the
> citation is real. The domain rule passes because the row is
> active. The judge might catch it, but the judge isn't free, so
> I don't run it on every turn."
>
> "It's the failure mode that's both clinically dangerous and
> verification-resistant. The mitigations are layered — never
> claim a dose without citing the row, surface the raw text from
> the row alongside the agent's interpretation, and treat dose
> claims as judge-required in the verification config. None of
> them are perfect."
>
> "It's also why I'd never let v1 write a prescription. Reading
> with cited evidence is a defensible product. Writing requires
> a much higher verification bar than I have."

---

## Other questions you should be ready for

These aren't on the case study sheet but are obvious:

- *"Why Claude over GPT-4 / Gemini?"* — "Tool-use API maturity,
  context window for the per-patient bundle, direct BAA. Bedrock
  parity for production."
- *"Why no agent framework?"* — "LangChain / LangGraph value is at
  orchestration scale I don't have — one agent, ten tools,
  single-patient scope. Hand-rolled is ~300 lines and means the
  middleware, redaction, and verification are first-class
  concerns, not framework callbacks."
- *"What's the BAA story?"* — "Anthropic offers direct BAAs.
  Production target is AWS Bedrock + PrivateLink, which inherits
  the hospital's existing AWS BAA. OpenAI public API has no BAA;
  excluded."
- *"Why not let the model write SQL?"* — "Bypasses the ACL,
  bypasses the audit log, and the schema is messy enough that the
  model would get it wrong. Tools through the service layer
  inherit both the ACL and the audit pipeline."
- *"How long did the MVP take?"* — Don't lie. "X hours of audit
  exploration delegated to parallel agents, Y hours synthesizing,
  Z hours on the architecture and skeleton. The audit was the
  expensive part."
- *"What would you cut if the deadline halved?"* — "The Python
  service. I'd run a thinner agent inside the PHP module against
  Anthropic directly, with the same boundary middleware. Lose
  testability and observability, gain shipping speed."

---

## What to bring to the call

- AUDIT.md / USERS.md / ARCHITECTURE.md open in browser tabs.
- The Railway URL — be ready to share-screen the live app.
- The GitHub repo, on the README.
- This document on a second screen if you have one. Glance, don't read.

## What not to do

- **Don't recap the documents.** They've read them. Add color.
- **Don't dodge the failure-mode question.** It's the most
  important question on the sheet — "what worries you most" tests
  whether you actually know your system.
- **Don't oversell.** Saying "v1 is production-ready" is the wrong
  answer. The right answer to "would you put this in front of a
  real physician?" is the six-item list above.
- **Don't apologize for what's not built.** The case study is
  explicit that the MVP is the *foundation*, not a working agent.
  Frame Thursday work as the next milestone, not a gap.

---

# Week 2 interview prep

W2 expanded the agent in three orthogonal directions: vision (read
PDFs), retrieval (RAG over a guideline corpus), and orchestration
(supervisor + 2 workers in LangGraph). The questions a grader is
likely to drill on, with crisp answers.

## "How does the vision pipeline avoid hallucinated citations?"

The vision model is allowed to emit `quote_or_value` (the literal
text it claims to have read) but NOT bbox coordinates or document
UUIDs — those are stripped from the tool's input_schema before the
call (`extraction/vision.py:build_extraction_tool_schema`). After
Anthropic returns, we hydrate the document UUID server-side and
run a **pdfplumber match step** on every claimed quote: if the
quote can't be found in the page's word list within an edit-distance
budget, the field is demoted to `extraction_confidence="low"` and
flagged for operator review rather than asserted to the chart.
Coordinates come exclusively from pdfplumber, never from the model.

## "What's the supervisor doing differently from a chain?"

It's a heuristic router, not an LLM call: tokens like `uspstf`,
`recommend`, `screen`, `should` flip routing to the
`evidence_retriever`; an attached document flips to `intake_extractor`;
otherwise straight to `answer`. Three reasons it isn't LLM-based:
(1) determinism for the eval gate — a guideline-shaped question
must ALWAYS go through evidence retrieval, not 80% of the time;
(2) ~2-3s latency saved per turn vs an LLM-supervisor; (3)
testability — `route_decision()` is 30 lines of pure-python with
16 unit tests. The hop counter caps at 5 to break loops.

## "Walk me through one chat turn end-to-end."

```
PHP receives JSON → mints HMAC bearer → POST /agent/chat
  → FastAPI verifies token + extracts patient_uuid
  → graph.ainvoke(initial_state)
    → supervisor_node: route_decision({...}) → "answer" (or evidence first)
    → answer_node: composes W1 Orchestrator with augmented prompt
      → ContextCache.get_or_warm() reads/builds 7-tool bundle from Redis
      → Pre-populate seen_tool_results from bundle (verifier sees the rows)
      → TokenMap.tokenize_dict(bundle) → [PT_NAME_1] etc
      → Anthropic Messages call with tool defs + history + augmented user msg
      → tool-use loop: dispatch each tool through patient_context middleware
        → middleware compares tool args' patient_uuid vs ctx.patient_uuid
          → fail-closed if mismatch (CrossPatientAccessError)
      → Verify final_text against seen_tool_results
        → retry up to 2 if verification fails
        → fall back to verified-facts-only if verifier still fails
      → TokenMap.rehydrate(final_text)
  → ChatResponse JSON
  ← back through the graph, supervisor → END
PHP receives JSON, panel renders markdown + citation chips
```

The critical security step is the middleware comparing args'
patient_uuid against ctx — every tool call passes through, none
can be configured to skip it.

## "How does the eval gate work, and how do you know it has teeth?"

63 cases × 5 boolean rubrics (schema_valid, citation_present,
factually_consistent, safe_refusal, no_phi_in_logs). The runner
fires every case against the deployed staging, aggregates per-
category pass rates, compares against `baseline.json`. Fails if
any category drops by >5pp OR below the absolute 90% floor.
PR-blocking GitHub Action.

Teeth: there's a unit test (`test_eval_runner.py:test_synthetic_regression_canary`)
that constructs a baseline-with-regression scenario (a broken
citation regex fails 1 of 8 extraction_lab cases = 12.5pp drop)
and asserts the comparison logic flags it. The PRD's hard-gate
scenario is locked in by that test independent of any specific
GitHub Actions run.

## "What about adversarial users? You have boundary cases — what about jailbreaks?"

Beyond boundary cases (cross-patient, prompt injection, write
attempts), W2 added 7 adversarial probes: DAN-style role-play,
fake-sysadmin authority impersonation, hypothetical framing,
system-prompt extraction, tool-spec poisoning (asking for a tool
that doesn't exist), citation forgery (asking to confirm a
fabricated UUID), and a multi-turn slow-boil escalation. Each
asserts the agent doesn't leak any forbidden term. The structural
verifier is the backbone — even when the model wobbles in prose,
the verifier rejects any response that asserts a non-existent row,
which is the failure mode all of these probes try to engineer.

## "Where would you spend your next week of engineering?"

Three specific bets:

1. **Critic worker** (PRD extension, not core). A small `claim_critic`
   re-reads each cited source and asks "does the cited quote actually
   contain the asserted fact?". Today the structural verifier checks
   that a citation EXISTS; the critic checks the citation is FAITHFUL.
2. **Multi-vector retrieval (ColQwen2)** once the corpus grows past
   ~500 chunks. BM25 + Voyage gets us to ~25% retrieval improvement
   from rerank; multi-vector takes the next step.
3. **JWT OAuth with JWKS** for the FHIR bridge. We're on Password
   Grant against a service account today; documented in AUDIT.md
   as a v2 swap. ~1 day to wire including key rotation.

## What to bring to the W2 interview

- The W1 prep material above.
- [W2_ARCHITECTURE.md](W2_ARCHITECTURE.md) and [W2_COSTS.md](W2_COSTS.md) open.
- The Railway URLs, ready to share-screen — both the OpenEMR chart
  and the Langfuse trace dashboard (so you can show per-call
  cost_details + cache token breakouts on a fresh trace).
- The deployed eval-gate CI run (a regression-canary PR if you've
  built one) ready to demo as the "graders inject a regression"
  proof.
- This document on a second screen.

---

# Thursday AI interview — drilled answers

> Four likely questions; the answers below are the ones I'd actually
> give live. Each leads with the one-sentence version + a concrete
> example or arc; depth on follow-up.

## "Explain your hybrid retrieval design. Why both sparse and dense over just one, and how did rerank actually change the final ranking?"

**Why both sparse and dense:** they fail on opposite query shapes.
BM25 (sparse) nails keyword precision — `"USPSTF statin"`
reliably surfaces the chunk that literally contains those tokens.
It dies on paraphrase. Dense (Voyage `voyage-3`) catches semantic
similarity — *"when should I start someone on a cholesterol drug
for prevention?"* matches the statin chunk even though `"prescribe"`
and `"start"` aren't in the chunk text. Run both, take the union,
get a recall-friendly candidate pool of ~15.

**What the reranker actually does:** Cohere Rerank 3 is a
cross-encoder. It looks at the query and each candidate *together*
(slow), instead of comparing pre-computed embeddings (fast but
lossy). For our 24-chunk corpus, the rerank cost is trivial and
the precision lift is measurable.

**Concrete example, demoed on `/visibility/retrieve`** with the
query *"What does USPSTF say about statin use for primary prevention?"*:

| Rank (BM25-only) | Chunk | BM25 score | What it actually is |
|---|---|---|---|
| 1 | `uspstf-tobacco-cessation-2021` | 0.0167 | Wrong — won by frequency of "USPSTF", "Task Force", "primary" |
| 2 | `uspstf-statin-cvd-prevention-2022` | 0.0164 | The actual answer |
| 3 | `uspstf-aspirin-cvd-2022` | 0.0161 | Adjacent-but-wrong |
| 4 | `aha-cholesterol-2018` | 0.0159 | Adjacent-but-wrong |

After rerank, the statin chunk moves to rank 1 because the
cross-encoder sees query and chunk *together* and recognizes that
"10-year cardiovascular disease risk" + "initiate a statin"
semantically answers the query, where "behavioral interventions for
tobacco cessation" doesn't — even though tobacco cessation has more
keyword overlap.

**Architectural callout:** all three layers degrade gracefully. No
Voyage key → BM25-only with reciprocal-rank fusion. No Cohere key
→ BM25 ∪ dense without rerank. Retriever logs a startup warning and
serves; the eval gate's evidence category drops from 100% to ~85%
but the system still runs.

## "If a physician acted on a wrong recommendation, where's the most likely source of that error?"

Ranked by likelihood:

**1. Real citation pointing at semantically misapplied content (most likely).**
The structural verifier checks that citations *exist* — every
`[Resource#uuid]` references a real row. It does NOT check that the
cited row actually *supports* the asserted claim. Failure mode:
model writes *"ADA recommends 140/90 BP target
[Guideline#ada-bp-target-dm-2024]"* — citation real, claim wrong
(the chunk says 130/80). Structural layer can't catch that.
**Closing the gap:** `evals/w2/judge.py` — Claude Haiku as a
faithfulness judge. Stage 4 of the cookbook. Opt-in per case today;
productionizing it on every chart-grounded turn is a v2 task.

**2. Stale chart data.** Bundle warms once on chart open with a
5-min Redis TTL. If a med is updated in OpenEMR mid-conversation,
the agent reads the old version. Within-turn tool calls hit live
FHIR; the cached bundle is the gap.

**3. Corpus drift.** A 2024 ADA guideline gets superseded; our
24-chunk corpus is hand-curated, not auto-refreshed. Agent confidently
cites stale guidance. v2 mitigation: a `corpus_freshness_check` job
that diffs against source URLs monthly.

**4. OCR/extraction error.** Lab PDF misread → downstream chart
data wrong. The bbox-match step demotes `extraction_confidence` to
"low" when pdfplumber can't find the cited quote, but a successful
match doesn't prove the value was read correctly.

**5. Cross-patient leak.** Lowest because three independent code
paths block it: patient-context middleware, structural verifier,
redaction layer. No single failure leaks data.

The honest summary: structural verification reliably catches "made
up citations." The gap is "real citation, semantically wrong
content." That's stage 4. It's in repo, OFF by default.

## "Why LangGraph for orchestration, and what tradeoffs did it create?"

**Why LangGraph specifically:**
1. **State machine model fits the problem.** Supervisor → worker →
   supervisor → answer is naturally a graph with conditional edges.
   Coding it as nested function calls would invert control flow vs
   how I think about it.
2. **State reducers are explicit.** Each node returns a state delta;
   the framework merges. I know exactly which fields are appended
   (`extractions`, `evidence`) vs replaced (`hops`, `attachment`).
   Implicit field merging is what makes most agent frameworks
   debug-hostile.
3. **Determinism.** My supervisor is a 30-line pure-Python function
   (`route_decision`), not an LLM call. LangGraph lets that live as
   a node like any other — no fight with the framework's expectations.

**Real tradeoffs:**

- **Async-only is the only sane mode.** Mixing sync and async nodes
  corrupts state subtly. We picked async; every node is `async def`.
  Costs zero performance, saves debugging.
- **Stack traces span four layers.** A failure goes FastAPI →
  LangGraph dispatcher → node body → LLM client → Anthropic.
  Mitigation: every node has `@observe(name=...)` so Langfuse spans
  align 1:1 with LangGraph nodes. When a turn fails, I open
  Langfuse first, find the failing span, drop into the trace.
- **Checkpoint serialization conflicts with rich state.** LangGraph
  wants to JSON-serialize state at each step for resumability.
  Our `WorkerState` carries Pydantic models that don't always
  round-trip clean. Disabled checkpointing; resumability isn't a
  feature we need.
- **Debugging routing logic without the framework.** I unit-test
  `route_decision()` directly — pure function, no graph — so
  routing bugs surface independent of LangGraph. 16 unit tests for
  ~30 lines of logic.

**With hindsight:** for a graph this small (3 worker nodes),
hand-rolled would have been fine — `if/elif` dispatching to
coroutines. LangGraph paid off when I added the back-edge
(`worker → supervisor → answer`) without rewriting the dispatcher.
If the graph had stayed at 2 nodes I might have skipped the
framework.

## "Tell me about the hardest technical problem you had during this project."

**The five-day eval-calibration regression that wasn't where I thought it was.**

The setup: PR-blocking eval gate runs the 63-case suite against the
deployed agent on every commit. After working clean on Tuesday, the
next four calibrations all failed identically: `golden 0/3`,
`multistep 1/3`. Same five cases, same way, deterministic across
every run.

The investigation went through four wrong hypotheses before the
right one:

**Hypothesis 1: ip_tracking race condition** in OpenEMR's
`setupIpLoginFailedCounter()` — `SELECT ... INSERT INTO ip_tracking`
was check-then-insert, raced under concurrent OAuth token mints.
Fixed it with `INSERT ... ON DUPLICATE KEY UPDATE`. Re-ran
calibration. **Same five cases failed.**

**Hypothesis 2: Anthropic 529 OverloadedError.** Logs showed bursts
of 529s during the eval. Added retry-with-backoff + jitter to the
Anthropic client. Re-ran. **Same five cases failed.**

**Hypothesis 3: cache poisoning.** Theorized that an early failed
`warm()` write to Redis served subsequent cases an empty bundle for
the full TTL. Made `warm()` skip the cache write when all 7 tools
failed; partial-fail bundles got a 10-second TTL. Re-ran. **Same
five cases failed.**

**Hypothesis 4: my fixes weren't actually deployed.** Found that
Railway's `--from-source` redeploy was reusing a cached Docker
layer. Forced a fresh rebuild with explicit invalidation. Confirmed
via the imageDigest field. Re-ran. **Same five cases failed.**

At this point I'd burned three days. The fact that the failures
were *deterministic* across runs was the key signal — random infra
flake doesn't repeat the same five cases out of 63.

**The real cause:** I went and read the agent service's middleware.
`PER_IP_REQUESTS_PER_MINUTE = 10`, applied to `/agent/chat`. The
rate limiter was originally written for `/demo/chat` (no auth) as
an abuse gate. It got applied to `/agent/chat` (HMAC-authed) too,
where the token mint *is* the abuse gate.

The eval fires 63 cases sequentially, with multistep cases firing
2 chats each. ~70 requests in 10 minutes from a small set of
Railway egress IPs. Late-stage cases (positions 51-56 — golden +
multistep) consistently tripped the per-IP bucket. The 429 response
had empty `text`, so the `factually_consistent` rubric saw no
expected substrings ("Lisinopril", "Atorvastatin", "hypertension",
"diabetes") and failed.

**The fix was four lines** — strip the `check_ip_quota` call from
the HMAC-authed endpoints, leave the global concurrency cap (which
exists to respect Anthropic's per-org TPM, not to gate abuse).
Calibration lifted to 100% on the next run.

**What I learned:** the determinism signal was telling me from day
one. Random failures = infra; same-five failures = code path. I
kept reaching for infra explanations because the symptoms (502s
during deploys, 529 backoffs) *were* real, just not the dominant
cause. Lesson: when symptoms are deterministic, the bug is in your
code, not the cloud.

The full debugging arc is in the branch history — every
wrong-but-real fix shipped (ip_tracking race, 529 retry, cache
poison) is in production today and earns its keep. They just weren't
the cause of the calibration regression.


# Sunday AI interview — drilled answers

> Drafted ahead of the Sunday May 10 final interview. Each answer is
> ~2-3 minutes spoken, structured as: lead claim → concrete proof
> with file references → trade-off acknowledgment. Interviewers
> reward honest trade-offs more than vague optimism.


## "This project required balancing speed (one-week sprint) with reliability (clinical context, eval gates, HIPAA). How did you navigate that tension, and what would you do differently with more time?"

**The lead.** I drew a hard line on what couldn't be bolted on later.
Two things: PHI never leaving the cache, and patient-context
boundaries fail-closed. Everything else I let myself defer — but
documented every cut in `AUDIT.md` so the trade-offs are visible.

**What I shipped Day 1 because retrofitting them would be a real
risk:**

- **PHI redactor with a token map.** Names and MRNs get replaced
  with `PT_NAME_*` and `MRN_*` tokens *before* the agent payload
  goes out — Anthropic and Langfuse only ever see redacted text.
  Rehydrate at render time. If I'd shipped this in week 2, I'd
  have had real PHI in trace logs from week 1.
- **Patient-context middleware fail-closed**, at
  `agent-service/src/copilot/middleware/patient_context.py`. Every
  tool call gets a UUID compare against the open chart's UUID
  before dispatch. Cross-patient call → raises
  `CrossPatientAccessError`, never reaches the bridge. This is a
  code path, not a prompt rule the model could be talked out of.
  There's a unit test that fails if the `!=` flips to `==` —
  actually live as one of my two regression-canary PRs on the
  repo right now.
- **Citation envelope on every fact, plus a structural verifier**
  that rejects sentences whose citations don't trace back to a
  real tool-result row. Faithfulness over fluency; a structural
  rejection is honest, a hallucinated citation is harm.

**What I deliberately deferred and tracked in `AUDIT.md`:**

- JWT private-key OAuth on the FHIR bridge — currently password
  grant, fine for dev/demo, swap planned for v2. Documented in
  AUDIT.md §1.3.
- Per-row ACL parity on the Next.js dashboard — currently maps
  onto OAuth scopes, which is a coarser grain than OpenEMR's
  per-row check. Documented in PATIENT_DASHBOARD_MIGRATION.md.
- Mounting the Railway volume on `sites/default/documents/`
  correctly was a Friday-night fix (with a recovery procedure now
  documented in AUDIT.md §1.5), not Day 1.
- LLM-as-judge tier with full clinician-graded rubrics — shipped
  it as a binary `judge_yes_no` on Haiku for binary clinical-
  quality checks, fine for the eval suite, but a real clinician
  review process is a v2 thing.

**Trade-off (the real one).** The corpus is 24 hand-curated chunks
across USPSTF, ADA, ACC-AHA, CDC, ACIP. That's enough to cover the
demo's clinical scope but it's not a real production corpus. With
another week I'd have ColQwen2 multi-vector and a clinician-review
pipeline for chunk additions. With another month I'd swap the
static corpus for a queryable up-to-date guideline source and treat
the static chunks as a tested fixture. Every cut has a row in
`AUDIT.md` mapped to a v2 timeline. Documented, not pretended away.


## "Walk me through your supervisor routing logic."

**The lead.** It's heuristic plus deterministic, hop-capped at 3.
No LLM call for routing. Same input always produces the same
routing decision — verified by `tests/test_routing.py`.

**The decision tree** (each turn enters the supervisor with three
signals: user text, open patient UUID, any `attachment_pdf_id`
from a fresh upload):

1. **`attachment_pdf_id` set?** Route to `intake_extractor` worker.
   The `doc_type` parameter splits internally between the `lab_pdf`
   schema path and the `intake_form` schema path. No LLM call for
   routing — the upload form sets `doc_type` explicitly.

2. **No attachment, but text matches an evidence trigger?** Route
   to `evidence_retriever` first, then chain into the answer node.
   Triggers are an explicit token list visible on the `/visibility`
   page: `USPSTF`, `AAFP`, `screening`, `recommend`, `indicated`,
   plus a few more. The retriever runs hybrid RAG — BM25, then
   Voyage dense embeddings, then Cohere rerank — and returns top-K
   chunks alongside the patient's chart bundle.

3. **No triggers but substantive question incoming?** Route directly
   to the answer node with the warm patient bundle. Most chat turns
   hit this path.

4. **Hop cap = 3.** If somehow we're still routing after three hops,
   force the answer node. Prevents infinite loops.

**Why deterministic, not LLM-routed:**

- **Auditable.** A clinician — or a grader — can read the trigger
  token list and predict where any query goes. With LLM routing,
  you'd need traces to debug routing decisions.
- **Cheap.** No additional Anthropic call per turn just for routing.
  Saves ~$0.005/turn at scale, compounds.
- **Testable.** `tests/test_routing.py` has 8 cases — attachment
  routing, evidence-token routing, no-trigger fallback, punctuation
  handling. They run in 0.5 seconds in CI as part of the new
  unit-test step in `eval-gate.yml`.
- **Predictable.** The clinical product can't have surprise routing.
  If the agent occasionally decides to skip the evidence retriever
  based on LLM mood, that's a liability.

**Trade-off.** Heuristic routing misses semantic nuance — *"what
should I tell this patient about losing weight"* doesn't contain
`recommend` or `screening`, so it doesn't trigger evidence
retrieval. I addressed this two ways: (1) richer trigger token
list with `programs`, `intervention`, `behavioral`, etc., and (2)
the answer node always has the corpus available as fallback context
for evidence-style answers. With more time I'd add an LLM-routing
fallback as a second pass when the deterministic router scores low
confidence — but only AFTER you can prove deterministic isn't
enough. Premature LLM routing is just adding latency and cost for
no clear win.


## "Walk me through a specific regression your CI gate caught. What change introduced it, which rubric category failed, and what did you fix?"

**The lead.** Yesterday I caught a real production-correctness bug in
the gate's own comparison logic. It would have spuriously failed
legitimate PRs at exactly 5pp drops. Not a synthetic demo — a real
bug, in my own code, surfaced by a test I wrote.

**What I changed.** I was hardening the test suite against Thursday's
grader feedback ("prove the hard guarantees in code, not docs").
Added a new file `tests/test_verifier_adversarial.py` — 17 cases
hammering the citation verifier with attack vectors. Ran the full
test suite to confirm nothing else broke. Five failures came back,
four were stale assertions in older files, but one was structurally
important: `test_compare_passes_on_exact_threshold` in
`test_eval_runner.py`.

**The test asserts** that a category at 0.95 against a baseline of
1.00 — exactly a 5pp drop — should pass the gate, because the
threshold is "more than 5pp" using strict `>`.

**The failure.** Python's IEEE 754 makes `1.00 - 0.95` evaluate to
`0.050000000000000044`. So the strict `if (baseline_rate - rate) >
delta` check was treating an exact 5pp drop as
`0.050000000000000044 > 0.05` — `True` — regression flagged.

**The impact if uncaught.** Imagine a normal PR that nudges one
category from 100% to 95% — well within the PRD's "5pp regression
delta" budget. Old code would have failed CI, blocked the merge,
sent the developer chasing a phantom regression. The grader could
even have written a "your gate over-fires on harmless changes"
finding.

**The fix.** Added a `_FLOAT_EPS = 1e-9` epsilon and changed the
comparison to `(baseline_rate - rate) - delta > _FLOAT_EPS`. That's
well below any realistic eval-rate difference — a single case flip
on a 100-case suite is 0.01, which is 1e7 times bigger than the
epsilon. Production-correct, no behavior change for real
regressions, but the boundary case passes cleanly.

**Which CI layer caught it.** The unit-test layer of
`.github/workflows/eval-gate.yml`, which I'd just added as a
fast-fail step BEFORE the 19-minute eval suite. So a regression in
property logic fast-fails in 6 seconds; a regression in agent
behavior is caught at the eval-suite layer. Two layers, two
different cost profiles, both wired into the same gate.

**Caveat for the interviewer's expected answer.** Both my standing
canary PRs are *deliberate* regressions — the first breaks the
citation regex (caught by eval suite), the second flips the
patient-context check from `!=` to `==` (caught by unit tests).
Those are demonstrations and they sit open on GitHub as red CI
runs. The FP epsilon was a real bug, in my own production code,
surfaced by a test I wrote. That's the more honest answer because
it shows the system catches its own mistakes, not just demo ones.


## "VLMs can hallucinate field labels on scanned forms. Concretely, what did your schema validation and confidence-flagging strategy look like, and can you give an example of a case where your pipeline surfaced an unsupported or low-confidence extraction rather than silently passing it through?"

**The lead.** Three layers: strict Pydantic schemas with required
citation envelopes, pdfplumber as bbox ground-truth, and a
`warnings` list with auto-appending validators that surface
uncertainty rather than silently passing it.

**Layer 1 — Pydantic schemas as the model contract.**
`LabPdfExtraction` and `IntakeFormExtraction` both set
`model_config = ConfigDict(extra="forbid")`. The LLM cannot invent
fields the schema doesn't know about. Each `LabResult` has a
*required* `citation: SourceCitation` co-field —
`{source_type, source_id, page_or_section, field_or_chunk_id,
quote_or_value, bbox}`. If the model can't produce a citation,
validation fails; the response gets rejected and retried. Tests
in `tests/test_schemas.py` pin every required field.

**Layer 2 — pdfplumber as ground truth for bboxes.** The model
transcribes a value and claims a quote. We then use pdfplumber-
extracted text positions on the source PDF to verify the quoted
text actually appears where the model said it does. **Coordinates
are pdfplumber's, not the model's.** The model doesn't *decide*
where things are; it just reads them. If the bbox match fails,
the extraction fails — `tests/test_matcher.py` covers the matching
logic.

**Layer 3 — auto-warning validators.** The schema has a
`warnings: list[str]` field. A `@model_validator(mode="after")`
auto-appends `"chief_concern not present in source document"` if
the model returned `null` for chief_concern without explaining.
Same guarantee for demographics — added Friday during the
verification-hardening pass. So the operator review screen never
sees an intake with a missing required section AND no warning.
Those two states are bound together by code.

**Concrete example where the pipeline surfaced low-confidence
rather than silently passing through:**

The `vitals_unit_rule` in `agent-service/src/copilot/verification/
rules.py`. Suppose the VLM reads "Temperature: 98.6" off a scanned
intake form and outputs `temperature: 98.6` without a unit — which
is what would happen on a poorly-scanned form where the °F symbol
was illegible. The rule runs after the LLM responds. It scans the
response text for vital-sign claims with a number, checks whether
the cited row's `unit_verified` flag is `True`. If not, AND the
response doesn't include the literal string "(units not recorded)"
or "unit unknown", the rule rejects the response and retries.

So in practice you see the agent return something like:

> *"Temp 98.6 °F (units not recorded in a verified unit field;
> value and unit string as stored) [Observation#…]"*

That parenthetical isn't decorative — it's the verifier forcing
the model to disclose the uncertainty. The alternative — silent
pass-through of "Temperature: 98.6" — is the failure mode the rule
prevents.

**Same idea on medications.** The `med_active_state_rule` rejects
"currently on metformin 500mg BID" if no cited row has `active=true`
AND an open `end_date`. So the model can't hallucinate active
medications based on a stale prescription record.

**Trade-off.** The schemas + verifier won't catch a *plausible-but-
wrong* extraction — if the VLM reads "Hemoglobin A1c: 7.4" but the
actual value on the form is "5.4", and pdfplumber confirms the
digit position — the citation passes structurally. The defense for
that class of error is the `extraction_lab` eval category — 8 cases
including intentionally noisy fixtures with known ground-truth
values, run on every PR against the deployed agent. With more time
I'd add OCR cross-validation as a second-pass on numeric fields
specifically (Tesseract or AWS Textract over the bbox region, then
cross-check against the VLM's transcribed value). Mentioned in
W2_ARCHITECTURE.md §10 "Open questions."
