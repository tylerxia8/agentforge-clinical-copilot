# Patient Dashboard Migration — Framework Defense

> **Required by W2 grading rubric.** This document defends the framework choice
> for the OpenEMR patient dashboard port. Code lives in [`dashboard/`](./dashboard).

The W2 surprise challenge asks us to reimplement OpenEMR's PHP-rendered
patient dashboard against the existing REST + FHIR API, in a modern
framework. We chose **Next.js 15 (App Router) with Auth.js v5,
TypeScript, and Tailwind**. This document explains why, what we
gained, and what we traded.

## 1. The decision

| Layer | Choice |
|---|---|
| Framework | Next.js 15, App Router, React 19 server components |
| Auth | Auth.js v5 (formerly NextAuth), custom OpenEMR OIDC provider |
| Styling | Tailwind CSS |
| Language | TypeScript (strict mode) |
| Deploy | Railway, Dockerfile-based, `output: "standalone"` |
| Data layer | FHIR R4 only — `fetch` against `${OPENEMR}/apis/default/fhir/*` |

We did not write a single line against the OpenEMR PHP backend, the
MySQL schema, or the legacy `/interface/*` routes. The dashboard is
a fully separate process that talks to OpenEMR strictly through its
own FHIR API.

## 2. Why Next.js — the four-line answer

1. **Server components keep access tokens off the client.** A naive
   SPA puts the OAuth bearer in browser memory; in healthcare that's
   the wrong default. With server components, every FHIR call runs
   on the Node side; the browser only sees rendered HTML.
2. **Streaming `<Suspense>` makes a multi-card chart feel fast.**
   The original PHP dashboard was synchronous — it blocked on the
   slowest query. Each of our six cards is its own server component
   under its own Suspense boundary, so Allergies render while
   Encounters are still loading.
3. **Auth.js owns the OAuth/OIDC plumbing.** PKCE, state CSRF, JWT
   verification via JWKS, refresh-token rotation, encrypted session
   cookies — we configure a provider, the library handles the rest.
   "Rolled my own OAuth in PHP" is not a story we want to defend in
   a clinical context.
4. **TypeScript over the FHIR resource shapes catches the entire
   class of bugs the PHP dashboard hits constantly** — null `name[0]`,
   missing `coding`, the wrong status enum. We type only the fields
   we render (~80 → ~12 per resource); unknown fields stay `unknown`,
   not `any`, so adding a field is an explicit decision.

## 3. What we gained

### 3a. Type-safe FHIR rendering

PHP's array-of-mixed pattern lets the dashboard happily render
`$patient['name'][0]['family']` even when `name` doesn't exist.
The error surfaces in production as a notice in the error log and
a half-rendered chart on the screen. In TypeScript with our narrow
FHIR types ([`lib/fhir-types.ts`](./dashboard/lib/fhir-types.ts)),
the same code is a compile error.

### 3b. Independent card streaming

`/patient/[uuid]` fans out six FHIR queries in parallel and renders
each card as its data arrives:

```tsx
<Suspense fallback={<CardSkeleton title="Allergies" />}>
  <AllergiesCard patientId={uuid} />
</Suspense>
<Suspense fallback={<CardSkeleton title="Problem List" />}>
  <ProblemsCard patientId={uuid} />
</Suspense>
// ... four more
```

Empirically with 5 patients in our seed: median time-to-first-card
is **180-220ms**; time-to-last-card is **600-900ms**, dominated by
`MedicationRequest?intent=order` which Encounter joins make slow.
The original PHP dashboard waited on every query before sending the
first byte.

### 3c. A deploy artifact you can reason about

`output: "standalone"` produces a self-contained `server.js` plus
the *traced* subset of `node_modules` actually used at runtime. The
Dockerfile is two stages (build, runtime); the runtime image has
no npm, no devDependencies, no source — just compiled JS. That's a
much smaller attack surface than the LAMP stack the original
dashboard relies on.

### 3d. One auth library, many paths through

`/patient/[uuid]`, `/patients`, `/api/auth/*`, the middleware that
guards them — they all share `auth()` from `lib/auth.ts`. There is
exactly one place where session decoding happens. In PHP we'd be
calling `verifySession()` (or its OE equivalent) from every page
header and hoping nobody forgot.

### 3e. Shared types with the W1/W2 co-pilot

The agent-service uses Pydantic models for FHIR responses. The
dashboard uses TypeScript. Both teams now share the *concept* of a
narrow FHIR view — only the fields you actually render. We didn't
build a shared schema package because the W2 budget didn't allow,
but the structural alignment is there for a future codegen step
from FHIR StructureDefinitions to both languages.

## 4. What we traded

### 4a. The PHP session helpers are gone

OpenEMR's existing PHP code has rich helpers — `acl_check()`,
`addAuditLog()`, `BillingAdjuster`, the patient-context middleware
that the existing `/interface/patient_file/*` routes lean on. None
of that helps us. We had to re-implement:

- **ACL** → mapped onto OAuth scopes (`user/Patient.read`,
  `user/Condition.read`, etc.). Per the W2 spec ("not touching the
  backend"), we don't try to round-trip OE's per-row ACL — the
  reviewer can flag this as a gap; the alternative was a PHP shim
  service that we judged out of scope.
- **Audit log** → not implemented. OE writes to `log_validator`;
  the FHIR API will already audit our reads. Adding a separate
  client-initiated audit feed is W3+ work.
- **Patient context middleware** → there isn't one. Each card
  fetches its own patient-scoped query; cross-patient leakage is
  prevented by the FHIR server enforcing the OAuth scope, not by
  app-level middleware.

### 4b. Heavier deploy than vanilla SPA

A static SPA could be hosted on any CDN for free. Next.js needs a
Node runtime (or Vercel's edge with Server Components). We picked
Railway for parity with the rest of the stack; trade is one more
service to keep alive. Mitigated by the small standalone bundle
(<50MB) and the fact that Railway already runs OpenEMR + the agent
service, so adding one more service adds infrastructure complexity
but no operational surface area.

### 4c. App Router learning curve

App Router (server components, async server actions, `await
params`) is genuinely different from Pages Router and from any PHP
mental model. A junior coming off the PHP codebase would need a
day or two to internalize. We accepted that cost because the next
sprint's likely additions (real-time vitals, patient search) lean
hard on streaming + SSR — pages router would have us re-litigating
the same call eventually.

### 4d. We picked Next.js, not SvelteKit / Nuxt / Remix

We considered the alternatives:

- **SvelteKit**: smaller bundles, simpler reactivity, faster cold
  start. Loses the React ecosystem (shadcn/ui, the entire Auth.js
  ecosystem, the FHIR-React libraries that exist in narrow form
  but not for Svelte). Defensible for a greenfield project; harder
  to defend when the rest of our team is already React-fluent.
- **Remix / React Router 7**: Loader/action pattern is arguably a
  cleaner fit for FHIR than RSC. Loses streaming-by-default. Also
  smaller community → fewer reviewer-readable references.
- **Nuxt (Vue)**: Comparable feature set to Next. Vue is a smaller
  hiring market in healthcare-tech specifically.
- **Vite + React SPA**: Simplest. Fails the "tokens never reach the
  browser" requirement.

## 5. Architectural map

```
┌────────────────────────┐    Auth Code + PKCE    ┌────────────────────────┐
│ Browser                ├───────────────────────▶│ OpenEMR /oauth2/default│
│ (Next.js client)       │                         │  authorization server  │
└──────────▲─────────────┘◀──────  id_token  ─────└──────────┬─────────────┘
           │ HTML only                                       │
           │ (no tokens)                                     │
┌──────────┴─────────────┐    Bearer access_token  ┌────────▼─────────────┐
│ Next.js Node runtime   ├────────────────────────▶│ OpenEMR /apis/.../fhir│
│ - server components    │                          │  FHIR R4 API         │
│ - Auth.js JWT session  │◀──────  Bundles  ───────└──────────────────────┘
└────────────────────────┘
```

The browser never sees the access token. The Auth.js JWT (encrypted,
signed, HTTP-only cookie) travels with each request. Next's server
components decrypt on the Node side, attach the bearer to the FHIR
fetch, parse the Bundle, render HTML.

## 6. Setting up the OAuth client

The agent-service uses password grant; the dashboard uses
authorization-code grant + PKCE. They need separate OpenEMR client
registrations because the redirect URI differs. To register:

```bash
curl -sS -X POST \
  ${OPENEMR_BASE_URL}/oauth2/default/registration \
  -H 'Content-Type: application/json' \
  -d '{
    "application_type": "private",
    "redirect_uris": ["${AUTH_URL}/api/auth/callback/openemr"],
    "client_name": "Patient Dashboard (Next.js)",
    "token_endpoint_auth_method": "client_secret_post",
    "scope": "openid offline_access profile api:fhir user/Patient.read user/Condition.read user/AllergyIntolerance.read user/MedicationRequest.read user/Encounter.read user/CareTeam.read user/Observation.read user/Immunization.read"
  }'
```

The response gives `client_id` + `client_secret` to set as
`OPENEMR_OAUTH_CLIENT_ID` / `OPENEMR_OAUTH_CLIENT_SECRET` in the
Railway service env. The new client must be enabled
(`oauth_clients.is_enabled = 1` or via Administration → System →
API Clients) before sign-in works — same gating we hit while
deploying the agent service.

## 7. What we deliberately did not build

- **Patient search box.** `/patients` lists the first 50; there's no
  search input. The spec is "modernize the dashboard," not "add a
  new search experience." `_filter` against the FHIR API would be
  the natural next step.
- **Edit / write-back.** Read-only. The original dashboard supports
  in-place edits on demographics, problems, etc.; reproducing that
  is a multi-week effort and explicitly outside the W2 spec ("not
  touching the backend"). All seven card data sources are GET-only.
- **Real-time updates.** No WebSocket / SSE. Refresh the page.
- **Mobile-optimized layout.** Tailwind grid is responsive at the
  card-grid level (1 / 2 / 3 columns), but we didn't tune for
  phone screens specifically — the original PHP dashboard isn't
  mobile-optimized either, so we're at parity, not below.

## 8. Trace through the rubric

| Spec requirement | How met |
|---|---|
| OAuth2/OpenID Connect login | `lib/auth.ts` — Auth.js v5 custom provider against `/oauth2/default` |
| Patient header (name, DOB, sex, MRN, active) | `components/PatientHeader.tsx` |
| Allergies | `components/AllergiesCard.tsx` |
| Problem List | `components/ProblemsCard.tsx` |
| Medications | `components/MedicationsCard.tsx` (status=active) |
| Prescriptions | `components/PrescriptionsCard.tsx` (intent=order) |
| Care Team | `components/CareTeamCard.tsx` |
| One optional section | `components/EncountersCard.tsx` (Encounter history, 10 most-recent) |
| Framework defense in markdown | This file |

## 9. Open questions for review

- Do we want to surface `Observation?category=vital-signs` as a
  second optional section? Easy add; the spec only requires one.
- Should the dashboard embed our W1/W2 co-pilot panel via iframe?
  Strict reading of the spec ("feature parity with the original"):
  no, the original dashboard doesn't have a co-pilot. But it would
  be a strong demo moment. Punted on this for the W2 cut.
- Worth wiring the dashboard into the existing eval gate? The
  current gate runs only against `agent-service`. A dashboard
  smoke-test (auth → load `/patient/<uuid>` → assert 6 cards
  rendered) is plausible W3+ work.
