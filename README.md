# AgentForge Clinical Co-Pilot

This repository is a fork of [OpenEMR](https://github.com/openemr/openemr)
extended with an AI clinical co-pilot for primary care physicians, built
for the Gauntlet AI Austin admission sprint.

> **Week 2 final demo video:** _link landing here once recorded — see
> [W2_DEMO_SCRIPT.md](W2_DEMO_SCRIPT.md) for the 5:00 walkthrough_

## What changed since the W2 MVP grade

The MVP passed Wednesday with the note *"harden the UX and reliability,
and stronger visibility into the retrieval architecture, eval coverage,
and worker orchestration."* Everything below shipped after that grade
in direct response, plus the two surprise-challenge additions:

| Area | Shipped | Where |
|---|---|---|
| **Visibility** | `/visibility` page — corpus inspector, ASCII supervisor graph, deterministic routing rule table, eval coverage with locked-baseline rates, recent supervisor decisions, **live retrieval inspector** showing BM25 / dense / rerank scores per chunk before any LLM sees them | https://copilot-agent-production-ba87.up.railway.app/visibility |
| **UX hardening** | Retry button on every error / refusal message in the chat panel; per-status friendly HTTP error copy (502/503/504/401/403/429 each get specific actionable text instead of "Server returned 502") | `interface/modules/custom_modules/oe-module-clinical-copilot/public/js/copilot-chat.js` |
| **Multi-format ingestion** (W2 surprise #1) | HL7 v2 ORU + ADT (structured parse, zero LLM cost), DOCX referral letters, XLSX patient workbooks, TIFF fax packets — all routed through `/agent/extract` with the appropriate `doc_type`. Cohort 5 W2 asset pack is committed as fixtures + a smoke runner | `agent-service/src/copilot/extraction/{hl7v2,docx,xlsx,tiff}.py` |
| **Modern dashboard** (W2 surprise #2) | Next.js 15 + Auth.js v5 OIDC against OpenEMR's existing OAuth flow. Six clinical cards + bidirectional cross-app navigation. Defense in [PATIENT_DASHBOARD_MIGRATION.md](PATIENT_DASHBOARD_MIGRATION.md) | `dashboard/` |
| **Cookbook stages 3-5** (production-evals reference) | Replay harness (`--record` / `--replay` JSONL), LLM-as-judge tier (`judge_yes_no` on Haiku for clinical-quality binary checks), A/B experiment diff (compare two recordings side-by-side) | `agent-service/evals/w2/{replay,judge,experiments}.py` |
| **Eval gate** | Locked at **63 cases / 11 categories / 6 rubrics / 100% baseline.** PR-blocking GitHub Action enforces ≥95% per category (5pp regression delta) | `.github/workflows/eval-gate.yml` |

| Document | Purpose |
|----------|---------|
| [SETUP.md](SETUP.md) | Bring the stack up locally (`docker compose up -d`) |
| [AUDIT.md](AUDIT.md) | Security / performance / architecture / data-quality / compliance audit of the OpenEMR codebase |
| [USERS.md](USERS.md) | Target user (PCP, 20-patient day) and the use cases the agent addresses |
| [W1_ARCHITECTURE.md](W1_ARCHITECTURE.md) | Week 1 AI integration plan — verification, observability, failure modes, scaling |
| [W2_ARCHITECTURE.md](W2_ARCHITECTURE.md) | **Week 2** multimodal evidence agent — schemas, vision pipeline, hybrid RAG, supervisor + worker graph, eval gate |
| [THREAT_MODEL.md](THREAT_MODEL.md) | **Week 3** attack surface map for the adversarial platform — 6 categories, highest-risk findings, coverage prioritization |
| [ARCHITECTURE.md](ARCHITECTURE.md) | **Week 3** multi-agent adversarial-platform architecture — Orchestrator / Red Team / Judge / Documentation agents, inter-agent comms, regression harness, observability |
| [PATIENT_DASHBOARD_MIGRATION.md](PATIENT_DASHBOARD_MIGRATION.md) | **Week 2 surprise port** — defense of Next.js 15 + Auth.js v5 framework choice for the FHIR-backed patient dashboard |
| [W1_COSTS.md](W1_COSTS.md) | W1 cost & scale projections (100 → 100K users) |
| [W2_COSTS.md](W2_COSTS.md) | **Week 2** cost & latency report — vision / RAG / multi-format / cookbook tier / dashboard, p50/p95, bottleneck analysis |
| [COSTS.md](COSTS.md) | **Week 3** adversarial-platform cost analysis — per-attempt decomposition, dev spend, projections at 100/1K/10K/100K runs/day, architectural changes per scale |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) / [W2_DEMO_SCRIPT.md](W2_DEMO_SCRIPT.md) | W1 / W2 demo video scripts |
| [INTERVIEW_PREP.md](INTERVIEW_PREP.md) | AI-interview talking points keyed to repo artifacts |
| [W2_PRESEARCH.md](W2_PRESEARCH.md) | W2 architectural-discovery checklist (16 questions) — design rationale for the Clinical Co-Pilot |
| [PRESEARCH.md](PRESEARCH.md) | **Week 3** architectural-discovery checklist (11 sections × 3 phases) — design rationale for the adversarial platform that attacks the W2 target |
| [EVIDENCE.md](EVIDENCE.md) | **Week 3** — one finding traced end-to-end (Orchestrator decision → Red Team attack → target response → Judge verdict → Documentation Agent → trust gate → W2 eval gate). Each step links to the deployed URL where the artifact can be inspected directly. |

**W3 hard gate — deployed target URL** (the live system the adversarial
platform attacks; required with every checkpoint per the W3 PRD):

  https://copilot-agent-production-ba87.up.railway.app

Health check: `GET /healthz` → `{"status":"ok"}`. The Red Team Agent's
`httpx.AsyncClient` (`agent-service/src/redteam/target.py`) points at
this base URL by default; every attempt in
`agent-service/evals/redteam_runs/` records the real status code +
response body from this endpoint. Nothing is mocked.

**Deployed apps:**

- **Embedded co-pilot** (PHP module rendered into the patient chart):
  https://openemr-production-0996.up.railway.app/
- **Modern dashboard** (Next.js port of the patient chart):
  https://openemr-dashboard-production.up.railway.app/
- **System visibility page** (W2: corpus, routing, eval coverage, live retrieval inspector):
  https://copilot-agent-production-ba87.up.railway.app/visibility
- **Adversarial platform dashboard** (W3: coverage, vuln pipeline, recent campaigns with Orchestrator rationale, attempts trend):
  https://copilot-agent-production-ba87.up.railway.app/adversarial
- **Standalone agent UI** (token-less demo / fallback):
  https://copilot-agent-production-ba87.up.railway.app/

**Reviewer entry points:**

- **No login required:**
  - **/visibility** — W2 agent introspection (corpus, routing, eval coverage, live retrieval inspector)
  - **/adversarial** — W3 platform operator view (92 attempts of evidence, 3 live + 7 pending vulns, Orchestrator rationale per campaign)
  - The standalone agent UI (token-less /demo/chat)
- **OpenEMR login required:**
  - The embedded co-pilot panel (Farrah Rolle is a good demo patient with rich data)
- **OAuth flow on first hit:**
  - The Next.js dashboard (signs in against OpenEMR's existing OIDC server)

**Stack:**

- OpenEMR (PHP 8.2 + Apache + MariaDB) →
  `interface/modules/custom_modules/oe-module-clinical-copilot/`
  embedded chat panel
- `agent-service/` — Python (FastAPI + Anthropic Claude) → Redis
  context cache → Langfuse traces. Hybrid RAG (BM25 + Voyage +
  Cohere Rerank) over a 24-chunk USPSTF / ADA / ACIP / ACC-AHA / CDC
  guideline corpus
- Multi-format ingestion: PDF (vision), HL7 v2 ORU + ADT (structured
  parse), DOCX referral letters, XLSX workbooks, TIFF fax packets
- `dashboard/` — Next.js 15 (App Router, React 19 server components)
  + Auth.js v5 OIDC against OpenEMR's `/oauth2/default` endpoint
- Four+1 Railway services: OpenEMR + agent + Redis + MySQL + dashboard

**Quick start (local):**

```bash
docker compose up -d         # OpenEMR + MariaDB + phpMyAdmin
docker compose logs -f openemr   # watch first-boot install (~5 min)
# then open http://localhost:8080  (admin / pass)
```

**Quick start (Railway deploy):**

```bash
railway login
railway init                 # create a new project
railway add --plugin mysql   # provision the MariaDB plugin
railway up                   # build + deploy from this Dockerfile
# then configure env vars in the Railway dashboard (see Dockerfile header)
```

---

# OpenEMR (upstream README)

[![Syntax Status](https://github.com/openemr/openemr/actions/workflows/syntax.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/syntax.yml)
[![Styling Status](https://github.com/openemr/openemr/actions/workflows/styling.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/styling.yml)
[![Testing Status](https://github.com/openemr/openemr/actions/workflows/test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/test.yml)
[![JS Unit Testing Status](https://github.com/openemr/openemr/actions/workflows/js-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/js-test.yml)
[![PHPStan](https://github.com/openemr/openemr/actions/workflows/phpstan.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/phpstan.yml)
[![Rector](https://github.com/openemr/openemr/actions/workflows/rector.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/rector.yml)
[![ShellCheck](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml)
[![Docker Compose Linting](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml)
[![Dockerfile Linting](https://github.com/openemr/openemr/actions/workflows/hadolint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/hadolint.yml)
[![Isolated Tests](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml)
[![Inferno Certification Test](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml)
[![Composer Checks](https://github.com/openemr/openemr/actions/workflows/composer.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer.yml)
[![Composer Require Checker](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml)
[![API Docs Freshness Checks](https://github.com/openemr/openemr/actions/workflows/api-docs.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/api-docs.yml)
[![codecov](https://codecov.io/gh/openemr/openemr/graph/badge.svg?token=7Eu3U1Ozdq)](https://codecov.io/gh/openemr/openemr)

[![Backers on Open Collective](https://opencollective.com/openemr/backers/badge.svg)](#backers) [![Sponsors on Open Collective](https://opencollective.com/openemr/sponsors/badge.svg)](#sponsors)

# OpenEMR

[OpenEMR](https://open-emr.org) is a Free and Open Source electronic health records and medical practice management application. It features fully integrated electronic health records, practice management, scheduling, electronic billing, internationalization, free support, a vibrant community, and a whole lot more. It runs on Windows, Linux, Mac OS X, and many other platforms.

### Contributing

OpenEMR is a leader in healthcare open source software and comprises a large and diverse community of software developers, medical providers and educators with a very healthy mix of both volunteers and professionals. [Join us and learn how to start contributing today!](https://open-emr.org/wiki/index.php/FAQ#How_do_I_begin_to_volunteer_for_the_OpenEMR_project.3F)

> Already comfortable with git? Check out [CONTRIBUTING.md](CONTRIBUTING.md) for quick setup instructions and requirements for contributing to OpenEMR by resolving a bug or adding an awesome feature 😊.

### Support

Community and Professional support can be found [here](https://open-emr.org/wiki/index.php/OpenEMR_Support_Guide).

Extensive documentation and forums can be found on the [OpenEMR website](https://open-emr.org) that can help you to become more familiar about the project 📖.

### Reporting Issues and Bugs

Report these on the [Issue Tracker](https://github.com/openemr/openemr/issues). If you are unsure if it is an issue/bug, then always feel free to use the [Forum](https://community.open-emr.org/) and [Chat](https://www.open-emr.org/chat/) to discuss about the issue 🪲.

### Reporting Security Vulnerabilities

Check out [SECURITY.md](.github/SECURITY.md)

### API

Check out [API_README.md](API_README.md)

### Docker

Check out [DOCKER_README.md](DOCKER_README.md)

### FHIR

Check out [FHIR_README.md](FHIR_README.md)

### For Developers

If using OpenEMR directly from the code repository, then the following commands will build OpenEMR (Node.js version 24.* is required) :

```shell
composer install --no-dev
npm install
npm run build
composer dump-autoload -o
```

### Contributors

This project exists thanks to all the people who have contributed. [[Contribute]](CONTRIBUTING.md).
<a href="https://github.com/openemr/openemr/graphs/contributors"><img src="https://opencollective.com/openemr/contributors.svg?width=890" /></a>


### Sponsors

Thanks to our [ONC Certification Major Sponsors](https://www.open-emr.org/wiki/index.php/OpenEMR_Certification_Stage_III_Meaningful_Use#Major_sponsors)!


### License

[GNU GPL](LICENSE)
