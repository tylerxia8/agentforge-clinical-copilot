# `vulns/` — vulnerability reports

Each file in this directory is a `VULN-XXXX.md` report produced by
the **W3 Documentation Agent** (`agent-service/src/redteam/documentation.py`)
from a confirmed exploit against the deployed W2 Clinical Co-Pilot.

## File naming

- `VULN-XXXX.md` — live report, severity `low` / `medium` / `high`,
  loaded by the W2 regression suite via the matching JSON sidecar at
  `agent-service/evals/w2/adversarial_findings/VULN-XXXX.json`.
- `_pending/VULN-XXXX.md` — **critical**-severity report pending
  human approval. The W2 regression suite does NOT load `_pending/`
  cases until a human reviewer moves the artifact to the live dir.

## How the regression loop works

```
   W3 Red Team attack   →   target response   →   Judge verdict
                                                      │
                          if verdict ∈ {success, partial}
                                                      │
                                              Documentation Agent
                                                      │
                              ┌───────────────────────┴───────────────────────┐
                              v                                               v
                  vulns/VULN-XXXX.md                  agent-service/evals/w2/
                  (human-readable                     adversarial_findings/
                   report)                            VULN-XXXX.json
                                                      (W2 eval-suite sidecar)
                                                                              │
                                                                              v
                                                       W2 eval gate runs on
                                                       every PR; fails if the
                                                       vulnerability is back.
```

## When to delete a report

If a finding turns out to be a false positive (Judge mis-verdicted), delete
**both** files atomically:

```
rm vulns/VULN-XXXX.md agent-service/evals/w2/adversarial_findings/VULN-XXXX.json
```

The W2 regression suite stops enforcing the case the moment the JSON is gone.

The Documentation Agent's duplicate-detection step (see `documentation.py`'s
`find_duplicate`) deduplicates near-identical findings automatically, so
re-running campaigns against the same surface area doesn't proliferate
near-duplicate reports.

## Demo fixture

For an example of what the Documentation Agent's output looks like
without a real exploit involved, see
`agent-service/evals/redteam_runs/_documentation_demo/` — that directory
holds artifacts produced by `python -m redteam.demo_documentation`
against a synthetic input. Those files are explicitly marked as
synthetic demo fixtures and are NOT loaded by the W2 regression suite.
