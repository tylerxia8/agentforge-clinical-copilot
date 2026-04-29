# Demo video script — MVP submission

> **Target length: 3:30–4:30** (case study allows 3-5; aim for the
> middle of the range — it forces tight delivery).
>
> **Tools:** screen recorder of your choice (Loom / OBS / QuickTime).
> One take is fine. Don't over-edit. Speak at conversational pace.
> The bar is "would a hospital CTO understand the project from this
> alone?", not "is this Apple Keynote-grade".
>
> Each section below has: time budget · what to show · what to say
> (talking points, not a word-for-word script). Adapt the wording to
> sound like you.

---

## 0:00–0:25 — Cold open & framing (25 sec)

**Show.** Webcam corner if you want; main view is your editor open to
[README.md](README.md).

**Say.**
> "AgentForge Clinical Co-Pilot. I'm building an AI agent inside
> OpenEMR — the open-source EHR — for a primary care physician with a
> 20-patient day. The case study scenario is the 90 seconds between
> exam rooms, when she needs to know who she's about to see, what's
> changed, and what matters today. The audit and the architecture
> for that build are this MVP."

**Why this open.** Tells the viewer who, what, and why in 25 seconds.
No "hi everyone" — get to the substance.

---

## 0:25–1:25 — The audit, in one finding (60 sec)

**Show.** Open [AUDIT.md](AUDIT.md). Scroll to the **Summary** at the
top, then jump to **§1.2** (the patient-level ACL gap), then to
**§4.6** (vitals with no unit column).

**Say.**
> "I ran a five-dimension audit before any AI work — security,
> performance, architecture, data quality, compliance. The headline
> finding shaped everything: OpenEMR's authorization is role-based,
> not patient-based. Any clinician with the `patients/med` permission
> can read any patient via the API. The interface enforces what
> screen you're on; the API surface doesn't. An agent layered on
> that inherits the same blast radius — and a chat UI invites
> broader queries than a chart click does. So the agent must
> enforce a per-turn patient-context boundary above the existing
> ACL.
>
> The other finding I want to flag: vitals. `form_vitals` stores
> weight, height, and temperature as decimals with no unit column.
> A value of 37 could be normal °C or fatal °F. The agent can't
> guess — it has to surface vitals with explicit 'unit unknown'
> provenance. That's a verification rule I write deterministically,
> not a thing I trust the model on."

**Why this section.** Demonstrates the audit had teeth and shaped
real architecture decisions. Pick *one* security finding and *one*
data-quality finding — don't try to summarize all five sections in
60 seconds.

---

## 1:25–2:00 — The user, in one use case (35 sec)

**Show.** Open [USERS.md](USERS.md). Scroll to **§3 / UC-1**
(pre-visit snapshot).

**Say.**
> "I picked a primary care physician — Dr. M — over an ED resident
> or hospitalist because the 90-second pre-visit moment is most
> acute in primary care and the data is most longitudinal. Every
> agent capability I'll build traces back to one of four use cases
> in this doc.
>
> The headline use case is the pre-visit snapshot. The agent reads
> the chart, diffs it against last visit, and surfaces what's
> changed — A1c trend, new meds, recent ED visits, screenings due.
> A dashboard shows all of that data; an agent prioritizes it, in
> the right format, for this patient. That's the case for an agent
> instead of a better dashboard, and I have to defend it for every
> capability I add."

---

## 2:00–3:15 — The architecture, in five decisions (75 sec)

**Show.** Open [ARCHITECTURE.md](ARCHITECTURE.md). Scroll to the
**Summary** (the five numbered decisions) and the **system diagram**
in §1.

**Say.**
> "Five architectural decisions, each tied to an audit finding.
>
> One — the agent runs in a separate Python service, not in PHP.
> The Python ecosystem for tool-using LLMs is years ahead. The
> OpenEMR PHP module is responsible for one thing: rendering the
> chat panel and proxying authenticated turns.
>
> Two — patient-context middleware is hard, not soft. Every tool
> call carries a patient UUID derived from the open chart. Tools
> that try to read across that boundary fail closed. This is the
> closure for the audit's biggest finding.
>
> Three — PHI gets redacted before the LLM ever sees it. Names,
> MRNs, full DOBs, phone, email get tokenized into stable
> placeholders. The token map lives in request scope, never leaves
> the process, never reaches the model. Responses are re-hydrated
> for the UI.
>
> Four — verification is deterministic, not 'trust the model'.
> Every clinical claim has to inline-cite a row that a tool actually
> returned. A regex pass + domain rules + an LLM-as-judge for
> ambiguous cases enforce it. Three failures, the agent refuses
> with a verified-facts-only fallback.
>
> Five — there's a per-patient context cache that warms on chart
> open. The first turn after the doctor opens the chart reads from
> Redis, not from MariaDB. That moves the latency floor from
> 350-400 milliseconds down to under 100, which puts the LLM
> round-trip back as the dominant cost — where it should be."

**Why 75 seconds for five decisions.** This is the densest section
of the video. ~15 seconds per decision. Practice this once before
recording. The five decisions are the architecture interview's
opening question.

---

## 3:15–4:00 — Show the running app (45 sec)

**Show.** Three quick switches:

1. **Browser:** your deployed Railway URL → log in as `admin` →
   land on the OpenEMR dashboard. Click into a demo patient's chart.
2. **Editor:** [agent-service/](agent-service/) tree expanded —
   `orchestrator.py`, `middleware/patient_context.py`, the test
   files. Don't read the code aloud; just show it exists.
3. **Terminal:** `cd agent-service && pytest` → 11 passing tests.

**Say.**
> "The OpenEMR fork is live on Railway — link in the README. Local
> dev is a single `docker compose up`. The agent service is the
> Python skeleton that implements those five architecture decisions
> — patient-context middleware, redaction, structural and domain
> verification, the orchestrator. The spine is tested. Tools are
> stubbed; that's Thursday's work."

**Why this section.** This is the "deployed app" deliverable. Show
it works. Don't try to demo the agent — there isn't one yet, and
the case study explicitly says the MVP is the foundation, not a
working agent.

---

## 4:00–4:30 — Roadmap and close (30 sec)

**Show.** Scroll to **[ARCHITECTURE.md §11 — Roadmap](ARCHITECTURE.md)**.

**Say.**
> "Thursday — the agent runs end-to-end against the demo data, with
> the verification layer live and an eval suite reporting pass
> rates. Sunday — Synthea-derived adversarial cases, the
> LLM-as-judge wired in, the encounter-open cache, and the cost
> dashboard. Everything I build between now and Sunday traces back
> to either an audit finding or a use case in [USERS.md](USERS.md).
> Thanks."

**Don't.** Recap the video. Don't say "and that's my project." Stop
when you've made the last point.

---

## Recording checklist

- [ ] Close Slack, Discord, email — no notification sounds.
- [ ] One screen, one window per cut. Don't fight tab clutter.
- [ ] Mic test for 10 seconds before the real take. Listen back.
- [ ] First take is usually overlong. Watch yours; you'll find 30+
      seconds to cut.
- [ ] Screen text needs to be readable — bump font size to 16+
      in your editor and ~110% browser zoom.
- [ ] Export at 1080p. Loom default is fine.
- [ ] **Upload, get a public link, paste it into the submission
      and into [README.md](README.md).**

## Common failure modes (skip these)

- **Reading the docs aloud.** The viewer can read; you're there to
  pull out the parts that matter and explain what's not on the
  screen.
- **Apologizing.** "I didn't get to wire the agent yet…" — don't.
  The MVP isn't supposed to have a working agent. Frame it as
  scope, not gap.
- **Demoing fake data.** Don't pretend the agent works by
  hand-waving over an empty UI. Show the audit, plan, and
  deployed-OpenEMR; promise the agent for Thursday.
- **The passive voice creep.** "It was decided that the agent
  would run in a separate service." → "I picked a separate Python
  service because…"

## What to put in the submission alongside the video

- The video URL (Loom / YouTube unlisted / similar).
- The Railway URL of the deployed OpenEMR.
- The GitHub repo URL.
- Confirmation that AUDIT.md, USERS.md, ARCHITECTURE.md are at the
  repo root and start with the required ~500-word summary.
