# USERS.md — Who the Clinical Co-Pilot is for

> The case study is explicit: *"Physicians need help finding information"*
> is not a user definition. This document picks one user, one workflow,
> and the use cases the agent will defend.
>
> Every capability proposed in [ARCHITECTURE.md](ARCHITECTURE.md) traces
> back to a use case here. If a capability cannot be tied to a row in §3
> below, it is out of scope.

---

## 1. The user

**Dr. M — primary care physician at a 4-provider community clinic.**

- Board-certified in family medicine; 6 years out of residency.
- Panel of ~1,400 patients; sees ~20 per day in 15-minute and
  30-minute slots (annual physicals, follow-ups, acute visits, a
  handful of new-patient intakes per week).
- Uses OpenEMR as the practice's primary EHR. Comfortable with the
  patient chart view, less comfortable with anything that requires
  clicking into more than two screens during a visit.
- Carries a Surface tablet between exam rooms; the OpenEMR session
  follows her through the day on a single browser tab.
- Handles her own inbox: lab results, prior-auth requests, refill
  requests, patient-portal messages. None of this is the agent's job
  in v1, but it is the texture of her day.

**Why a PCP and not an ED resident or hospitalist?**

The case study lists three plausible users. We picked the PCP because:

1. **The 90-second-between-rooms scenario is most acute here.** ED
   physicians have triage notes prepared by the nurse; hospitalists
   have a sign-out from the night team. The outpatient PCP walks into
   the room cold. The pre-visit moment is the one where a co-pilot
   delivers the most leverage per query.
2. **The data is more longitudinal.** A PCP's relationship with a
   patient spans years — chronic disease tracking, screening
   intervals, medication trends. This rewards an agent that can
   synthesize across the full record, not just today's note.
3. **The latency budget is realistic.** Hospitalist rounding tolerates
   a 30-second answer; ED triage demands sub-5-second. PCPs can
   tolerate the 3–5 seconds that an LLM round-trip honestly takes.
4. **The error surface is well-bounded.** The PCP makes most decisions
   in the room with the patient present — there is a human-in-the-loop
   on every action. This makes a v1 agent ethically deployable in a
   way that a fully autonomous discharge-planning agent for a
   hospitalist would not be.

The PCP is also where the data-quality landmines from
[AUDIT.md](AUDIT.md) §4 hurt the most: chronic medication lists,
problem lists with mixed ICD-9/ICD-10 history, lab trends with
inconsistent units. The agent has to reason about exactly the
schema warts that an outpatient workflow surfaces.

---

## 2. A day in Dr. M's clinic

Times approximate, mapped to where the agent enters the workflow.

| Time | What Dr. M is doing | Where the agent helps |
|------|---------------------|-----------------------|
| 7:45–8:00 | Reviews today's schedule on her tablet, coffee in hand | **UC-4** (schedule pre-read) |
| 8:00 | First patient. 30-min annual physical — she's seen him 4× before | **UC-1** before, **UC-2** during |
| 8:30 | 15-min follow-up. Diabetic patient, last A1c was 8.2 | **UC-1** + **UC-2** (lab trend) |
| 8:45 | New patient. Walks in cold, no records imported | Agent steps back — out of scope |
| 9:15 | 90 seconds between rooms — she's behind. Next is acute knee pain | **UC-1** (snapshot only) |
| 9:30 | 78yo with CHF on 11 meds, here for "feeling tired" | **UC-1** + **UC-3** (med reconciliation) |
| ... | ... | ... |
| 12:00 | Lunch. Eats at her desk, opens her message inbox | Out of scope for v1 |
| 1:00 | Afternoon block resumes | Same patterns |
| 5:30 | Last patient out. Pajama-time charting begins | Out of scope for v1 |

The agent's working envelope is **8:00 AM to 5:30 PM**, the
in-room/between-rooms window. Charting and inbox are deferred to a
later iteration so that v1 can be excellent at one thing instead of
mediocre at three.

---

## 3. Use cases (the source of truth)

Each use case below has:

- **Trigger** — what Dr. M is doing when she invokes it.
- **What she asks** — example utterances.
- **What the agent returns** — shape of the answer.
- **Data sources** — which OpenEMR services/tables back it.
- **Why an agent** — the explicit defense the case study requires.
- **Failure modes** — what the agent must do when the data is
  missing, ambiguous, or contradictory.
- **Refusal patterns** — what it must not answer, even if asked.

---

### UC-1 — Pre-visit snapshot ("the 90-second case")

**Trigger.** Dr. M opens a patient chart in OpenEMR, or types
`/snapshot` in the co-pilot panel, in the moments before walking in.

**What she asks.**
- *(implicit, opening the chart is the trigger)*
- "Quick read on my next patient"
- "What's changed since I last saw her?"

**What the agent returns.** A bulleted, scan-in-15-seconds summary:

```
Sarah K., 64F, here for: 6-month diabetes follow-up
What's changed:
  • A1c down to 7.1 (was 8.2 in Oct) — metformin titration is working
  • Started gabapentin 300mg HS in Dec (neuropathy) — verify tolerating
  • One ED visit Jan 8 for hypoglycemia — no admission, dose-adjusted on discharge
Active meds (5): metformin 1000 BID, lisinopril 20, atorvastatin 40,
  gabapentin 300 HS, ASA 81  ⚠ allergy: sulfa
Today, you should probably:
  • Check feet (last documented foot exam: Apr 2024 — overdue)
  • Confirm eye exam scheduled (last: 2023)
  • Renal panel hasn't been ordered since the lisinopril dose change
[Sources: forms#1042, prescriptions#88-92, encounter#7714, lists#244]
```

**Data sources.** `PatientService::getOne()`, `EncounterService` (last
3 encounters), `ConditionService` (active problems), the medication
join from [AUDIT.md](AUDIT.md) §4.2, recent vitals, recent labs,
preventive-care due dates from `clinical_rules`.

**Why an agent.**
- A dashboard already shows all of this — but it shows *all* of it.
  The PCP needs the *prioritized* view, and what's prioritized depends
  on the patient's current problem mix and reason for visit. This is
  exactly the kind of "synthesize across data sources, format for the
  current context" task that loses fidelity in any fixed UI.
- The "what's changed" framing is conversational — it requires
  diffing two states ("what I knew last visit" vs. "what's true now")
  and rendering only the delta. That is awkward as a tab in the
  patient chart and natural as a chat reply.
- Follow-ups (UC-2) chain off this — and the same surface should
  handle both the snapshot and the drill-in.

**Failure modes.**
- *No prior encounter on file.* Agent says so explicitly: "No prior
  encounters in this record — treating this as a new-patient view."
  Falls back to UC-2-style point lookups instead of "what changed".
- *Encounter on file but no chief complaint.* Says "Visit reason not
  documented" rather than fabricating one.
- *Vitals with no unit ([AUDIT.md](AUDIT.md) §4.6).* Reports the raw
  value with `(units not recorded)` rather than guessing.
- *Conflicting medication lists between `prescriptions` and `lists`.*
  Reports the conflict as a flag (UC-3 territory), does not silently
  pick one.

**Refusal patterns.**
- Will not summarize a patient outside Dr. M's care relationship —
  the patient-context middleware (see [AUDIT.md](AUDIT.md) §1.2 / §6,
  [ARCHITECTURE.md](ARCHITECTURE.md) §3) blocks the tool call
  before it runs.
- Will not infer diagnoses the chart does not state. ("Probably
  has metabolic syndrome" → no, unless coded.)

---

### UC-2 — Targeted lookup during the visit

**Trigger.** Dr. M is mid-conversation with the patient and needs one
specific fact from the chart she can't recall.

**What she asks.**
- "When was her last colonoscopy?"
- "What did the cardiology consult say in November?"
- "Has her HbA1c trend been improving?"
- "Is she up to date on her tetanus?"

**What the agent returns.** One sentence + the source.

```
Last colonoscopy: 2019-04-12 (normal, 10-year recall).
[Source: procedure_report#1411]
```

```
HbA1c trend (last 4):
  2024-04: 8.7
  2024-10: 8.2
  2025-04: 7.5
  2025-10: 7.1   ↓ improving
[Source: procedure_result codes 4548-4]
```

**Data sources.** Procedure history, immunizations, problem list,
encounters with note bodies (selectively).

**Why an agent.**
- These questions are *the* reason a typed search bar fails: the user
  doesn't want to navigate to a tab, run a filter, scroll, and read.
  She wants to keep eye contact with the patient and ask out loud.
  Voice-input for this UX is a v2 — even text-input it shaves a
  meaningful number of seconds off the flow.
- Several of these questions require synthesis the schema doesn't
  give for free. "Is she up to date on tetanus?" requires reading the
  immunization list, knowing the 10-year interval, and comparing to
  today. A dashboard either shows the raw immunization history (work
  for the doctor) or pre-computes everything (and is wrong when the
  rules change). An agent does the right amount of computation per
  question.

**Failure modes.**
- *Question requires data not in the chart.* "I don't have a
  cardiology consult on file from November — the most recent
  cardiology note is from 2023-08-15." Does **not** generalize from
  primary-care notes ("looks like she has heart issues").
- *Trend with too few data points.* Reports the values without
  drawing a trend line.

**Refusal patterns.**
- Will not interpret out-of-domain values it has not been verified on.
  ("The CT report says X, what does that mean?" → "I can pull the
  report — clinical interpretation is yours.")

---

### UC-3 — Reconciling conflicting / messy chart data

**Trigger.** Dr. M notices something doesn't add up — usually a
medication or problem-list inconsistency. This is the
data-quality-meets-clinical-reasoning moment from [AUDIT.md](AUDIT.md)
§4.

**What she asks.**
- "Is she actually still on lisinopril? I see it twice."
- "Why does the problem list have both ICD-9 and ICD-10 hypertension?"
- "Her allergy list says sulfa but the chart shows Bactrim was
  tolerated — what's the story?"

**What the agent returns.** An explanation grounded in the underlying
records, with the conflict made explicit:

```
Lisinopril appears in two places:
  • prescriptions #244 — 20 mg PO daily, written 2024-03-12, marked active=1, no end_date
  • lists #588 (medication) — 10 mg PO daily, started 2023-01-04, never marked inactive
The 20 mg prescription is the most recent and what the pharmacy is
filling. The 10 mg entry on the problem-list view is a stale duplicate
— common in OpenEMR when a dose change isn't propagated. I'd
recommend marking lists#588 inactive.
[Sources: prescriptions#244, lists#588]
```

**Data sources.** Medications join (audit §4.2), problem-list
deduplication logic (audit §4.4), allergy + adverse-event linking.

**Why an agent.**
- This is the hardest case to defend as needing an LLM, and also the
  one where a static UI is most clearly inadequate. A dashboard
  *shows* both rows. A doctor still has to figure out what they mean.
  The agent's job here is not retrieval — it is **explanation**, and
  it requires (a) knowledge of OpenEMR's specific data-quality
  patterns, (b) the discipline to cite sources, and (c) the
  willingness to say "this is a data issue, not a clinical issue".
- This is also the use case that most clearly needs the verification
  layer ([ARCHITECTURE.md](ARCHITECTURE.md) §4): every claim must
  be tied back to a row, and the agent must be able to honestly say
  "I don't know which is correct."

**Failure modes.**
- *Genuinely ambiguous case the agent can't resolve.* Reports the
  ambiguity, recommends Dr. M reconcile it, does **not** pick a
  winner.

**Refusal patterns.**
- Will not silently update or "clean up" a record. All writes are
  out of scope for v1 — the agent reads, surfaces, and recommends.

---

### UC-4 — Morning schedule pre-read

**Trigger.** Dr. M opens her schedule for the day at ~7:50 AM. The
agent runs across all of today's patients in one pass.

**What she asks.**
- *(implicit on schedule open, opt-in)*
- "Anything I should be flagging on today's schedule?"

**What the agent returns.** A short triage list, one line per
patient that has something noteworthy. Patients with nothing flagged
are not mentioned.

```
8:30 - Sarah K. — A1c improved (7.1) but renal panel overdue since dose change
9:30 - Robert T. — ED visit Jan 8 for hypoglycemia; here for "feeling tired"
10:00 - Maya L. — abnormal mammogram from Dec, needs results discussed
2:15 - Frank D. — prior-auth for Eliquis pending since Dec, may come up
(8 other patients on schedule with nothing flagged)
```

**Data sources.** Today's schedule (`openemr_postcalendar_events`),
plus a per-patient mini-context of recent labs / ED visits / pending
items.

**Why an agent.**
- This is a "first principles" use case for an LLM: it requires
  reading 20 charts in parallel, comparing across them, picking out
  what's noteworthy *for this physician's workflow*, and surfacing
  only what matters. A dashboard that pre-renders everything is too
  noisy; a saved query is too narrow.
- It's also the highest-value use case for the doctor's time — five
  minutes of agent prep at 7:50 AM saves five minutes of in-room
  surprise across the day.

**Failure modes.**
- *Slow query due to 20-patient fan-out.* The pre-read is the use
  case where the patient-context cache from [ARCHITECTURE.md](ARCHITECTURE.md) §5
  matters most; it should run as a background job that begins when
  the schedule loads, not on demand.
- *Nothing noteworthy on any patient.* Says so. "Nothing on today's
  schedule needs flagging — quiet day."

**Refusal patterns.**
- Will not flag a patient based on demographic factors alone.
  ("65-year-old male" is not a reason to flag.) Flags must be
  rooted in chart events.

---

## 4. What is explicitly out of scope for v1

Naming what the agent will not do is as important as naming what it
will. The following are tempting and we are leaving them out:

| Capability | Why deferred |
|------------|--------------|
| **Drafting visit notes / SOAP A&P** | Note authorship is a distinct ethical and verification problem; conflating it with the in-room assistant blurs accountability. |
| **Ordering / writing prescriptions** | The agent reads only in v1. Writes require a separate signature flow and a much harder verification surface. |
| **Patient-portal messaging on the doctor's behalf** | Agent ↔ patient interaction has its own consent and tone surface. |
| **Triaging the inbox / signing labs** | Different mental context (between-visits, not in-visit). Different latency tolerance. Worth its own user definition. |
| **Voice input** | Useful in the long run, not a v1 differentiator. Browser support is uneven. |
| **Cross-clinic data (HIE, payor records)** | Outside the OpenEMR data surface. |
| **Decision support that prescribes a course of action** | Agent surfaces information; the doctor decides. UpToDate-as-a-prompt is a v3 conversation. |

If a stakeholder asks the agent to do one of these, the agent says
"I'm not equipped for that yet — here's what I can help with" and
lists the four use cases above.

---

## 5. Success criteria

How we will know v1 is working for Dr. M:

1. **Pre-visit snapshot (UC-1) is correct on demo and adversarial
   patients.** Every claim has a source. No hallucinated labs, meds,
   or diagnoses. Conflicts are surfaced, not papered over.
2. **First-token latency under 3 seconds, full answer under 6 seconds**
   for UC-1 and UC-2 on a cached patient context. (See
   [AUDIT.md](AUDIT.md) §2.5 for the budget derivation.)
3. **Patient-context boundary is enforced** — the agent refuses to
   surface data about any patient outside the currently-open chart.
   Eval set ([ARCHITECTURE.md](ARCHITECTURE.md) §6) includes
   adversarial prompts that try to break this.
4. **Vitals with no unit are reported as such** — no silent °C / °F
   guessing. Eval set includes a known-bad-units patient.
5. **Dr. M would actually open the panel.** Validated by a 5-doctor
   walk-through with a think-aloud script (out of scope for the
   build deadline; included as a follow-up).

The bar from the case study — *"the agent is the thing the user
would actually choose"* — is the standard. Items 1–4 are
necessary conditions; item 5 is the sufficient condition.

---

## 6. Traceability matrix

Every capability proposed in [ARCHITECTURE.md](ARCHITECTURE.md) ties
back to one of the use cases above. This is the contract:

| Architecture capability | Justified by |
|-------------------------|--------------|
| Multi-turn conversation | UC-2 follow-ups; UC-1 → UC-3 chains |
| Tool calling (patient_lookup, lab_history, med_list, …) | UC-1, UC-2 |
| Verification + source attribution | UC-1, UC-2, UC-3 (every output cites rows) |
| Patient-context middleware | UC-1, UC-2, UC-3, UC-4 (boundary enforcement) |
| Per-patient context cache | UC-1 latency, UC-4 background prep |
| PHI redaction layer | All UCs (BAA / minimum-necessary) |
| Audit logging of every tool call | All UCs (HIPAA §164.312(b)) |
| Schedule-aware background pre-read | UC-4 only |

Anything not in the right column is out of scope.
