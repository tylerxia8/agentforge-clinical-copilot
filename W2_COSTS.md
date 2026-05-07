# W2_COSTS.md — Cost & latency report

> Required by the W2 PRD Submission Requirements:
> *"Actual dev spend, projected production cost, p50/p95 latency, and
> bottleneck analysis."*
>
> This document covers the W2 capabilities specifically (multimodal
> extraction, hybrid RAG, supervisor + workers, eval gate). For the
> W1 economics baseline (chat-only, scale projections through 100K
> users), see [COSTS.md](COSTS.md). The W1 numbers carry forward
> mostly unchanged; this document calls out the W2 deltas.

---

## 1. Actual W2 dev spend

| Cost source | W1 baseline | W2 incremental | Notes |
|-------------|-------------|----------------|-------|
| Anthropic API | ~$1.20 | **+$11.50** | Vision extraction during fixture testing + 7 calibration runs (cookbook-stage iterations) + multi-format smoke runs (DOCX/XLSX/TIFF each call Claude once per file × 7 patients) |
| Voyage AI | $0 | **+$0.00** | Sub-cent corpus embedding (24 chunks × ~150 tokens × $0.18/M) — well within free tier |
| Cohere | $0 | **+$0.00** | Free tier covers <1K rerank calls/mo |
| Railway (compute) | $0 | $0 | Five services now (added `openemr-dashboard`), all on the same hobby tier compute pool |
| Persistent volume | $0 | **+$0.00** | First 5 GB free; we use ~60 MB for OAuth state + uploads + dashboard build cache |
| **Total to date** | $1.20 | **+$11.50** | **W2 spend = $12.70 cumulative** |

The incremental Anthropic spend was front-loaded into (a) eval
calibration — seven runs across the week as we iterated rubrics +
diagnosed the per-IP rate-limit bug + bedded down the multi-format
smoke; (b) DOCX/XLSX/TIFF ingestion testing — each format sends one
Claude tool-use call per file, ~$0.04 each × 7 patient files × 3
formats × 2 retries during prompt tuning. Steady-state dev burn is
back to ~$0.20/day; the eval gate adds ~$1.60 per CI run (63 cases
× $0.025 avg).

---

## 2. Per-extraction cost across all 5 ingestion paths

A lab PDF runs through the vision pipeline once:

| Slot | Tokens | Rate | Cost |
|------|--------|------|------|
| PDF input (1 page, Anthropic billing) | ~1,800 | $3.00/M input | $0.0054 |
| System prompt (lab parser instructions) | ~500 | $3.00/M | $0.0015 |
| Tool schema (LabPdfExtraction JSON Schema) | ~350 | $3.00/M | $0.0011 |
| Tool-use output (structured extraction) | ~800 | $15.00/M | $0.0120 |
| **1-page lab total** | | | **≈ $0.020** |
| 2-page lab | | | **≈ $0.027** |
| Multi-page intake form (3 pages) | | | **≈ $0.040** |

The pdfplumber bbox-match step + the FHIR writeback in PHP are
CPU-only and add no LLM cost. The on-disk PDF + bbox JSON survive
on the persistent volume for free.

### Multi-format ingestion (W2 add)

| Format | LLM call? | Per-file cost | Notes |
|---|---|---|---|
| `lab_pdf` | yes (vision) | **$0.020 – $0.027** | Same as above |
| `intake_form` | yes (vision) | **$0.020 – $0.040** | Same as above, scales with pages |
| `hl7v2_oru` | **no** | **$0.000** | Pure structured parse — segments → LabResult rows. Zero LLM tokens. |
| `hl7v2_adt` | **no** | **$0.000** | Same — demographics dict from PID/EVN/IN1 segments |
| `docx_referral` | yes (text-only) | **≈ $0.025** | DOCX → plain text → Claude with intake-form schema. Cheaper than vision because the input is text not document bytes |
| `xlsx_workbook` | yes (text-only) | **≈ $0.025** | Sheets → CSV-shaped text → Claude with intake-form schema |
| `tiff_fax` | yes (vision) | **≈ $0.030** | TIFF → in-process PDF → existing vision pipeline. Slightly higher than native PDF because the converted PDF is rasterized (more pixels per page worth of context) |

The HL7 paths are zero-LLM-cost — that's the leverage of receiving
already-structured data. A clinic feeding lab results via HL7
instead of scanned PDFs cuts the per-extraction cost to free.

---

## 3. Per-chat-turn cost (W1 → W2 delta)

The W1 COSTS.md§2 figure was **$0.019/turn** at hot-cache steady
state with 4 tools wired. W2 changes:

| What changed | Net effect |
|--------------|-----------|
| Bundle fan-out grew from 4 → 7 tools (added labs/vitals/immunizations) | +~3,000 tokens cached per session (system prompt + bundle); +$0.001 amortised |
| Worker graph adds the supervisor as an extra hop, but supervisor is **heuristic, not LLM-based** (W2_ARCHITECTURE.md §2) | $0.000 — no extra LLM call |
| When the supervisor routes through `evidence_retriever`, the answer node sees an augmented prompt with retrieved chunks | +~600 tokens output, +$0.009 on evidence-shaped turns only |
| RAG retrieval itself: BM25 (free) + Voyage embed ($0.0002/query) + optional Cohere rerank ($0.001/query) | ≈ $0.001 added on evidence turns |

**Updated steady-state turn cost:**

| Turn shape | Cost |
|-----------|------|
| Plain chart question (W1-style) | **$0.020** (+$0.001 vs W1) |
| Guideline-grounded question (RAG + augmented answer) | **$0.030** |
| First turn after chart-open (cache-write penalty) | **$0.058** (one-time per session) |

**Per-physician monthly stays in the $40–55 band** at the W1 base
of 65 turns/day × 22 days, with ~30% of clinic turns hitting RAG.
The W1 scale-projections in COSTS.md §3 shift by ≤10% across all
tiers.

---

## 4. Latency — p50 / p95 measured against deployed staging

Measured against `https://copilot-agent-production-ba87.up.railway.app/`
on the live OpenEMR seed (Farrah, Ted, Eduardo, etc.) over a sample
of ~40 calls each. Cold-cache cases are the first turn after chart
open; hot are subsequent turns within the 5-min Redis TTL.

| Operation | p50 | p95 | Bottleneck |
|-----------|-----|-----|-----------|
| `/agent/chat` — plain, hot cache | 5.5s | 11s | Anthropic LLM call (~85% of wall) |
| `/agent/chat` — plain, cold cache | 9s | 14s | + 7-tool warm fan-out (~3s) |
| `/agent/chat` — guideline-grounded (RAG path) | 7.5s | 14s | LLM call again, but on a longer augmented prompt |
| `/agent/extract` — 1-page lab (vision + match + writeback) | 9s | 16s | Anthropic vision (~75% of wall) |
| `/agent/extract` — 3-page intake form | 13s | 22s | Vision call scales ~linearly with page count |
| Bbox click-through (pdf.js modal load + render) | 0.4s | 0.9s | First-load is CDN fetch of pdf.min.mjs (~1s); subsequent in-session loads are <100ms |
| Eval suite full run (63 cases, sequential) | 9.5 min | n/a | Anthropic per-org rate limit (30K tokens/min) — we already hit it once when paralleling 14 patients in W1 |

The eval-suite duration is real: each case runs end-to-end against
the live agent service, so the wall-clock is gated by per-org rate
limit + chat turn time. Going parallel beyond 4 in-flight risks
the 30K-tokens-per-minute cap from W1's incident.

---

## 5. Bottleneck analysis

For each flow, "what's the slowest part" + "what would actually
move the needle":

### `/agent/chat`

```
Wall time breakdown (typical hot-cache turn, 6s total):
  ├── Supervisor heuristic routing       ~0ms     pure-function
  ├── Bundle read from Redis            ~30ms    network local
  ├── Token redaction                   ~10ms    pure-python
  ├── Anthropic LLM call (Sonnet 4.6)   ~5,000ms  network external  ← 83%
  ├── Tool calls (if any, parallel)    ~200ms   network openemr
  ├── Verification + retry            ~50ms    pure-python
  └── Token rehydration                ~10ms    pure-python
```

**Lever:** prompt caching is doing the heavy work; cache-hit rate
is what Langfuse's new `cost_details` + cache-token tracking lets
the dashboard surface. A redeploy that invalidates the cache (e.g.
a system-prompt edit) costs ~3s + $0.04 per active session for the
warm-up.

**What would NOT move the needle:** a faster JSON parser, a faster
Redis driver, a leaner verification regex. None of those touch the
LLM call.

### `/agent/extract`

```
Wall time breakdown (typical 1-page lab, 9s total):
  ├── PHP CSRF + ACL + magic-bytes      ~50ms    local
  ├── PDF write to volume               ~30ms    Railway disk
  ├── Forward to /agent/extract         ~20ms    inter-service
  ├── Anthropic vision call             ~7,000ms network external  ← 78%
  ├── pdfplumber word extraction        ~100ms   CPU
  ├── Bbox match (8 citations)          ~30ms    CPU
  ├── Pydantic full validation          ~20ms    CPU
  ├── PHP writeback (pnotes + docs +
  │   procedure_result × 8)             ~250ms   MariaDB
  └── JSON encode + return              ~10ms    local
```

**Lever:** the vision call is the dominant cost. Faster: render the
PDF to PNG ourselves and use Sonnet's image input mode (sometimes
2-3x faster than PDF mode for short documents). Trade-off: images
are token-heavier; net cost goes up by ~30%. For W2 we keep PDF
mode because cost > latency in the deploy target.

### `/agent/chat` with evidence retrieval

```
Additional time on top of plain chat (RAG-shaped turns, ~7.5s total):
  ├── Supervisor routes to evidence_retriever  ~0ms (heuristic)
  ├── BM25 + Voyage embed + Cohere rerank      ~300ms
  └── Answer node LLM call (now bigger prompt) +500ms vs plain
```

**Lever:** the dense embed (Voyage) + rerank (Cohere) adds 200-300ms
for a ~10% retrieval-quality lift on our 8-chunk seed corpus. At
40-chunk corpus size (Sunday target) the lift goes up to ~25% per
internal benchmarks; still a good trade. Beyond that, multi-vector
indexing (ColQwen2) becomes interesting — but that's the W2 stretch
listed in the PRD, not core.

---

## 5b. Cookbook stages 3-5 cost (W2 add)

| Stage | Per-invocation cost | Notes |
|---|---|---|
| Stage 3 — `--record` | **$0** beyond the underlying live run | The runner already calls the agent once per case; recording is just a JSONL writer on the way out. |
| Stage 3 — `--replay` | **$0 / nearly-instant** | Reads JSONL, applies rubrics. No agent calls. The point of replay: tweak a rubric, re-grade in <1s instead of paying $1.73 + 10 min for a fresh CI run. |
| Stage 4 — `judge_yes_no` (Haiku 4.5) | **≈ $0.0005 / call** | ~500 input + 100 output tokens at Haiku's $1/$5 per M. Adopting it on all 30 factually-graded cases adds $0.015 to a calibration. |
| Stage 5 — `experiments.diff_recordings` | **$0** | Pure local diff over two JSONL files. |

The replay harness pays for itself the first time you tweak a rubric:
re-running just the rubric (free) beats re-running the live suite
($1.73, 10 min) every time. The judge tier is opt-in per case so the
extra cost is bounded by however many cases you adopt it on.

## 6. Eval suite cost & cadence

The PR-blocking eval gate runs the 63-case suite against the
deployed staging on every PR to `agent-service/**` or the PHP
module. Per-run cost:

| Cost item | Amount |
|-----------|--------|
| 50 single-turn cases × ~$0.025 avg | $1.25 |
| 3 golden cases (richer rubrics, longer responses) × ~$0.035 | $0.10 |
| 3 multistep cases × 2-3 turns × ~$0.025 | $0.20 |
| 7 adversarial cases × ~$0.025 | $0.18 |
| **Per run** | **≈ $1.73** |
| Wall time | ~10 min serial (gated by per-org rate limit) |

At a typical sprint cadence of 5-10 PRs/day during active dev,
this is ~$10-17/day in eval cost. CI cost is dominated by the LLM
calls, not by GitHub Actions minutes (the runner itself is free
on the public repo tier).

**Inflection point:** if eval cost exceeds dev-time savings, we
either tag cases by category and run only the affected category
on a PR (using the eval-runner's existing per-category aggregation)
or move to a nightly full run + PR-gates on a 12-case smoke subset.
Today the cost is small enough that we run the full suite per PR —
the demo's hard-gate scenario depends on the gate being teeth-bearing,
and "we only run a smoke set on PRs" weakens that.

---

## 6b. Patient dashboard service (W2 surprise add)

The Next.js dashboard runs as a separate Railway service
(`openemr-dashboard`). Cost shape:

| Cost item | Amount |
|---|---|
| Railway compute | **$0/mo** (hobby tier; one container, shared with the rest of the project pool) |
| Egress | Negligible — dashboard hits OpenEMR FHIR over Railway's internal network |
| OAuth-related Anthropic / vendor calls | **$0** — dashboard is read-only against FHIR; no LLM in the request path |
| Cold-start latency | ~400ms first hit / ~30ms warm (Next.js standalone bundle on a single replica) |

Per-physician dashboard cost is effectively zero. The infrastructure
gain is access-tokens-never-touch-the-browser (server components do
the FHIR calls), not an AI cost line. See
[PATIENT_DASHBOARD_MIGRATION.md](PATIENT_DASHBOARD_MIGRATION.md)
for the full framework defense.

## 7. The W2 summary line

| Layer | Per-unit cost | Latency p95 |
|-------|---------------|-------------|
| Chat turn (plain) | $0.020 | 11 s |
| Chat turn (RAG) | $0.030 | 14 s |
| Lab PDF extraction | $0.020-0.027 | 16 s |
| Intake form extraction | $0.040 | 22 s |
| HL7 v2 ORU / ADT ingestion | **$0.000** | <500 ms (pure parse) |
| DOCX referral extraction | $0.025 | 7 s |
| XLSX workbook extraction | $0.025 | 7 s |
| TIFF fax-packet extraction | $0.030 | 18 s (TIFF→PDF + vision) |
| LLM-as-judge call (Haiku) | $0.0005 | ~3 s |
| Replay-grade rubric run | $0.000 | <1 s for 63 cases |
| A/B experiment diff | $0.000 | <1 s |
| Dashboard FHIR card render | $0.000 | 0.3 s p50, 0.9 s p95 |
| Eval suite (full live run) | $1.73 | 10 min |
| Bbox overlay click | <$0.0001 | 0.9 s |

**Per-physician monthly cost adds ~$5-8 over W1** (+10-20% relative)
to gain document ingestion, RAG-grounded answers, writeback to the
chart, multi-format intake (HL7/DOCX/XLSX/TIFF), the cookbook eval
stages, and the modern dashboard. The PRD's narrower-than-the-spec
guidance pays off: we kept the per-unit costs small enough that the
AI bill doesn't balloon even with five new capability surfaces, and
the structural verifier + eval gate kept the quality bar from
sliding (every category landed at 100% on the locked baseline).
