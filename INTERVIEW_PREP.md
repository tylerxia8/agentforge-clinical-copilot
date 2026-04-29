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
