# W3 social post — draft

> PRD-required deliverable: "Social Post (Final only) — Share on
> X or LinkedIn: describe the project, show the platform in
> action, tag @GauntletAI."

Three drafts, pick one or remix. The story to communicate: built
a four-agent system that autonomously attacks an AI clinical
assistant, found bugs in its own Judge, the trust gate caught
them before they polluted the regression suite, and the findings
plug back into the W2 eval gate as permanent regression guards.

---

## Version A — X / Twitter (280-char-aware, threadable)

**Tweet 1 (top of thread, ~270 chars):**

> Built an adversarial AI security platform that hunts vulnerabilities
> in clinical-AI systems autonomously.
>
> Four agents, distinct trust levels: an Orchestrator that picks what
> to attack, a Red Team that generates attacks, an independent Judge,
> and a Documentation Agent that files vuln reports.
>
> Week 3 of @GauntletAI Austin admission. 🧵

**Tweet 2 (~270 chars):**

> The cool part isn't finding vulns. It's the trust gate catching
> the Judge being wrong about its own verdicts.
>
> Twice during this week's runs, the Judge LLM produced
> critical-severity "success" verdicts on responses that were actually
> clean refusals. The architecture's _pending/ trust gate auto-routed
> every one for human approval before it polluted the regression suite.

**Tweet 3 (~270 chars):**

> Fixed both Judge bugs structurally — a universal deterministic
> check that runs BEFORE the LLM Judge and skips it when
> target.refused=True + no foreign-UUID + no injection marker.
>
> Same class of false positive can't happen again. The platform
> validated its own Judge.

**Tweet 4 (closing, ~270 chars):**

> Architecture: LangGraph + Langfuse, Sonnet for strategic + generative
> roles, Haiku for judging + documentation. Findings auto-promote into
> the W2 eval gate as permanent regression guards.
>
> Code + 92 attempts of run logs:
> github.com/tylerxia8/agentforge-clinical-copilot

---

## Version B — LinkedIn (single post, longer-form)

**Title / first line (grab):**

> What if your AI security platform could find bugs in its own Judge?

**Body (~3000 chars, line-broken for LinkedIn rendering):**

> This week I built an autonomous adversarial AI security platform —
> a four-agent system that hunts vulnerabilities in clinical-AI
> systems while a human sleeps.
>
> The hard problem isn't generating attacks. It's deciding whether
> an attack succeeded — and trusting that decision enough to
> auto-file vulnerability reports and add regression cases that
> block future PRs. The PRD for this sprint (Gauntlet AI Austin
> admission, Week 3) was explicit: a single-agent or pipeline
> architecture doesn't count. You need distinct agents with
> distinct responsibilities and trust levels, because an agent
> that both generates AND judges its own attacks has a
> conflict of interest by design.
>
> Four agents:
>
> 🔵 **Orchestrator** (Sonnet 4.6) reads coverage state across
> prior runs and decides what to attack next. Picks campaigns
> to maximize information gain per dollar. Has a deterministic
> fallback when the LLM call fails.
>
> 🔴 **Red Team Agent** (Sonnet 4.6 with an "authorized
> security researcher under signed BAA" framing — RLHF-permitted,
> not a jailbreak) generates the attacks. Two modes: generate
> from category seed, or mutate a partially-successful prior
> attempt. Multi-turn attack support including synthesized
> conversation history.
>
> ⚖️ **Judge** (Haiku 4.5, deliberately a different model from
> the Red Team to break correlation) evaluates each attempt
> against a category-specific rubric. Deterministic-signal
> overrides where possible: cross-patient UUID match is a
> string compare, not an LLM judgment. LLM only fires for
> genuinely ambiguous cases.
>
> 📝 **Documentation Agent** (Haiku 4.5) converts confirmed
> exploits into PRD-format vulnerability reports + W2 eval-case
> sidecars. Critical-severity findings route to _pending/ for
> human approval before going live.
>
> The unexpectedly satisfying part: the platform found bugs in
> its own Judge twice during the week. Both times the trust
> gate caught it — the Documentation Agent's severity-based
> routing put the false-positive findings into _pending/ where
> they never reached the live regression suite. I fixed the
> bugs structurally (a universal deterministic check that runs
> before the LLM Judge and skips it for clean-refusal cases),
> and the same class of bug can't recur.
>
> 92 attempts run against the live target (the Clinical Co-Pilot
> I built in Weeks 1 and 2 of this same admission sprint). Total
> dev spend: about $15. The findings that ARE real are at the
> input-validation and infrastructure layer where LLM-level
> probes can't reach: rate limiting, history validation, demo
> endpoint authorization. Each documented in vulns/ with PRD-
> format reports.
>
> Confirmed exploits auto-promote into the same W2 eval gate
> that protected last week's submission — meaning every PR
> going forward is guarded against regressing the W3 findings.
> The W3 platform's contribution isn't finding one exploit; it
> turns every confirmed exploit into a permanent regression
> case the existing CI gate enforces.
>
> Code + threat model + architecture + cost analysis + 92
> attempts of run logs + three vulnerability reports:
> github.com/tylerxia8/agentforge-clinical-copilot
>
> @GauntletAI — Week 3 of the Austin admission track. Built on
> Anthropic Claude + LangGraph + Langfuse, deployed on Railway.
>
> #AISafety #AIRedTeam #LLMSecurity #ClinicalAI

---

## Version C — short and punchy (for either platform)

> Spent the week building an autonomous adversarial AI security
> platform — four agents that hunt vulnerabilities in clinical-AI
> systems without a human in the loop.
>
> The platform found bugs in its own Judge twice.
>
> The trust gate auto-routed both to _pending/ for human approval
> before they polluted the regression suite. Fixed structurally
> with a universal deterministic check; same false-positive shape
> can't recur.
>
> 92 attempts × $0.15/attempt average × 6 attack categories,
> all in repo:
> github.com/tylerxia8/agentforge-clinical-copilot
>
> @GauntletAI Week 3.

---

## What to attach (when posting)

For maximum impact, include 1-2 images:

1. **Architecture diagram screenshot** from ARCHITECTURE.md's
   ASCII diagram (cropped, may not render well outside
   monospace contexts)
2. **The trust-gate evidence screenshot**: `vulns/_pending/`
   directory listing showing 8 auto-gated findings + the
   _pending/README.md scrolled to "Current state" — the
   smoking gun for the "platform validates its own Judge"
   claim.
3. **A terminal screenshot** showing the
   20260512T030840Z orchestrator-mode run with the
   Orchestrator's verbatim "state_corruption has the only
   active partial verdict ..." rationale visible.

Don't post a screenshot of a vulnerability report's content
without making clear it's against a target system the poster
owns — the BAA framing in the Red Team prompt should be
visible if security people scroll the code.

## When to post

After the demo video is uploaded and the URL is in the README.
The social post should link to the repo, and ideally the demo
video too (Loom / YouTube unlisted).

## Verifying tag

@GauntletAI — confirm the handle on the platform you post to
(X = @GauntletAI / Twitter; LinkedIn may be /company/gauntlet-ai
or similar). Double-check before posting.
