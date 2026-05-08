# AUDIT.md — OpenEMR fork, AgentForge Clinical Co-Pilot

> Audit scope: OpenEMR `master` (8.1.1-dev), read-only review for the
> purpose of integrating an AI clinical co-pilot. Reviewed: REST + FHIR
> APIs, OAuth2 + ACL, the patient-data schema, the module/event surface,
> audit-logging infrastructure, and HIPAA posture.
> Out of scope: penetration testing, runtime profiling, third-party
> dependency CVE scan.

---

## Summary

OpenEMR is a mature, ONC-certified EHR with a **strong cryptographic
toolkit, a comprehensive ACL/audit/event infrastructure, and a clean
service layer that an AI agent can target without resorting to raw SQL**.
It is also a 25-year-old codebase that ships with several defaults a
hospital CTO would not put into production, and several data-model
realities that an agent must be designed *around*, not *on top of*. The
five findings below shaped every decision in [ARCHITECTURE.md](ARCHITECTURE.md):

**1. Authorization is role-based, not patient-based.** `PatientService`,
`EncounterService`, and the FHIR controllers gate access on ACL
permissions like `patients/med` (`src/Common/Acl/AclMain.php`,
`src/Services/PatientService.php`). They do **not** filter by the
caller's care-team relationship to the patient. Any clinician with
"medical records" permission can query any patient via the API. An AI
agent layered on this surface inherits the same blast radius — and
amplifies it, because a chat interface invites broader queries than a
chart click. Our agent must enforce a patient-context boundary above
the service layer.

**2. There is no built-in de-identification or redaction.** The FHIR
`$export` operation (`src/RestControllers/FHIR/Operations/`) returns
full PHI — name, DOB, MRN, free-text notes — with no Safe Harbor
filter. Today the LLM provider sees whatever the agent tool returns.
This is solvable (BAA + scoped redaction), but it is a build
requirement, not a config flag.

**3. The schema has data-quality landmines an agent will trip over.**
`form_vitals` stores weight/height/temperature as decimals with **no
unit column** — a value of `37` could be normal °C or fatal °F.
`prescriptions.drug` is a varchar of free text where strength, route,
and frequency are sometimes embedded ("Lisinopril 10mg PO daily") and
sometimes split across `dosage`/`route`/`interval`. `lists.diagnosis`
stores ICD-9 and ICD-10 codes side-by-side with no canonical marker.
Any agent claim about meds, vitals, or problems must be defensively
parsed and surfaced with provenance.

**4. There is no application-level cache and several agent-critical
queries are unindexed.** No composite `(pid, type, enddate)` on
`lists`, no `(pid, date DESC)` on `forms`. With ADODB and PHP-FPM
session-file locking, a 5-tool agent turn realistically floors at
~350-400 ms before the LLM call. Caching the per-patient context
bundle on encounter open is the single biggest latency win.

**5. The integration surface itself is excellent.** The
`oe-module-*` pattern (composer.json + ModuleManagerListener +
PSR-4 autoload), Symfony EventDispatcher, the `OpenEMR\Common\Logging\SystemLogger`
(Monolog), `EventAuditLogger`, and the AES-256 `CryptoGen` are
all production-grade. We do not need to fork the core; we drop a
custom module under `interface/modules/custom_modules/oe-module-clinical-copilot/`,
subscribe to events, call services through the DI container, and log
through the existing Monolog/audit pipelines.

**Net:** OpenEMR is the right substrate. The work is not "make it
support an agent" — it's "build the patient-context, redaction,
verification, and observability layers that the core deliberately
left to the integrator."

---

## 1. Security audit

### 1.1 Authentication

- **Password hashing.** Modern, configurable: bcrypt (default cost 10),
  Argon2i, Argon2id, SHA512HASH. Implementation in
  `src/Common/Auth/AuthHash.php` uses `hash_equals()` for comparison
  (timing-safe) and re-hashes legacy bcrypt salts on next successful
  login. **Recommendation:** raise default cost to 12.
- **Timing-attack prevention.** `AuthUtils.php` keeps a dummy bcrypt
  hash and runs `password_verify` against it for unknown usernames so
  timing does not reveal user existence. Same pattern for the LDAP
  path. Good.
- **MFA.** TOTP and U2F supported (`src/Common/Auth/MfaUtils.php`).
  TOTP secrets are encrypted via the standard `CryptoGen` pipeline.
  Caveat: a legacy fallback decrypts with a password-derived key if
  the standard key is missing — a second trust path that should be
  documented and ideally removed.
- **Sessions.** `SessionUtil.php` sets `cookie_samesite=Strict`,
  `gc_maxlifetime=14400` (4h). Core OpenEMR sets `cookie_httponly=false`
  by design (the JS layer needs to switch sessions for portal vs.
  practice contexts) — patient portal and OAuth2 paths set it true.
  **Gap:** no explicit `session_regenerate_id()` post-MFA — relies on
  destroy/recreate, which is functionally similar but easier to reason
  about with explicit regeneration.

### 1.2 Authorization (ACL) — High severity

- **GACL is solid for role-based gates.** `AclMain::aclCheckCore()` is
  the chokepoint; superuser bypass is explicit; deny-overrides-allow
  is enforced.
- **Critical gap: no patient-record-level filter.**
  `PatientService::getOne()`, `getAll()`, and the FHIR
  `Patient`/`Encounter`/`Observation` controllers do not filter by the
  caller's provider relationship to the patient. The check is "does
  this user have *patients/med*?" not "does this user *treat* this
  patient?" SMART scopes (`patient/Patient.read`, `user/Patient.read`)
  are parsed but the patient-compartment interface
  (`IPatientCompartmentResourceService`) is not actually enforced at
  query time. **Impact for the agent:** a chat tool with broad clinician
  scope can surface any patient. We must implement
  patient-context middleware (see [ARCHITECTURE.md](ARCHITECTURE.md) §3).

### 1.3 API authentication (OAuth2 + SMART-on-FHIR)

- Full league/oauth2-server stack with PKCE (`S256`), authorization
  code + client credentials grants, refresh tokens, and
  `/.well-known/smart-configuration` discovery.
- Keys: RSA-2048 keypair generated on first boot, stored under
  `sites/default/documents/certificates/` with `0640`/`0660`
  permissions. The key passphrase is itself AES-256-encrypted in the
  `keys` table; the master encryption key lives encrypted on disk
  under `sites/<site>/documents/logs_and_misc/methods/`.
- **Gaps:** no token revocation endpoint advertised in
  `SMARTConfigurationController`; no automatic key rotation
  (replacement requires manual file swap + DB update; all extant
  tokens become invalid).

### 1.4 SQL injection and output escaping

- Query layer (`library/sql.inc.php`) wraps ADODB and forces parameter
  binding for `sqlStatement`, `sqlInsert`, `sqlUpdate`, `sqlDelete`.
  Spot checks of high-traffic paths (login, patient list, encounter
  forms) showed no raw `$_GET`/`$_POST` interpolation into SQL.
- Output: `library/htmlspecialchars.inc.php` provides `text()`,
  `attr()`, `attr_url()`, `js_escape()`, `csvEscape()` (the last
  guarding against CSV formula-injection). Twig templates (newer
  forms) use auto-escape by default. Smarty templates (legacy) and
  raw `<?= ?>` blocks (oldest pages) require the developer to escape
  explicitly — bug surface, but not actively broken.

### 1.5 Secrets and key management — High severity

- `sites/default/sqlconf.php` ships with literal `$login='openemr'; $pass='openemr';`.
  Production deployments must override at deploy time. The repo's
  `.env` support (`vlucas/phpdotenv`) is present in dependencies but
  not enforced at install. **Action:** Railway deployment config will
  inject DB credentials via env vars; sqlconf.php must be templated,
  not committed with real values.
- OAuth2 keys: well-handled by upstream code, **but split-storage
  trap**. The drive-level key (`sites/default/documents/logs_and_misc/methods/sixa,sixb`)
  and the OAuth2 RSA keypair (`sites/default/documents/certificates/oa{private,public}.key`)
  live on the filesystem; their counterparts (raw key in `keys` table,
  passphrase in `keys.oauth2passphrase`, encrypted client secrets in
  `oauth_clients`) live in the DB. On any container redeploy that
  reset the filesystem, OpenEMR's `OAuth2KeyConfig::verifyKeys()`
  silently regenerates the on-disk halves but cannot insert the new
  DB rows (duplicate-key error against the old ones), leaving the
  store inconsistent. Result: every encryption op throws
  `CryptoGenException: Key in drive is not compatible with key in
  database — Exiting`, or `POST /oauth2/default/token` returns 500
  with empty body on every grant.
- **Mitigation in this deployment (active):** Railway persistent
  volume mounted at `/var/www/localhost/htdocs/openemr/sites/default/documents/`
  (volume name `agentforge-clinical-copilot-volume`), seeded on first
  boot from a baked template at `/opt/openemr-documents-template/`;
  see `Dockerfile` and `docker-entrypoint-wrapper.sh:19-26`.
  Subsequent boots see an initialized volume (the seed only runs if
  `logs_and_misc/methods/` is absent) and the drive key persists.
- **Recovery procedure if a key-mismatch incident does occur** (e.g.,
  the volume was added *after* an earlier ephemeral-fs deploy
  encrypted client secrets in the DB — encountered once on
  2026-05-08): the DB-stored client passphrases were encrypted with
  the previous drive key and are no longer decryptable, so token
  mint will 500 indefinitely. Recovery is a fresh OAuth client
  registration plus an env-var swap, no data loss:

  1. `POST /oauth2/default/registration` with the agent's required
     SMART scopes — receives a new `client_id` + `client_secret`
     whose stored passphrase is encrypted with the *current* drive
     key on the live container.
  2. `UPDATE oauth_clients SET is_enabled = 1 WHERE client_id =
     '<new>'` — DCR registers clients in `is_enabled=0` by default.
  3. Update `OPENEMR_OAUTH_CLIENT_ID` and `OPENEMR_OAUTH_CLIENT_SECRET`
     on the agent service (Railway → auto-redeploys).
  4. Verify: `POST /oauth2/default/token` → 200, then `POST /demo/chat`
     for a known patient → response with non-empty `sources[]`.

  Old client rows can be left in `oauth_clients` (their stale
  passphrases are unusable so they pose no auth risk) or disabled
  with `UPDATE oauth_clients SET is_enabled=0 WHERE client_name LIKE
  'AgentForge Co-Pilot v%' AND id != <new id>`.
- No code-level key rotation mechanism for DB credentials, OAuth2
  keys, or the audit-log encryption key.

### 1.6 Audit logging

- `EventAuditLogger.php` (singleton) maps inserts/updates/deletes on
  47 tables to event categories. `auditSQLEvent()` is the workhorse;
  `recordLogItem()` writes to the `log` table with optional AES-256
  encryption of the comments column (`enable_auditlog_encryption`,
  off by default).
- Sinks: database log table + optional ATNA syslog (RFC 3164 audit
  trail to an external SIEM, off by default).
- **What is logged:** logins, MFA events, OAuth2 key rotation,
  inserts/updates on patient-mapped tables.
- **What is not logged by default:** SELECT operations
  (`audit_events_query`, off by default — too verbose). Failed ACL
  checks logged only as generic "other". REST API request bodies and
  response payloads are never logged.
- **Integrity:** the `log_validator` / `log_comment_encrypt` checksum
  lives in the same DB as the log row it protects. No HMAC with an
  external key. If the DB is compromised, log tampering is undetectable.

### 1.7 PHI exposure surfaces

- `bootstrap.php` sets `display_errors=0` and `log_errors=1` — safe
  default. **However** the runtime debug toggle in `interface/globals.php`
  flips `display_errors=1` when the admin enables it; a fatal error
  in a patient-data path could then leak SQL fragments and stack
  traces to the browser.
- File uploads (documents, lab PDFs) gated only by ACL `patients/docs`,
  not by patient relationship.

### Top-5 security findings (ordered by impact on the AI agent)

| # | Finding | Severity | Impact on agent |
|---|---------|----------|-----------------|
| 1 | No patient-level ACL in REST/services | **High** | Agent can surface unrelated patients to any clinician with `patients/med` |
| 2 | Plaintext DB creds in default `sqlconf.php` | **High** | Deployment-time only; trivial to fix but easy to forget |
| 3 | No de-identification before `$export`/REST egress | **High** | Full PHI flows to LLM provider unless we redact |
| 4 | Audit-log integrity is checksum-in-same-DB | **Medium** | Hospital security review will flag this |
| 5 | Debug toggle can flip `display_errors=1` in prod | **Medium** | One bad config click = stack-trace PHI leak |

---

## 2. Performance audit

The agent's latency budget is the single hardest constraint: a physician
between rooms wants an answer in seconds. Below are the database and
runtime characteristics that will dominate that budget.

### 2.1 Hot tables for the agent

| Table | Row size | Indexes that exist | Indexes the agent needs |
|-------|----------|---------------------|-------------------------|
| `patient_data` (~75 cols) | ~3.5 KB | `pid` UNIQUE, `uuid` UNIQUE, `(lname,fname)`, `DOB` | `(pid, status)` for active-patient filters |
| `forms` (form-instance index) | ~500 B | `pid_encounter` composite, `form_id` | **`(pid, date DESC)`** — currently scans full table |
| `lists` (problems/meds/allergies) | ~1 KB | `pid`, `type`, `uuid` | **`(pid, type, enddate)`** — every "active meds" query is unindexed |
| `prescriptions` | ~1.2 KB | `patient_id`, `uuid` | `(patient_id, active, start_date)` |
| `pnotes` | ~2 KB header + LONGTEXT body | `pid` only | `(pid, date DESC)` + don't `SELECT *` (body can be MB-sized) |
| `procedure_result` | ~800 B | `procedure_report_id`, `uuid` | Patient-direct lookup requires 3-way join through `procedure_report` → `procedure_order` |

### 2.2 Query patterns to avoid copying

- **N+1 in patient summary.** `interface/patient_file/summary/demographics.php`
  loops over patient issues and calls `getCodeText()` per code — 20
  active diagnoses ≈ 60+ queries. Don't reproduce this in the agent's
  context-loader; do one JOIN.
- **`SELECT *` against wide tables.** `library/patient.inc.php`'s
  `getPatientData()` pulls all 75 columns of `patient_data`. Cheaper
  to project the ~15 the agent actually reasons over.
- **Free-text parsing in PHP after the SELECT.** `add_edit_issue.php`
  pulls `lists.diagnosis` and `explode(";", ...)` in app code. If the
  agent needs structured codes, do it in the SQL with `JSON_TABLE`
  or move the parsing to a one-time batch normalization.

### 2.3 FHIR / REST as agent data source

- No HTTP caching headers, no ETags, no default pagination caps on
  `/fhir/Medication?patient=…`. A patient with 500 historical scripts
  returns a 2 MB JSON blob — ~160 ms of network alone.
- The Twig template cache exists; there is no query-result or
  service-result cache. `src/Common/Utils/CacheUtils.php` exists but
  is not wired into the FHIR controllers.

### 2.4 Concurrency

- PHP-FPM with file-backed session locking: a long-running agent
  request holds the user's session lock and blocks the same user's
  next browser click. Mitigations: use Redis session handler (already
  supported), or `session_write_close()` immediately after auth in the
  agent endpoint.
- ADODB does not pool connections; a fan-out of 5 tool calls per turn
  × 50 concurrent users = 250 connections — needs `max_connections`
  tuning or pooling (PgBouncer-style for MariaDB: ProxySQL).

### 2.5 Latency budget for the agent (back-of-envelope)

```
Single agent turn, no caching:
  Tool call dispatch + auth ............ ~30 ms
  5 service-layer queries × ~50 ms ..... ~250 ms
  JSON serialization + response ........ ~20-100 ms (size-dependent)
  -----------------------------------------------------
  Backend floor (no LLM)              ≈ 350-400 ms
  + Claude / GPT-4 round-trip          ~1500-3000 ms
  -----------------------------------------------------
  Realistic first-token latency      ≈ 2-4 s
```

The single-largest win is **caching the patient context bundle on
encounter open** so the first agent turn after the physician opens the
chart is reading hot data, not querying it.

### Top-5 performance risks for the agent

1. Missing composite indexes on `lists` and `forms` — every "active
   meds / recent encounters" query is a scan. **High.**
2. No application-level caching layer for patient context. **High.**
3. FHIR responses unbounded + uncached. **High.**
4. `lists` and `prescriptions` split medication state across two
   tables; the agent will read both. **Medium.**
5. LONGTEXT columns (`pnotes.body`, `prescriptions.comments`) on
   `SELECT *` paths. **Medium.**

---

## 3. Architecture audit

### 3.1 Layout

| Directory | Purpose |
|-----------|---------|
| `interface/` | Legacy PHP page tier — patient-file UI, forms, admin |
| `interface/patient_file/` | The patient chart (where the agent UI lives) |
| `interface/modules/custom_modules/` | **Drop-in modules** — our integration target |
| `src/` | PSR-4 `OpenEMR\` namespace — modern services, FHIR, auth, crypto |
| `src/Services/` | Data access façade (`PatientService`, `EncounterService`, …) |
| `src/RestControllers/` | HTTP handlers, ACL gates, response formatting |
| `src/Events/` | Symfony EventDispatcher payloads (`PatientCreatedEvent`, …) |
| `src/Common/Logging/` | `SystemLogger` (Monolog/PSR-3) + `EventAuditLogger` |
| `src/Common/Crypto/` | `CryptoGen` — AES-256-CBC with versioned keys |
| `library/` | Pre-PSR helpers — `sql.inc.php`, `auth.inc.php`, formatting |
| `apis/` + `_rest_routes.inc.php` | REST + FHIR dispatch |
| `oauth2/` | OAuth2 / SMART-on-FHIR endpoints |
| `templates/` | Twig templates (with Smarty in legacy modules) |
| `sql/`, `sites/` | Schema + per-tenant config |

### 3.2 Request lifecycle

```
GET /interface/patient_file/summary/demographics.php?pid=42
  └─ index.php              loads sites/<site>/sqlconf.php
  └─ interface/globals.php  Composer autoload → Kernel::boot() →
                            DB (ADODB), Symfony EventDispatcher,
                            session, DI container (Firehed/Container)
  └─ ACL gate               aclCheckCore('patients','demo')
  └─ Page renders patient panels (PHP + Twig + jQuery)
  └─ ▶ Co-pilot panel injected via Twig include from our module
       └─ JS loads → AJAX to module endpoint → CopilotService →
          PatientService / FHIR endpoints → LLM provider →
          response → SystemLogger.info(...) → AuditLogger.newEvent(...)
```

### 3.3 Module system — our integration surface

Existing examples to follow: `oe-module-faxsms`,
`oe-module-dashboard-context`, `oe-module-prior-authorizations`. Each is
a self-contained tree:

```
oe-module-clinical-copilot/
├── composer.json              "type": "openemr-module"
├── info.txt                   module registry key
├── openemr.bootstrap.php      Laminas Module init
├── ModuleManagerListener.php  install / enable / disable hooks
├── moduleConfig.php           menu items, routes
├── src/                       PSR-4 OpenEMR\Modules\ClinicalCopilot\
│   ├── CopilotService.php     orchestrates LLM + tool calls
│   ├── Tools/                 each agent tool = one class
│   ├── Verification/          source-attribution + constraint checks
│   ├── Events/                listens to PatientViewedEvent
│   └── Http/                  AJAX controller for chat turns
├── public/{js,css}/           chat panel UI
├── templates/                 Twig template injected into patient_file
└── sql/install.sql            our own tables (chat history, eval logs)
```

The module is registered through Symfony EventDispatcher
(`ServiceContainer::getEventDispatcher()`). Useful events to subscribe
to: `PatientCreatedEvent`, `PatientUpdatedEvent`, encounter open
(via `EncounterMenuEvent`).

### 3.4 Data access surface

Three options, ordered by quality:

1. **Service classes** (`src/Services/PatientService.php`,
   `EncounterService`, `AllergyIntoleranceService`, `ConditionService`,
   …). Inject via DI. Cleanest. Bypasses HTTP overhead. Inherits ACL.
2. **REST endpoints** (`/api/patient/:puuid/...`, `/fhir/...`). Adds
   serialization and HTTP cost (~30-50 ms) but makes the agent
   testable in isolation.
3. **Raw SQL.** Don't.

The agent will use option 1 internally and option 2 for any external
verification or eval harness.

### 3.5 Logging surface

- `OpenEMR\Common\Logging\SystemLogger` is a PSR-3 logger backed by
  Monolog. Get an instance via DI or
  `ServiceContainer::getLogger()`. Use this for all agent traces.
- `OpenEMR\Common\Logging\EventAuditLogger::newEvent(...)` for
  HIPAA-relevant access events. Will fire ATNA syslog if enabled.

### 3.6 Recommended insertion point

- **UI:** Twig partial included from
  `interface/patient_file/summary/demographics.php` (or via JS DOM
  injection if we don't want to fork that file).
- **Backend:** AJAX endpoint registered in our module, dispatched via
  the existing `controller.php` or a module-owned route.
- **Data:** services from `src/Services/`, called inside agent tools.
- **Auth:** trust the existing session
  (`SessionWrapperFactory::getInstance()`), enforce ACL via
  `acl_check()`, then add our own patient-context middleware to close
  the gap from §1.2.

---

## 4. Data quality audit

Each item below is a reason an agent's response can be wrong even when
all the code paths return successfully.

### 4.1 Demographics

`patient_data.sex`, `race`, `ethnicity`, `language`, `country_code` are
all `varchar(255) NOT NULL DEFAULT ''` — no FK to `list_options` despite
the option lists existing. Real demo data has `race=''` and a populated
`ethnoracial='Latina'` — the agent querying `race` misses this patient.
`gender_identity` (TEXT) is separate from `sex`; logic for "current
gender vs. sex at birth" is application-defined.

### 4.2 Medications — multiple landmines

- **Free-text drug names with embedded structure.**
  `prescriptions.drug` (varchar(150)) commonly contains
  `"Lisinopril 10mg PO daily"`. `dosage`, `route`, `interval` may also
  be populated — sometimes redundantly, sometimes contradictorily.
  `drug_dosage_instructions` (longtext) is yet a third place the same
  info appears.
- **RxNorm coding is sparse.** `rxnorm_drugcode` (varchar(25))
  nullable; a real-world percentage of records have it empty.
- **Active-vs-discontinued is split across two tables.**
  `prescriptions.active` (0/1) and `lists_medication.is_primary_record`
  (1=primary, 0=reported). "Current medication list" requires JOIN
  `prescriptions` → `lists` → `lists_medication`, filter by
  `lists.activity=1`, AND check `(end_date IS NULL OR end_date > now)`.
  An agent that queries `prescriptions` alone will return zombies.

### 4.3 Allergies

`lists` rows where `type='allergy'`. Allergen name is a free-text
`title` field (e.g., "penicillin", "Penicillin", or even
"PCN"). `severity_al` is `varchar(50)` NULL — sometimes "Mild", sometimes
"moderate reaction" (free-text), sometimes empty. `verification` is
`varchar(36) NOT NULL DEFAULT ''` — its valid values
(`unconfirmed`/`confirmed`/`refuted`/`entered-in-error`) are documented
but not enforced; empty is the default. The agent must treat
"penicillin allergy" as a fuzzy lookup, not an equality check.

### 4.4 Problem list / diagnoses

`lists.diagnosis` is `varchar(255)` storing strings like
`"ICD9::401.0"`, `"ICD10:I10"`, `"SNOMED:38341003"` — code system as a
prefix in the same field. The `code_types` table marks ICD-9 and SNOMED
as `active=0` and ICD-10 as `active=1`, but ICD-9 rows still exist and
are still inserted by some legacy paths. **Both can be `activity=1`
for the same problem on the same day**, so a naive "active diagnoses"
query double-counts. The agent must dedupe by problem, prefer ICD-10,
and not treat ICD-9 vs ICD-10 hits as independent evidence.

### 4.5 Lab results

`procedure_result.result` is varchar(255) free-text — `"142"`,
`"75% on room air"`, `"negative"`. `units` is varchar(31) free-text;
`range` is varchar(255) free-text (`"85-99"`, `"<100"`,
`"<5 normal"`). `result_code` (intended LOINC) is often empty.
`abnormal` is intended as `no|yes|high|low` enum but is a varchar.
**The agent cannot reliably abnormal-flag a result on its own** — it
must read the `abnormal` column verbatim and report uncertainty when
empty.

### 4.6 Vitals — the worst offender

`form_vitals` columns are `weight`, `height`, `temperature`, `bpd`,
`bps`, `pulse`, `oxygen_saturation` — all `decimal` with **no unit
column** anywhere on the row. The only related metadata is
`temp_method` ("oral"/"rectal"). Defaults are `0.0`, so "not taken"
is indistinguishable from "zero". A `weight=75` could be 75 kg or 75
lb — a 2.2× error. A `temperature=37` is a normal °C reading or a
fatal °F reading. **The agent must surface vitals with explicit
"unit unknown" provenance unless the deploying clinic has been
configured.**

### 4.7 Encounters

`form_encounter.date` is nullable. `encounter` is a bigint but not
auto-increment — higher ID does not mean later encounter. There is no
"is this the current visit" flag; agents identifying "today's
encounter" must combine `pid` + `date::date = CURDATE()` and accept
that there is sometimes more than one match.

### 4.8 Notes

`pnotes.body` is longtext. `form_soap` has subjective/objective/
assessment/plan TEXT fields with an `authorized` tinyint whose
semantics ("signed"? "reviewed"?) vary by form. There is no
cryptographic signature on signed notes. `pnotes.deleted` is a soft
delete; `form_soap` has no `deleted` column.

### 4.9 Demo data

`sql/example_patient_data.sql` ships ~10–15 fake patients with realistic
names but **no allergies, no labs, and minimal medications**. Useful
for UI testing; useless as an agent eval set. We will need to either
seed Synthea-generated data or hand-craft adversarial cases (see
[ARCHITECTURE.md](ARCHITECTURE.md) §6 — Eval).

### Top-5 data hazards for the agent

1. **Vital-sign units unspecified** — silent dosing-relevant errors.
2. **Free-text medication strings** — `drug` field embeds strength /
   route / frequency; structured fields are not authoritative.
3. **Dual ICD-9 + ICD-10 problem rows** — double-count risk.
4. **Active-vs-discontinued split across two tables** — "zombie meds"
   if you query `prescriptions` alone.
5. **Allergen names are free-text** — needs fuzzy match, not equality.

---

## 5. Compliance & regulatory audit

OpenEMR is ONC-certified. That certifies the *core* is capable of
HIPAA-compliant operation; it does not certify any particular deployment
*is* compliant, and it explicitly does not cover an LLM integration we
are about to bolt on. The findings below are the gaps a hospital CTO
will ask about.

### 5.1 §164.312(b) — Audit & accountability

- **What works:** the `EventAuditLogger` infrastructure is well-built;
  47 PHI-bearing tables are mapped to event categories;
  optional AES-256 comment encryption; optional ATNA syslog egress
  for SIEM ingestion.
- **What's missing:** SELECT auditing (`audit_events_query`) is off
  by default — too noisy for general use, but **must be on for the
  agent's data-access path** so we have a trail of every PHI field
  read on the agent's behalf. Field-level audit (which columns of
  which row) is not built in.

### 5.2 §164.514 — Minimum necessary / access controls

This is the §1.2 finding restated in regulatory terms: the
"minimum necessary" rule is not enforced at the API layer. SMART
scopes parse correctly but a token with `user/Patient.read` reads
*any* patient. Our agent must enforce a per-turn patient-context
boundary and, ideally, a per-user care-team filter.

### 5.3 §164.312(a)(2)(iv) — Encryption

- **At rest:** `CryptoGen` (AES-256-CBC, PBKDF2 key derivation,
  versioned keys via `KeySource::Drive` and `KeySource::Database`)
  is mature. **Default is opt-in** — patient demographics are not
  encrypted at rest unless the deploying admin enables it.
- **In transit:** documentation requires HTTPS for OAuth2; there is
  no HTTPS-redirect or HSTS enforcement in the application layer
  (left to Apache/`.htaccess`/load-balancer).
- **Audit log integrity:** the `log_validator` checksum lives next to
  the row it protects. No HMAC with an externally-held key. A DB
  compromise = undetectable log tampering.

### 5.4 §164.514(b) — De-identification / Safe Harbor

There is no de-identification module. `$export` returns full PHI. For
the agent, this means **redaction is our build job**, not OpenEMR's.
Minimum viable redaction layer:

- Strip MRN, SSN, full name, full DOB, email, phone before the LLM
  call.
- Coarsen DOB → age bucket; coarsen visit dates → relative ("3 weeks
  ago").
- Maintain a per-turn token map so the response can be re-hydrated
  with real identifiers in the UI without round-tripping them to
  the LLM.

### 5.5 §164.404 — Breach notification

No anomaly detection. No "user X just read 1,000 charts in 60 seconds"
alert. For the agent we need budgeted access (per-user query rate
limits) and an alert path so a leaked agent token can't quietly
exfiltrate the practice.

### 5.6 BAA / LLM-provider matrix

The case study assumes a signed BAA. Practical, BAA-eligible options:

| Provider | BAA available | Endpoint | Notes |
|----------|---------------|----------|-------|
| **Anthropic (direct)** | Yes (direct contract) | `api.anthropic.com` | Models: Claude 4.x family. Train-opt-out is contractual, not API-flag. |
| **AWS Bedrock (Anthropic Claude)** | Yes (AWS BAA covers Bedrock) | `bedrock-runtime.<region>.amazonaws.com` | PrivateLink available — keeps egress off the public internet. |
| **Azure OpenAI** | Yes (Microsoft Services BAA) | `https://<resource>.openai.azure.com` | Hospitals already trust Azure procurement. |
| **Google Vertex AI** | Yes (Google Cloud BAA) | `<region>-aiplatform.googleapis.com` | Enterprise tier required. |
| **OpenAI public API** | **No** | `api.openai.com` | Cannot receive PHI. |

Default for this project: **Anthropic direct API** for development
(matches the case study's framing); recommend AWS Bedrock + Claude
for any real hospital deployment to inherit AWS PrivateLink + the
hospital's existing AWS BAA.

### 5.7 §164.524 / §164.526 — Patient access rights

`portal/patient/` exists and is functional but the access-control code
predates the modern service layer; we did not deeply audit it. An
agent should not surface patient-portal-only fields to a clinician
view without a deliberate decision (and vice versa).

### 5.8 §164.530(j) — Retention & disposal

No automated log purge. Soft-delete is used but never garbage-collected.
Agent-generated chat logs and eval traces should be on a documented
retention schedule from day one.

### Top-5 compliance gaps that block production

1. **No de-identification before external API egress.** Critical for
   any LLM call.
2. **No patient-context enforcement on the API.** §164.514 minimum-
   necessary is at risk every agent turn.
3. **Audit-log integrity has no external root of trust.** §164.312(b)
   review will flag.
4. **Encryption-at-rest is opt-in, not default.** §164.312(a)(2)(iv)
   posture is "capable", not "compliant".
5. **No anomaly detection for bulk PHI access.** §164.404 breach
   detection depends on manual log review today.

---

## 6. What this audit changes about the agent plan

These are the audit findings that became hard requirements in
[ARCHITECTURE.md](ARCHITECTURE.md):

1. **A patient-context middleware is non-negotiable** — every agent
   tool call carries a `patient_uuid` derived from the session's
   currently-open chart, and the middleware refuses any tool result
   that crosses that boundary. Closes the §1.2 / §5.2 gap.
2. **A redaction layer sits between every tool result and the LLM
   prompt** — name, MRN, SSN, full DOB, email, phone are tokenized
   in; the response is re-hydrated for the UI without round-tripping
   identifiers through the model. Closes the §5.4 gap.
3. **The verification layer treats the schema's free-text fields as
   *evidence*, not *facts*** — every agent claim cites the row(s) it
   came from, and claims about vitals carry an explicit "unit
   verified / unit unknown" tag. Closes the §4.6 / §4.2 gaps.
4. **The patient-context bundle is cached on encounter open** — the
   first agent turn reads from cache, not from MariaDB. Closes the
   §2.5 latency floor.
5. **The agent uses `src/Services/` classes, not raw SQL** — inherits
   ACL, fires existing events, lands in the existing audit pipeline.
6. **Anthropic direct API for dev; document AWS Bedrock as the
   production target** — both BAA-eligible per §5.6.

The agent itself is described in [ARCHITECTURE.md](ARCHITECTURE.md);
the user it serves and the use cases that justify each capability are
in [USERS.md](USERS.md).
