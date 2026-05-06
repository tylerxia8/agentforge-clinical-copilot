# OpenEMR Patient Dashboard (Next.js)

Modern reimplementation of the OpenEMR patient chart, consuming the
existing FHIR API. See [`PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md)
in the repo root for the framework-choice defense (required by
the W2 grading rubric).

## Local development

```bash
cd dashboard
npm install
cp .env.example .env.local
# Fill AUTH_SECRET, OPENEMR_OAUTH_CLIENT_ID, OPENEMR_OAUTH_CLIENT_SECRET.
# See PATIENT_DASHBOARD_MIGRATION.md §6 for OAuth client registration.
npm run dev
```

Open http://localhost:3000 → click "Sign in with OpenEMR" → consent
to the FHIR scopes → land on the patient picker.

## Deploy

Railway service `openemr-dashboard` deploys from `dashboard/` via
Dockerfile. Set the env vars from `.env.example` in the Railway
dashboard. Auth.js needs `AUTH_URL` set to the public Railway URL of
this service so OAuth callbacks resolve.

## Routes

| Path | Auth | Notes |
|---|---|---|
| `/` | none | Redirects to `/patients` (signed-in) or `/login` |
| `/login` | none | OAuth sign-in; calls OpenEMR's `/oauth2/default/authorize` |
| `/patients` | required | Patient picker (lists `/Patient?_count=50`) |
| `/patient/[uuid]` | required | Full dashboard: header + 6 clinical cards |
| `/api/auth/*` | — | Auth.js handlers (callback, signout, session) |

## Cards

Per the W2 spec ("Allergies, Problem List, Medications, Prescriptions,
Care Team + one optional"):

- **Allergies** — `AllergyIntolerance?patient=…` filtered to active
- **Problem List** — `Condition?patient=…` filtered to active/recurrence
- **Medications** — `MedicationRequest?patient=…&status=active`
- **Prescriptions** — `MedicationRequest?patient=…&intent=order` (sorted by authoredOn desc, capped at 50)
- **Care Team** — `CareTeam?patient=…` participants flattened across active teams
- **Encounter History** *(optional)* — `Encounter?patient=…` (10 most recent)

Each card is its own server component wrapped in `<Suspense>` so they
stream independently — a slow Encounter query doesn't block the
Allergies render.
