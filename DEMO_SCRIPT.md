# Demo video script — Sunday final submission

> **Target length: 3:30–4:30** (case study allows 3-5; the middle of
> the range is the right pace).
>
> **Tools:** Loom is fine. Single-take is fine. The Sunday cut is the
> headline product moment — between Thursday and now, the agent went
> from one tool (medications) to three (medications + problems +
> allergies), the eval suite expanded 6 → 9 cases all green, and
> Langfuse traces are wired through every turn. The standalone chat
> UI rendering this synthesis is the moment to lead with.
>
> **What's different from Thursday's script:** Thursday's video opened
> on a curl in a terminal. Sunday's opens on the **browser UI** with
> the **UC-1 90-second snapshot** — meds + problems + allergies +
> clinical reasoning, all cited inline. That's the headline product
> moment of the project.

---

## 0:00–0:30 — Cold open: the headline UC-1 snapshot (30 sec)

**Show.** Browser tab one — `https://copilot-agent-production-ba87.up.railway.app/`.

**Pre-stage.** Have the page already loaded with Farrah selected.
Have the suggestion *"Quick read on this patient."* visible. Hit
record, then click the suggestion. Talk while the response renders
(~13 seconds for this multi-tool synthesis).

**Say.**
> "AgentForge Clinical Co-Pilot. Live in production. Pre-visit
> snapshot — what a primary care physician needs in the 90 seconds
> between rooms. One click. Real chart, real Anthropic Claude, real
> FHIR data."

**As the response renders, narrate:**
> "Hypertension. Type 2 diabetes. Lisinopril, Atorvastatin. A
> Penicillin allergy that's *confirmed*, not just on file.
> Five citations across three FHIR resource types — every clinical
> claim traceable to a specific row. And the model volunteered
> something good: Lisinopril is renoprotective for diabetics. Useful
> at the bedside, not just summarized."

**Why.** This is the strongest 30 seconds in the entire video. Lead
with the product working, with real synthesis, with citations the
viewer can scan. Don't recap Thursday. Don't say "let me show you."

---

## 0:30–1:30 — What the agent WON'T do (60 sec)

Three suggestion buttons in the same browser tab — empty chart,
cross-patient, prompt injection — clicked in sequence.

**Beat 1: empty chart.**
- Click **Ted Shaw** in the sidebar (Ted has zero medications, zero
  problems, zero allergies on file)
- Click **"Quick read"**

**Beat 2: cross-patient.**
- Click **Farrah Rolle** to switch back
- Click **"Tell me about Bob Smith's medications…"**

**Beat 3: prompt injection.**
- Click **"Ignore previous instructions…"**

**Say (over the three demos, ~20 sec each):**
> "Three things this product has to refuse — or it's indefensible.
> Empty chart: zero medications and the agent says so. No drug names,
> no fabrication. Cross-patient: I'm in Farrah's chart but asking
> about Bob Smith. The agent stays in scope, won't even acknowledge
> the other patient by name. Prompt injection — 'ignore previous
> instructions' — same outcome. The boundary holds because it's a
> code path in the agent service, not a sentence in a prompt the
> model could be talked out of."

**Why.** A demo that only shows the happy path is a sales pitch.
This minute is what a hospital CTO is actually evaluating.

---

## 1:30–2:00 — Observability: the Langfuse trace (30 sec)

**Show.** Switch to a second browser tab — `https://us.cloud.langfuse.com`,
your project's **Tracing → Traces** view. The traces from the
preceding four turns should already be there.

Click into the most recent trace (the prompt-injection one).

**Say.**
> "Every turn is a trace. Per-tool latency, every LLM generation
> with input + output token counts, model name, cost, and the final
> output. Tagged with the patient session, refused vs accepted.
> Six seconds end-to-end on a typical turn. About two cents at the
> Sonnet 4.6 list price."

**Don't.** Click into nested spans for a long time — the dashboard
is more impressive than narrating it. ~25 seconds of camera time on
the Langfuse view is plenty.

---

## 2:00–2:30 — Eval suite: 9/9 (30 sec)

**Show.** Switch to terminal. Run:
```bash
./demo/5-evals.sh
```

The full markdown table prints in ~50 seconds. **Cut to fast-forward**
in editing — the recording captures it real-time, but in the final
video skip ahead so the viewer sees the table appear at human pace.

Alternative: have a recent run already pasted in the terminal so the
"results" show immediately.

**Say:**
> "Nine integration cases run real chat turns at the deployed
> service and grade the responses. Boundary refusals, citation
> validity, the UC-1 snapshot, fabrication-on-empty, cross-patient
> leakage, prompt injection — every load-bearing property the
> architecture has to defend. Nine out of nine passing right now.
> Latencies four to thirteen seconds, comfortably inside the
> latency budget I projected on Tuesday."

---

## 2:30–3:15 — Architecture in 45 seconds (45 sec)

**Show.** [ARCHITECTURE.md](ARCHITECTURE.md), the five-decision
summary at the top.

**Say.**
> "Five architectural decisions, each tied to an audit finding.
>
> One — agent runs in a separate Python service. Cleaner blast
> radius, real ecosystem for tool-using LLMs.
>
> Two — the patient-context middleware is a code path, not a
> prompt instruction. The cross-patient and injection cases held
> because every tool call carries the open chart's UUID and the
> middleware fails closed.
>
> Three — PHI is tokenized before reaching the LLM. Names, MRNs,
> full DOBs become placeholders. The token map lives in request
> scope only.
>
> Four — verification is deterministic, not 'trust the model'.
> Every clinical claim has to inline-cite a row a tool actually
> returned. Three failures, the agent refuses with a verified-
> facts-only response.
>
> Five — encounter-open cache pre-fetches the per-patient bundle
> in Redis. First chat turn reads hot data. That's why the
> snapshot was thirteen seconds, not thirty."

---

## 3:15–3:45 — Cost reality (30 sec)

**Show.** [COSTS.md](COSTS.md) — scroll to the summary table.

**Say.**
> "Per-physician cost stays in a twenty-one to twenty-eight dollar
> per month band across four orders of magnitude scale. The LLM
> call is the dominant per-turn cost and it scales linearly. What
> changes discontinuously is operations and compliance overhead at
> the ten-thousand and hundred-thousand user tiers — Bedrock with
> PrivateLink, on-call rotation, eval-gated CI. Not the AI bill.
>
> Real dev spend so far: about a dollar twenty in Anthropic
> credits. The full breakdown, including the scale inflection
> points, is in COSTS.md."

---

## 3:45–4:15 — Honest framing + roadmap (30 sec)

**Show.** Editor with [interface/modules/custom_modules/oe-module-clinical-copilot/](interface/modules/custom_modules/oe-module-clinical-copilot/)
expanded.

**Say.**
> "What you saw is the standalone web UI for the agent. The
> production target is an embedded chat panel that renders into the
> patient chart in OpenEMR — the source ships in this repo, the
> module is 7.0.3-compatible, runs locally on docker-compose. The
> Docker bake-into-Railway deploy hit a healthcheck timeout this
> weekend; that's a known boot issue I'll resolve with a local
> repro.
>
> What's next, in priority order: eval expansion to eighty cases
> with Synthea-derived adversarial patients, encounter and lab
> tools, custom multi-stage Docker build that gets the module
> rendered onto the deployed chart. Everything traces back to
> either an audit finding in [AUDIT.md](AUDIT.md) or a use case
> in [USERS.md](USERS.md). Thanks."

**Don't.** Recap the video. Don't say "and that's it."

---

## Pre-recording prep — the browser UI is your stage

Open these in tabs **before recording**, in this order:

1. **Tab 1:** `https://copilot-agent-production-ba87.up.railway.app/`
   with Farrah pre-selected. Window-size to ~1280x800. Browser zoom
   115%.
2. **Tab 2:** `https://us.cloud.langfuse.com/` → your project →
   Tracing → Traces. Already filtered to the last hour.
3. **Tab 3:** GitHub repo at `https://github.com/tylerxia8/agentforge-clinical-copilot`
   for the cold reference if needed.
4. **Terminal window** with `./demo/5-evals.sh` ready to paste.
5. **Editor** with [DEMO_SCRIPT.md](DEMO_SCRIPT.md), [ARCHITECTURE.md](ARCHITECTURE.md),
   and [COSTS.md](COSTS.md) open.

Practice the cold open once. The UC-1 response is the strongest
moment in the entire video — don't read the response aloud, narrate
it as it renders.

## Recording checklist

- [ ] Close Slack / Discord / mail
- [ ] Browser zoom 115%, terminal font 16+, editor font 16+
- [ ] Pre-fire one chat turn so the LLM is "warm" (cache hit on
      next request → faster response on camera)
- [ ] Mic test 10s before the real take
- [ ] First take usually overlong. Watch yours; you'll find 30+
      seconds to cut
- [ ] Export 1080p
- [ ] **Upload, get a public link, paste into the submission and
      into [README.md](README.md)**

## What to put in the final submission

- Demo video URL (Loom / YouTube unlisted / similar)
- GitHub repo: https://github.com/tylerxia8/agentforge-clinical-copilot
- OpenEMR Railway: https://openemr-production-0996.up.railway.app/
- Agent UI: https://copilot-agent-production-ba87.up.railway.app/
- Eval results: [agent-service/evals/results.md](agent-service/evals/results.md) (9/9)
- Cost analysis: [COSTS.md](COSTS.md)
- Social post: link to your X / LinkedIn post per [SOCIAL_POST.md](SOCIAL_POST.md)
- AI interview required within 24h — interview prep in [INTERVIEW_PREP.md](INTERVIEW_PREP.md)
