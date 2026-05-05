# Agent service — Clinical Co-Pilot

Python/FastAPI service that runs the LLM agent. Called over internal
HTTP by the `oe-module-clinical-copilot/` PHP module in OpenEMR.

See [../ARCHITECTURE.md](../ARCHITECTURE.md) (Week 1 design) and
[../W2_ARCHITECTURE.md](../W2_ARCHITECTURE.md) (Week 2 multimodal +
RAG + supervisor graph) for the full design.

## Layout

```
agent-service/
├── pyproject.toml
├── Dockerfile
├── README.md           ← you are here
└── src/copilot/
    ├── main.py             FastAPI app: /agent/{chat,extract,warm}, /demo/chat, /healthz
    ├── settings.py         env-loaded config
    ├── orchestrator.py     the W1 agent loop (load → redact → LLM → tools → verify → rehydrate)
    ├── context/
    │   ├── patient.py        PatientContext + HMAC token mint/verify
    │   └── cache.py          Redis-backed context bundle cache
    ├── middleware/
    │   ├── patient_context.py  fail-closed patient-boundary enforcement
    │   └── rate_limit.py       per-IP + global concurrency caps
    ├── redaction/
    │   └── tokens.py         tokenize PHI → [PT_NAME_1] etc., rehydrate
    ├── verification/
    │   ├── structural.py     citation regex + presence checks
    │   └── rules.py          deterministic domain rules (vitals units, med active state, …)
    ├── tools/                W1 FHIR tools (medications, problems, allergies, encounters)
    ├── llm/
    │   ├── anthropic_client.py
    │   └── prompts.py        system prompt with the citation contract
    ├── bridge/
    │   └── openemr.py        OAuth2 client → OpenEMR REST/FHIR
    │
    │   ─── W2 additions ──────────────────────────────────
    │
    ├── schemas/                  Strict Pydantic schemas for extractions
    │   ├── citations.py            SourceCitation + BBox envelope
    │   ├── lab_pdf.py              LabPdfExtraction → LabResult rows
    │   └── intake_form.py          IntakeFormExtraction → demographics + meds + allergies + …
    ├── extraction/
    │   ├── pdf.py                  pdfplumber wrapper (Word with x0/top/x1/bottom)
    │   ├── matcher.py              quote → bbox positional matcher
    │   ├── vision.py               Anthropic vision call (strip-and-hydrate around tool-use)
    │   └── pipeline.py             attach_bboxes() composition
    ├── rag/
    │   ├── corpus.py               Chunk + load_corpus()
    │   ├── retriever.py            BM25 + injectable dense + injectable rerank
    │   ├── corpus/seed/*.json      8 seed chunks (USPSTF, ADA, ACIP)
    │   └── __init__.py             make_retriever() factory
    └── workers/
        ├── state.py                WorkerState TypedDict
        ├── routing.py              route_decision() heuristic supervisor
        ├── nodes.py                supervisor + intake_extractor + evidence_retriever + answer
        └── graph.py                LangGraph wiring (only file that imports langgraph)
```

## Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET  | `/healthz`              | Liveness probe                           | none |
| GET  | `/`                     | Standalone demo UI (gated)               | none |
| POST | `/demo/chat`            | Token-less chat (graph-routed)           | none |
| POST | `/agent/chat`           | Production chat (graph-routed)           | HMAC bearer |
| POST | `/agent/extract`        | **W2** multipart upload → vision extraction → bbox match | HMAC bearer |
| POST | `/agent/warm/{uuid}`    | Fire-and-forget bundle preload           | HMAC bearer |

`/agent/chat` and `/demo/chat` route through the W2 supervisor graph:
plain chart questions go straight to the W1 answer node; questions
that mention USPSTF / AAFP / a screening or recommendation trigger
evidence retrieval first.

`/agent/extract` accepts `multipart/form-data` with:
- `file` — the PDF (≤25 MB; magic-bytes-checked)
- `doc_type` — `lab_pdf` or `intake_form`
- `document_reference_id` — UUID supplied by the calling PHP module

Returns `{extraction, bbox_match}` where `extraction` is the validated
Pydantic dump and `bbox_match` is `{walked, matched}` for telemetry.

## Running locally

```bash
cd agent-service
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
export OPENEMR_BASE_URL=https://openemr-production-0996.up.railway.app
export OPENEMR_OAUTH_CLIENT_ID=...           # from /oauth2/default/registration
export OPENEMR_OAUTH_CLIENT_SECRET=...
export OPENEMR_SERVICE_USERNAME=copilot-svc
export OPENEMR_SERVICE_PASSWORD=...
export AGENT_SHARED_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
export REDIS_URL=redis://localhost:6379/0

# Optional W2 dense + rerank — falls back to BM25-only if unset
export VOYAGE_API_KEY=pa-...
export COHERE_API_KEY=co-...

uvicorn copilot.main:app --reload --port 8000
```

To install with the dense + rerank extras:
```bash
pip install -e '.[rag-dense]'
```

## Tests

```bash
pip install -e '.[dev]'
pytest tests/ -v
```

Coverage as of Week 2 MVP:
- `test_schemas.py` — 30 schema validation cases
- `test_matcher.py` — 15 bbox matcher cases
- `test_vision_helpers.py` — 18 strip-and-hydrate cases
- `test_retriever.py` — 14 retrieval + degradation cases
- `test_routing.py` — 16 supervisor routing cases

Tests do not require any vendor API keys (Voyage, Cohere, Anthropic);
all integration points are mocked or use the BM25-only fallback.
