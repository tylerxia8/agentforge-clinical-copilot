# Social media posts (Final submission)

Two drafts — pick one or post both. Required for Final per the case
study: "Share on X or LinkedIn: describe the project, show the agent,
tag @GauntletAI."

---

## X (Twitter) — ~280 chars

> Just shipped AgentForge Clinical Co-Pilot for @GauntletAI: an AI
> agent embedded in OpenEMR that answers clinical questions about
> the open chart in ~6s, cites every claim back to a FHIR row, and
> refuses cross-patient queries by code path — not by prompt.
>
> 6/6 integration evals pass.
>
> Live: copilot-agent-production-ba87.up.railway.app
> Code: github.com/tylerxia8/agentforge-clinical-copilot

**Attach:** a 30-second screen capture of the chat UI doing one happy
turn + one cross-patient refusal. Loom-export the relevant clip from
the Thursday demo video.

---

## LinkedIn — long-form

> **AgentForge Clinical Co-Pilot — built in 7 days for @GauntletAI's
> Austin admission track.**
>
> One-line product: an AI agent embedded in OpenEMR (the open-source
> EHR) that gives a primary care physician what they need to know
> about the patient in front of them — in seconds, with every claim
> cited to a real row in the patient's chart.
>
> ⏱️ Per-turn latency: ~6 seconds, end-to-end, against live FHIR data.
>
> 🛡️ Three load-bearing safety properties, each tested as code:
>
> 1. **Patient-context boundary.** Every tool call carries the open
>    chart's UUID. The middleware refuses cross-patient queries,
>    even under "ignore previous instructions" prompt injection. This
>    is a code path, not a prompt instruction — that distinction is
>    the entire reason it holds.
>
> 2. **Citation contract.** Every clinical claim must inline-cite a
>    FHIR row id. Citations are validated against the actual rows the
>    tools returned this turn. Invented citations are rejected
>    automatically; the agent falls back to a verified-facts-only
>    response.
>
> 3. **Refuse before fabricating.** On an empty chart the agent says
>    "no medications on file" — it does not invent. Six integration
>    eval cases verify this against the live deployment.
>
> 🏗️ Architecture choices, each tied to an audit finding:
>
> · OpenEMR has a real ACL system but doesn't filter at the API layer
>   by patient ownership. The agent layers a per-turn patient-context
>   middleware on top — this is the closure for the audit's biggest
>   finding.
> · PHI is tokenized before reaching the LLM. Names / MRNs / DOB
>   become placeholders. The token map lives in request scope and
>   never reaches Anthropic.
> · Patient data bundles cache on chart-open in Redis. First chat
>   turn reads hot data — six seconds, not twenty.
> · Per-physician cost stays in a $21-28/month band across four
>   orders of magnitude scale — the LLM call dominates and scales
>   linearly. Full breakdown in COSTS.md.
>
> 📊 Live demo: https://copilot-agent-production-ba87.up.railway.app/
> 📁 Code: https://github.com/tylerxia8/agentforge-clinical-copilot
> 📓 Architecture, audit, eval results all in the repo.
>
> Built solo in 7 days as part of the @GauntletAI Austin admission
> sprint. Thanks to the Gauntlet team for a project brief that
> insists on the right things — verification, observability, eval —
> from day one.
>
> #ClinicalAI #OpenEMR #FHIR #AnthropicClaude #AgentForge

**Attach:** the full Thursday demo video (3-5 min) embedded.

---

## What to actually post

The X post is the one most people will see; LinkedIn is for the
hospital CTOs / health-tech investors who'd evaluate this for real.

Recommended order:
1. Post on LinkedIn first with the full video and longer write-up.
2. Quote-tweet your own LinkedIn link from X with the short version.
3. Tag @GauntletAI on both.

## Pre-publish checklist

- [ ] Demo video URL is public (Loom: Settings → Public; YouTube:
      Unlisted is fine for the case study)
- [ ] Both Railway URLs render in an incognito browser (no auth wall)
- [ ] GitHub repo is public (Settings → Visibility)
- [ ] AGENT_SHARED_SECRET, ANTHROPIC_API_KEY, etc are NOT in the
      repo (they're Railway env vars only — verify with
      `git log --all -p | grep -i "sk-ant\|sk-lf"` returns nothing)
- [ ] @GauntletAI handle correct on the platform you post to
