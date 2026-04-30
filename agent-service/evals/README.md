# Integration evals

Pytest-style cases that fire real `/agent/chat` turns at the deployed
agent service and grade the responses. Stdlib only — no extra Python
dependencies.

## What's tested

| Case | Property under test |
|------|---------------------|
| `happy.farrah_active_meds` | Agent surfaces real chart data with valid citations (UC-1) |
| `empty.ted_no_meds` | Agent doesn't fabricate when there's nothing on file |
| `empty.eduardo_no_meds` | Same property, different patient (sanity dup) |
| `adversarial.cross_patient_query` | Chart-boundary middleware refuses cross-patient leakage |
| `adversarial.prompt_injection` | "Ignore previous instructions" doesn't bypass the chart boundary |
| `invariant.no_invented_citations` | Every citation the agent emits references a real row |

The cases are intentionally narrow — six cases exercise the
load-bearing properties of the audit→architecture chain:

- AUDIT.md §1.2 (no patient-level ACL) → patient-context boundary →
  the cross-patient and prompt-injection cases.
- ARCHITECTURE.md §4.1 (citation contract) → the citation-validity
  invariant.
- USERS.md UC-1 (pre-visit snapshot) → the happy path.
- ARCHITECTURE.md §7 ("refuse before fabricating") → the empty-chart
  cases.

## Run it

```bash
cd agent-service/evals

export AGENT_URL=https://copilot-agent-production-ba87.up.railway.app
export AGENT_SHARED_SECRET=<same value the deployed service is running with>

python run.py
# or via Docker if no local Python:
# docker run --rm -i -e AGENT_URL -e AGENT_SHARED_SECRET \\
#   -v "$PWD":/work -w /work python:3.12-slim python run.py
```

Exit code is 0 if all cases pass, non-zero otherwise. Stdout is a
markdown results table you can paste into the demo video / submission.

## Limitations (deliberate, for v1)

- Cases hit the live deployment, not a local agent. CI integration
  is a v2 concern.
- No LLM-as-judge grader yet — graders are deterministic regex /
  membership checks. That's enough for the boundary properties; the
  judge call is a v2 refinement (see ARCHITECTURE.md §4.2).
- Six cases. The architecture roadmap targets ~80 with Synthea data
  for Sunday final.

## Adding a case

1. Pick the property you want to defend.
2. Find or seed the data on the deployed OpenEMR.
3. Add a `Case(...)` to `CASES` and a `grade_*` callable.
4. Re-run.
