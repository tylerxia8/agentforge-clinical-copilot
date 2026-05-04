# W2_ARCHITECTURE.md — Multimodal Evidence Agent

> Reads on: [ARCHITECTURE.md](ARCHITECTURE.md) (Week 1 design we're
> extending), [AUDIT.md](AUDIT.md) (the security constraints, still
> binding), [USERS.md](USERS.md) (the use cases), and the Week 2 PRD.
>
> What this document is: the plan we will defend at the four-hour
> Architecture interview and execute against the Tuesday MVP, Thursday
> early submission, and Sunday final.

---

## Summary

The Week 2 agent extends Week 1's single-agent tool-using LLM with
**two new capabilities — vision and routing — without changing the
security spine**. The user surface is the same embedded chat panel.
The patient-context middleware, redaction layer, FHIR bridge,
verification regex, persistent volume, eval harness, and Langfuse
traces from Week 1 all carry over. What we add is:

1. **Document ingestion**: a single tool, `attach_and_extract(patient_id,
   file, doc_type)`, that accepts a lab PDF or intake form, stores the
   source in OpenEMR's `documents/` (now on the persistent volume),
   calls Claude Sonnet 4.6 with the PDF as image input under a strict
   Pydantic schema, and writes derived facts back as FHIR
   `Observation`s + an OpenEMR `documents` row. Every extracted field
   carries a `{page, bbox, source_quote}` so the UI can highlight the
   exact span on click.
2. **Hybrid RAG over a small clinical-guideline corpus**: BM25 +
   Voyage `voyage-3` dense, deduplicated, reranked with Cohere Rerank
   3 to top-5. The corpus is ~40 hand-curated USPSTF / AAFP / NIH
   excerpts relevant to the PCP profile — small enough to fit in
   memory, narrow enough to defend.
3. **Supervisor + two workers** orchestrated with **LangGraph**: an
   `intake-extractor` worker that drives `attach_and_extract`, an
   `evidence-retriever` worker that drives the hybrid RAG, and a
   supervisor that decides which worker (if any) to call before the
   final-answer step. Handoffs are LangGraph edges; each is a
   Langfuse span.
4. **Eval gate, 50 cases, boolean rubrics, PR-blocking**: extends
   Week 1's `agent-service/evals/run.py`. Five categories per the PRD
   (`schema_valid`, `citation_present`, `factually_consistent`,
   `safe_refusal`, `no_phi_in_logs`). A GitHub Actions workflow runs
   the suite against the deployed staging service on every PR; merge
   is blocked if any category drops by >5% or below 90%.
5. **Citation contract upgrade**: every clinical claim now carries
   machine-readable `{source_type, source_id, page_or_section,
   field_or_chunk_id, quote_or_value}` in a structured `citations[]`
   array on the response, in addition to the inline
   `[Resource#uuid]` markers Week 1 already enforces. The chat panel
   renders click-through chips that open a PDF preview with the
   extracted bbox highlighted.

**Stack delta from Week 1:**
- `langgraph>=0.2` for the supervisor/worker graph
- `voyageai>=0.3` for dense embeddings (Anthropic-aligned partner)
- `cohere>=5.13` for the reranker
- `pdfplumber>=0.11` for word-level bbox extraction (ground truth for
  the bbox overlay; we don't trust the VLM's claimed coordinates)
- `pypdfium2>=4.30` for rendering PDF pages to PNG when we need an
  image-only modality (Anthropic accepts PDFs directly, but image
  fallback is useful for noisy scans)

**Stack carried forward unchanged:**
Python 3.12 + FastAPI, Anthropic SDK, Redis, MariaDB, Langfuse,
embedded panel CSS/JS, OpenEMR PHP module, Railway volume on
`sites/default/documents/`.

**What's deliberately NOT in W2 core** (per PRD: "narrower than the
original spec and stronger because of it"):
- Critic agent (extension, not core)
- Third document type (referral fax, med list — extension)
- Lab trend chart widget (extension)
- ColQwen2 / multi-vector indexing (stretch)
- Writes for orders, prescriptions, or note authorship (still W2-out)

---

## 1. Document ingestion flow

The flow is one round-trip from the user clicking **Attach** in the
chat panel to a structured set of `Observation` resources written
back to the chart. Every step has a logged Langfuse span.

```
[User] → upload → [PHP module]
                    │
                    ├── stores PDF on Railway volume at
                    │   sites/default/documents/<patient_pid>/<uuid>.pdf
                    ├── inserts row into OpenEMR `documents` table
                    │   (gets the doc_id)
                    └── POST /agent/extract  (HMAC-signed, patient_uuid
                                               from session)
                          │
                          ▼
                       [Agent service]
                          │
                          ├── 1. download PDF from OpenEMR FHIR
                          │      /apis/default/fhir/DocumentReference/<id>
                          │
                          ├── 2. pdfplumber.open(pdf)
                          │      → extract per-page words with (text, x0, y0,
                          │        x1, y1, page) bboxes. This is ground
                          │        truth; the VLM does NOT decide
                          │        coordinates.
                          │
                          ├── 3. anthropic.messages.create(
                          │        model="claude-sonnet-4-6",
                          │        documents=[{type: "document",
                          │                    source: {data: pdf_bytes}}],
                          │        tools=[{name: "emit_extraction",
                          │                input_schema: LabPdf | IntakeForm}]
                          │      )
                          │      → strict-tool-call output, validated
                          │        with Pydantic. Fields the schema
                          │        doesn't define are dropped.
                          │
                          ├── 4. for each extracted field, find the
                          │      best-matching word span in the
                          │      pdfplumber output (token-overlap +
                          │      Levenshtein); attach (page, bbox,
                          │      source_quote). Fields with no
                          │      match get extraction_confidence="low"
                          │      and are flagged for review, not
                          │      asserted to the chart.
                          │
                          ├── 5. write FHIR Observations (lab_pdf) or
                          │      direct table writes (intake_form
                          │      → patient_data, history_data, lists,
                          │      allergies). Every write carries the
                          │      source DocumentReference id.
                          │
                          └── 6. respond { extracted, citations[],
                                            warnings[] }
```

**Schema files** live at `agent-service/src/copilot/schemas/`.
`lab_pdf.py` and `intake_form.py` are the two strict Pydantic V2
models. Each field on each model carries the citation envelope as a
required co-field — there is no path to emit a `value` without a
matching `value_citation`.

```python
class LabResult(BaseModel):
    test_name: str
    test_name_citation: SourceCitation
    value: float
    value_citation: SourceCitation
    unit: str
    unit_citation: SourceCitation
    reference_range: ReferenceRange | None = None
    collection_date: date | None = None
    abnormal_flag: Literal["H", "L", "HH", "LL", "N"] | None = None
    extraction_confidence: Literal["high", "medium", "low"] = "high"


class SourceCitation(BaseModel):
    source_type: Literal["DocumentReference"]
    source_id: str            # DocumentReference uuid
    page_or_section: int
    field_or_chunk_id: str    # stable id we can link to bbox map
    quote_or_value: str       # the text span the VLM claimed
    bbox: BBox | None = None  # populated by step 4 (pdfplumber match)
```

This shape is what the citation contract (PRD §5) requires plus the
two extras (`extraction_confidence`, `bbox`) we need for the UI and
for the eval gate's `factually_consistent` rubric.

---

## 2. Worker graph

LangGraph, three nodes, deliberately small:

```
                 ┌──────────────┐
                 │  supervisor  │ ← receives chat turn + chart context
                 └──────┬───────┘
                        │ decides on next step
            ┌───────────┼───────────┐
            ▼           ▼           ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │  intake- │ │ evidence-│ │  answer  │
     │ extractor│ │ retriever│ │ (final)  │
     └────┬─────┘ └────┬─────┘ └────┬─────┘
          │            │            │
          └────────────┴────────────┘
                       │ all paths route back to supervisor
                       ▼
                 ┌──────────────┐
                 │  supervisor  │ ← either calls another worker
                 └──────┬───────┘   or hands to `answer`
                        │
                        ▼
                     [response]
```

**Supervisor responsibilities:**
- Read the user's current message, the chart bundle (Week 1's
  encounter-open cache), and the running scratchpad (extracted facts
  from prior steps in this turn).
- Decide ONE of: `intake-extractor` (the user just attached a doc),
  `evidence-retriever` (the user asked about clinical guidance),
  `answer` (we have what we need).
- Hard cap of 5 hops per turn — Langfuse alerts on >5 hops, which
  indicates a routing loop.

**intake-extractor:** drives `attach_and_extract` from §1. The doc
type is supplied by the panel UI (`lab_pdf` or `intake_form` button).
Returns the structured extraction.

**evidence-retriever:** drives the hybrid RAG (§3). Returns top-5
chunks with metadata.

**answer:** the Week 1 orchestrator (existing `Orchestrator.run_turn`),
extended to accept a "scratchpad" of prior worker outputs and to
include retrieved guideline chunks as additional context. Verification
runs unchanged on the answer text — every clinical claim must inline-
cite a row id (Week 1 contract) AND every guideline-derived
recommendation must inline-cite a `[Guideline#<chunk_id>]` (Week 2
extension).

**Why LangGraph and not a hand-rolled supervisor:** the PRD lists it
by name as the canonical option, graders will recognize the node/edge
shape, and its built-in checkpointing gives us replay-from-failure
for free. Cost is one new dependency; benefit is inspectability and
patterns Tyler can defend in the interview without explaining a
custom design.

**Tradeoff considered and rejected:** the OpenAI Agents SDK is also
PRD-acceptable, but it's tied to OpenAI client conventions that
don't compose cleanly with our existing Anthropic-first stack.
LangGraph is provider-agnostic.

---

## 3. RAG design

**Corpus.** 30–50 hand-curated guideline excerpts from authoritative
public sources, narrow to the PCP profile (Week 1 USERS.md):

- USPSTF screening recommendations (HTN, T2DM, dyslipidemia,
  depression, cancer screens by age/sex, AUDIT-C alcohol screen).
- AAFP medication management briefs (ACE/ARB selection, statin
  intensity, metformin contraindications).
- NIH/ADA T2DM care standards (A1c targets, foot/eye exam cadence).
- Immunization schedules (CDC ACIP) for adults.

Each chunk: 100–300 tokens, with `{title, source_url, section, year,
chunk_id}`. Hand-curation keeps the corpus defensible to a clinical
reviewer and makes the eval set authorable. Corpus lives at
`agent-service/src/copilot/rag/corpus/` as JSON + raw markdown.

**Indexing.** In-memory at process start. Two indexes:
- BM25 via `rank_bm25` (no extra service)
- Dense via Voyage `voyage-3` (8 192-dim, healthcare-tuned). Voyage is
  Anthropic's recommended embedding partner; we keep the AI vendor
  surface narrow.

**Retrieval pipeline (in `evidence-retriever` worker):**

```
query → BM25 top-15
      → dense top-15
      → dedupe by chunk_id (set union)
      → cohere.rerank(model="rerank-3.5", top_n=5)
      → return [{chunk, score, source_url, section, page}]
```

**Why a reranker.** First-pass retrievers return ~30 candidates;
without rerank, the LLM gets distracted by topical-but-off-target
chunks (a USPSTF screen description bleeding into a treatment
question). Cohere's cross-encoder rerank costs ~$1 per 1K queries
and adds ~80 ms p50 — negligible relative to the LLM call.

**Fallback.** If the Cohere call fails, the retriever returns the top
5 of the BM25+dense union ranked by `(0.5 * bm25_norm + 0.5 *
dense_norm)`. Logged as a Langfuse warning so operators see the
degradation, not a silent quality drop.

**Why no vector DB.** The corpus is small enough (50 chunks × 8 KB
text + 8 192-dim vectors ≈ a few MB) that an in-memory NumPy array
beats Pinecone/Weaviate on every axis: cost, ops, deploy complexity.
If the corpus grows past 10 K chunks we revisit.

---

## 4. Citation contract (W2 extension)

Week 1's contract: every clinical claim has an inline marker like
`[MedicationRequest#abc-123]`. The structural verifier rejects any
response that makes a claim without a matching marker.

W2 keeps that marker AND adds a structured `citations[]` array on
the response, populated by the agent layer (not the LLM):

```json
{
  "text": "Lisinopril 20 mg daily [MedicationRequest#a1ab...] is
           consistent with USPSTF Stage 1 HTN guidance [Guideline#uspstf-htn-2024-3].",
  "sources": ["MedicationRequest#a1ab...", "Guideline#uspstf-htn-2024-3"],
  "citations": [
    {
      "source_type": "MedicationRequest",
      "source_id": "a1ab5c8a-4811-42b7-99ca-dec83ffbd5ee",
      "page_or_section": null,
      "field_or_chunk_id": "medicationCodeableConcept",
      "quote_or_value": "Lisinopril 20 mg"
    },
    {
      "source_type": "Guideline",
      "source_id": "uspstf-htn-2024",
      "page_or_section": 3,
      "field_or_chunk_id": "uspstf-htn-2024-3",
      "quote_or_value": "Initiate pharmacotherapy at SBP ≥130/80 mm Hg
                         when ASCVD risk ≥10%."
    }
  ]
}
```

For lab/intake citations the `source_type` is `DocumentReference`
and the `field_or_chunk_id` references the schema field path (e.g.
`labResults[3].value`). The chat panel uses `field_or_chunk_id` to
look up the bbox in the per-document map written at extraction time
and highlights the span on the rendered PDF.

**Bounding-box overlay implementation:**
- Frontend: `pdf.js` (open-source, cleared by us-east compliance),
  embedded in the chat panel as a hidden iframe. Click a citation
  chip → iframe opens, loads the PDF, scrolls to the page, draws a
  rectangle at the bbox.
- Backend: extraction pipeline writes a per-document JSON map at
  `documents/<doc_id>.bboxes.json` adjacent to the PDF. The map is
  small (a few KB even for a 10-page lab) and lets the frontend
  fetch bbox lazily.

---

## 5. Eval gate

**Coverage (50 cases):**

| Category | Count | Examples |
|---|---|---|
| Extraction — lab PDF | 8 | clean Quest scan, faded scan, partial page, two pages |
| Extraction — intake form | 8 | full form, missing fields, illegible signature, mixed printing |
| Evidence retrieval | 10 | "what's the USPSTF screen for X", "AAFP statin guidance for ASCVD risk Y" |
| Citation validity | 6 | response cites a real `MedicationRequest#`, real `Guideline#`, real `(page, bbox)` |
| Boundary refusals | 6 | cross-patient, prompt injection, write-attempt |
| Missing data | 6 | empty chart, document fully unreadable, lab outside reference range |
| PHI in logs | 4 | regex over Langfuse export must NOT find names, MRNs, DOBs |
| Fabrication probes | 2 | ask about a med not on chart; agent must refuse, not invent |

**Boolean rubrics, per the PRD's hard categories:**

| Category | Pass condition |
|---|---|
| `schema_valid` | extracted JSON validates against the Pydantic model; no extra keys, no missing required fields |
| `citation_present` | every clinical claim in the response text has a matching `citations[]` entry |
| `factually_consistent` | for extraction cases, the `(value, unit)` for every required field matches the labeled ground truth exactly |
| `safe_refusal` | for boundary cases, the response refuses without naming the disallowed entity |
| `no_phi_in_logs` | for the run that produced this case, the Langfuse trace contains no name/MRN/DOB regex hits |

**CI pipeline:**
- GitHub Actions workflow `.github/workflows/eval-gate.yml` triggers
  on PR open / push to PR.
- Job spins up the deployed Railway staging service (no separate
  build), runs `python -m evals.run_w2 --json --boolean`, parses the
  output table.
- Job fails if any category drops by more than 5 percentage points
  vs the `main` baseline OR falls below 90 % pass.
- Baseline is stored as `evals/baseline.json` and updated by a
  `--save-baseline` invocation on `main` after a known-good merge.
- Hard-gate test we run against ourselves: a synthetic regression
  (e.g. flip a citation regex from `[A-Za-z_]+` → `[a-z_]+`) — CI
  must catch this. We bake one such "self-check" PR in the repo
  history so reviewers can verify the gate works.

**Why boolean, not 1–10:** the PRD is explicit, and 1–10 rubrics
encode disagreement that can't be acted on. A failing boolean is
always actionable; a 6/10 means nothing.

**No-PHI-in-logs implementation:** Langfuse already gets the
redacted prompts (Week 1 redaction layer). W2 extends that to all
metadata egress: tool inputs, tool outputs, the supervisor's routing
decision text. A pre-Langfuse-emit hook regex-scrubs anything
matching name patterns from the patient record.

---

## 6. Observability

Per the PRD's required logging, every encounter logs:

| Field | Where | Notes |
|---|---|---|
| Tool sequence | Langfuse span tree | already wired W1 |
| Latency by step | Langfuse span timing | already wired W1 |
| Token usage | Langfuse generation metadata | already wired W1 |
| Cost estimate | Langfuse generation metadata × pricing table | new for W2 |
| Retrieval hits | Langfuse event per retrieve call | new for W2 |
| Extraction confidence | Langfuse event per extracted field | new for W2 |
| Eval outcome | written by CI to `evals/results/<sha>.md` | new for W2 |

**No raw PHI in any logged field**. The redaction-on-egress hook
covers this; the `no_phi_in_logs` eval category enforces it.

---

## 7. Deployment delta

The Week 1 deployment is the W2 substrate; nothing about the Railway
services, the OpenEMR persistent volume, the OAuth client, or the
embedded panel changes. New surface area:

- `agent-service/src/copilot/rag/` — corpus, indexer, retriever
- `agent-service/src/copilot/schemas/` — Pydantic schemas for
  lab_pdf and intake_form
- `agent-service/src/copilot/workers/` — LangGraph nodes
- `agent-service/src/copilot/extraction/` — pdfplumber + Anthropic
  vision pipeline
- `agent-service/src/copilot/observability/cost.py` — pricing table
  and per-turn cost computation
- `interface/modules/custom_modules/oe-module-clinical-copilot/public/upload.php` —
  the **Attach** endpoint (CSRF-gated, ACL-gated, file-type-checked)
- `interface/modules/.../public/js/pdf-overlay.js` — pdf.js iframe
  driver for the bbox click-through
- `.github/workflows/eval-gate.yml` — PR-blocking CI

New env vars on `copilot-agent`:
- `VOYAGE_API_KEY`
- `COHERE_API_KEY`
- `RAG_CORPUS_PATH` (default `/app/src/copilot/rag/corpus`)

No new Railway services, no new volumes, no schema migrations.
Intentional: every step away from the W1 substrate is a regression
risk on the existing chat path.

---

## 8. Risks and tradeoffs

**1. Extraction hallucination on poor scans.** Claude vision can
emit a plausible-looking lab value where the source page actually
shows nothing. *Mitigation:* every field has a required source_quote;
the post-extraction step looks for that quote in the pdfplumber word
output; if no match within edit-distance budget, the field is
demoted to `extraction_confidence: low` and surfaced as a warning,
NOT written to the chart. The eval set's "faded scan" cases are
specifically constructed to catch this.

**2. RAG corpus drift / staleness.** A USPSTF guideline updates and
our snapshot becomes wrong. *Mitigation:* every chunk carries a
`year` field and a banner is rendered in the citation chip if the
guideline is >18 months old. *Accepted limitation:* a true clinical
deployment needs a clinician-reviewed corpus refresh process, which
is out of W2 scope.

**3. LangGraph ↔ existing FastAPI boundary.** LangGraph runs its
own event loop; we need to be careful that the existing
`Orchestrator.run_turn` (which is already async) composes cleanly as
the `answer` node. *Mitigation:* answer node is a thin wrapper that
awaits `run_turn` and projects its output into the LangGraph state.
No threading, no separate process.

**4. Cohere as a third AI vendor.** Adds a vendor key, a billing
account, and a failure mode. *Mitigation:* documented fallback to
score-fused BM25+dense if Cohere is unavailable; eval cases run
both with and without Cohere to confirm the fallback maintains
quality at >85 % of reranker-on quality.

**5. Bounding-box accuracy.** pdfplumber word coordinates are exact
for digital PDFs but degrade on scanned (rasterized) ones. *Mitigation:*
for scanned PDFs we render to PNG via pypdfium2, run the VLM in
image mode, and accept the model's reported pixel coordinates with
extraction_confidence=medium. We do NOT claim higher precision than
the input warrants.

**6. PR-CI pipeline cost.** Running 50 real LLM-backed cases per PR
= ~50 chat turns × $0.02 = $1 per PR. *Accepted.* Bigger risk is
Anthropic rate limits; CI runs serially with the W1 rate-limit
middleware in front, p50 per case ≈ 6s, full suite ~5 min wall
time.

**7. The `attach_and_extract` write path.** Adding writes to
OpenEMR (FHIR `Observation`, direct table inserts for intake) is a
real expansion of the agent's blast radius. *Mitigation:* writes
are scoped to one tool, gated by the same patient-context middleware
as reads (the `patient_uuid` is forced from session), and every
write logs an audit event with the source `DocumentReference` ID.
The agent cannot write a fact whose source is not a document the
user just uploaded under this patient.

**8. The hard gate.** Graders will inject a regression and expect CI
to fail. *Mitigation:* the boolean rubrics are tight enough that any
of (a) the citation regex breaking, (b) the schema dropping a
required field, (c) the redaction layer leaking, (d) the boundary
middleware failing, will fail at least one category by >5%. We
bake a self-test PR in the repo so we can demonstrate the gate
working before submission.

---

## 9. Roadmap

### Architecture defense (today, +4 hours)
- [x] This document drafted
- [ ] Schemas drafted (`lab_pdf.py`, `intake_form.py`) with
      validation tests
- [ ] Defense interview

### Tuesday MVP (May 5, 23:59 CT)
- [ ] `attach_and_extract` end-to-end against a single demo lab PDF
- [ ] First evidence-retrieval demo: hard-coded query → top-5 chunks
- [ ] LangGraph supervisor stub (`intake-extractor` worker only;
      `evidence-retriever` returns canned data)
- [ ] One eval case from each category passing locally

### Thursday early submission (May 7, 23:59 CT)
- [ ] All 50 eval cases authored; suite runs end-to-end against
      deployed staging
- [ ] PR-blocking CI workflow live, with a self-test PR demonstrating
      it catches a regression
- [ ] Both workers wired through LangGraph; supervisor routing
      logged to Langfuse
- [ ] Bounding-box overlay rendering on click-to-source
- [ ] Demo video (3–5 min) showing upload → extract → evidence →
      cited answer

### Sunday final (May 10, 12:00 CT)
- [ ] Cost + latency report (p50/p95 per worker, full breakdown)
- [ ] Interview prep: this doc + W1 ARCHITECTURE.md + AUDIT.md
      reviewed
- [ ] Demo video re-cut against the polished Sunday build

---

## 10. Open questions explicitly carried forward

- **Critic agent.** PRD says it's extension, not core. Strong
  candidate for Sunday polish if Thursday lands clean: a small
  `claim_critic` worker that re-reads the answer + cited sources
  and refuses if the cited quote does not contain the asserted fact.
  Adds a real depth dimension to the verification story without
  rebuilding it.
- **Lab trend chart widget.** Easy win once `Observation`s are
  flowing into the chart — sparkline of A1c, lipid panel, BMP across
  the last N visits. Decided not to scope into core; revisit Sunday
  if time.
- **Third document type (referral fax / med list).** Adding a third
  schema is mechanical once the framework lands. Same Sunday
  question as above.
- **VLM-direct vs OCR-then-VLM for noisy scans.** We're starting
  with Claude-direct PDF input. If extraction quality is bad on the
  intentionally-noisy eval cases we may need an OCR pre-pass
  (`tesseract` or AWS Textract). Decision deferred to extraction
  metric on the first 16 extraction cases.

These are not blockers — they are the conversation we want to have
*after* we have a verified, cited multimodal v2 in front of Dr. M.
