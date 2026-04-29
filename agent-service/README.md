# Agent service — Clinical Co-Pilot

Python/FastAPI service that runs the LLM agent. Called over internal
HTTP by the `oe-module-clinical-copilot/` PHP module in OpenEMR.

See [../ARCHITECTURE.md](../ARCHITECTURE.md) for the full design. Quick
map of this skeleton:

```
agent-service/
├── pyproject.toml
├── Dockerfile
├── README.md           ← you are here
└── src/copilot/
    ├── main.py             FastAPI app: /chat, /warm, /healthz
    ├── settings.py         env-loaded config
    ├── orchestrator.py     the agent loop (load → redact → LLM → tools → verify → rehydrate)
    ├── context/
    │   ├── patient.py        PatientContext + HMAC token mint/verify
    │   └── cache.py          Redis-backed context bundle cache
    ├── middleware/
    │   └── patient_context.py  fail-closed patient-boundary enforcement
    ├── redaction/
    │   └── tokens.py         tokenize PHI → [PT_NAME_1] etc., rehydrate
    ├── verification/
    │   ├── structural.py     citation regex + presence checks
    │   └── rules.py          deterministic domain rules (vitals units, med active state, …)
    ├── tools/
    │   ├── base.py           Tool ABC
    │   ├── registry.py
    │   └── medications.py    fully-sketched example; other tools follow the same shape
    ├── llm/
    │   ├── anthropic_client.py
    │   └── prompts.py        system prompt with the citation contract
    └── bridge/
        └── openemr.py        OAuth2 client → OpenEMR REST/FHIR
```

## Status of each piece

| Piece | State |
|-------|-------|
| FastAPI app + endpoint shape | Sketched, runnable |
| Patient-context middleware (§3 of ARCHITECTURE.md) | Implemented |
| Redaction tokenize/rehydrate (§2.5) | Implemented (basic — name, MRN, DOB, phone, email) |
| Structural verification (§4.1) | Implemented |
| Domain rules (§4.2) | 2 of 5 implemented (vitals units, med active state); 3 stubbed |
| LLM-as-judge | TODO — wire after Thursday |
| Anthropic client | Implemented (sync) |
| Orchestrator loop | Implemented (single retry on verification fail; judge call stubbed) |
| Tools | `get_active_medications` fully sketched; others stubbed in `tools/__init__.py` |
| OpenEMR REST bridge | Sketched — OAuth2 client_credentials grant + one endpoint shown |
| Context cache (Redis) | Sketched |
| Langfuse traces | TODO — slot reserved in orchestrator |

## Running locally

```bash
cd agent-service
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
export OPENEMR_BASE_URL=http://localhost:8080
export OPENEMR_OAUTH_CLIENT_ID=...
export OPENEMR_OAUTH_CLIENT_SECRET=...
export AGENT_SHARED_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
export REDIS_URL=redis://localhost:6379/0
uvicorn copilot.main:app --reload --port 8000
```

The OpenEMR PHP module talks to `http://agent-service:8000` in the
docker-compose network (Thursday deliverable adds the service +
network wiring).
