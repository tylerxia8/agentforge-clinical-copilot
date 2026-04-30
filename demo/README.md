# Demo pre-staging

Five scripts that fire real chat turns at the deployed agent. Each
one is a single beat in [../DEMO_SCRIPT.md](../DEMO_SCRIPT.md). Run
them on camera in order — output is colored, formatted, and quick.

## Setup (do once before recording)

```bash
export AGENT_SHARED_SECRET=<the deployed agent's HMAC secret>
# (Optional — defaults to the production Railway URL)
# export AGENT_URL=https://copilot-agent-production-ba87.up.railway.app

# Make scripts executable (Linux/Mac; Git Bash on Windows ignores it)
chmod +x demo/*.sh

# Sanity check before recording — token mints, fetches, no errors:
./demo/1-happy.sh
```

If the happy-path test prints a clean response with two
`[MedicationRequest#…]` citations, you're ready to record.

## On camera

In recording order, matching the script beats:

| Order | File | Beat in DEMO_SCRIPT.md | Expected outcome |
|-------|------|------------------------|------------------|
| 1 | `1-happy.sh` | 0:00–0:25 cold open | Lisinopril + Atorvastatin, 2 citations, ~6s |
| 2 | `2-empty.sh` | 0:25–0:45 (refusals) | "no active medications", `sources: []` |
| 3 | `3-cross-patient.sh` | 0:45–1:05 (refusals) | refuses or pivots; 0 cross-patient sources |
| 4 | `4-injection.sh` | 1:05–1:25 (refusals) | refuses; chart boundary holds |
| 5 | `5-evals.sh` | 1:25–2:10 (eval results) | 6/6 pass markdown table |

Each chat-turn script (1–4) takes ~5–13 seconds. Eval suite (5) takes
~50 seconds (6 cases sequentially). Pre-roll the recording at the
moment you hit Enter on each script to keep dead air out.

## Why these are scripts, not raw curls

1. Token mint is non-trivial (HMAC + base64url + JSON ordering must
   match the Python verifier). Easier to ship a known-good helper
   than retype the Node one-liner each take.
2. Pretty output for the camera. Raw JSON is hard to read at 1080p.
3. Re-runnable for re-takes without scrolling terminal history.

## Troubleshooting

- **`✘ AGENT_SHARED_SECRET is not set`** — `export` it before running.
- **`401 missing bearer token`** — the FastAPI Header() injection bug
  from earlier; should not happen on the current deploy. Re-deploy if
  it does.
- **`401 invalid token`** — secret mismatch. Confirm the env var
  matches what's set on the `copilot-agent` Railway service.
- **`502 Bad Gateway`** — Railway is restarting the container. Wait
  10 seconds and retry.
- **30+ second response time** — Anthropic rate-limit retry, or the
  model decided to do extra tool rounds. Cut and re-fire if recording.
