# `agent-service/evals/w2/adversarial_findings/` — W3 → W2 regression sidecar

This directory is the **regression harness wiring** that turns
W3-discovered vulnerabilities into permanent W2 eval-suite cases.

The PRD requires the platform to "convert confirmed exploits into
deterministic, repeatable test cases and run them against every new
version of the target system." This directory is how we satisfy
that requirement without mutating the human-authored
`agent-service/evals/w2/cases.py`.

## Why a sidecar dir instead of editing `cases.py`?

Three reasons (see ARCHITECTURE.md §"Documentation Agent"):

1. **Safety.** The W3 Documentation Agent writes these files
   autonomously. A bad finding can be deleted with `rm` rather than
   requiring a Python source edit.
2. **Reversibility.** Toggle a regression case on or off without a
   commit just by moving the JSON file in/out.
3. **Trust gates.** Critical-severity findings land in `_pending/`
   pending human approval. The W2 eval-suite loader (Friday-final
   wiring) reads the live dir, not `_pending/`.

## File format

Each `VULN-XXXX.json` matches this shape:

```json
{
  "case_id": "adversarial_vuln_0001",
  "category": "adversarial" | "boundary",
  "vuln_id": "VULN-XXXX",
  "description": "Adversarial regression case auto-promoted from ...",
  "fire": {
    "kind": "chat",
    "patient_uuid": "<demo-patient-uuid>",
    "message": "<the attack prompt>"
  },
  "rubrics": ["safe_refusal", "no_phi_in_logs"],
  "must_not_mention": ["...", "..."],
  "severity": "low" | "medium" | "high" | "critical",
  "discovered_at": "<iso-timestamp>"
}
```

The `must_not_mention` array is derived from the threat category
(cross-patient cases must not name foreign patients; indirect-
injection cases must not echo payload acknowledgment markers).

## Live vs `_pending/`

- Top-level `*.json` — live regression cases. Loaded by the W2 eval
  suite.
- `_pending/*.json` — critical-severity cases awaiting human
  approval. NOT loaded.

To approve a pending case:

```
mv adversarial_findings/_pending/VULN-XXXX.json adversarial_findings/
mv vulns/_pending/VULN-XXXX.md vulns/
```

Then commit. The next W2 eval run will exercise the new case.

## Pairing with `vulns/`

Every JSON here has a matching `VULN-XXXX.md` in `vulns/` at the
repo root. The pair represents one vulnerability — one is for
humans, one is for the eval gate.

## Loader (Friday-final wiring)

The W2 eval-suite loader that reads this directory is the last
piece of the regression-harness wiring. It scans for
`VULN-*.json`, constructs a `W2Case` per file, and yields them
alongside the hand-authored cases in `cases.py`. Until that loader
is wired into the eval-gate workflow, this directory is the
forward-compatible storage; the gate enforces the human-authored
suite only.
