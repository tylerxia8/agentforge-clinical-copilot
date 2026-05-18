# Reflections — W3 final, what landed, what's next

Two-part doc: the W3 final-grader feedback verbatim (so future
me and any reviewer can read the source of the W4 direction
without losing it to chat history), then a decoded gap + the
concrete W4 work that addresses it.

---

## Grader feedback (W3 final, received 2026-05-16)

> Tyler, this is a strong final submission and one of the better
> examples of connecting orchestration, vulnerability management,
> and reporting into a system that feels operational instead of
> just feature complete. The vulnerability pipeline and reporting
> flow are the strongest parts of the project because they
> resemble a real security workflow with human approval gates,
> reproducible attack chains, remediation guidance, and
> validation after fixes are applied. Covering all six threat
> categories, separating agent responsibilities, and validating
> fixes directly against the deployed copilot helped move this
> beyond a theoretical adversarial platform. The scaling and
> cost discussion was also important because most teams ignored
> operational tradeoffs entirely.
>
> The next thing I would focus on is stronger proof that
> orchestration decisions, replay automation, regression
> handling, and Judge consistency are being driven autonomously
> from observability signals instead of mostly structured
> sequential execution. Overall this is a solid security
> engineering direction with meaningful progress from MVP
> through final.

## Decoding the critique

The praise lands on the right things: pipeline + reporting flow
+ post-fix validation + agent separation + cost discussion. The
critique is one specific shape and worth restating in concrete
terms so I don't mistake it for a vague aesthetic preference:

**Where the W3 platform was at submission:** four loops driven
by *structured sequential execution*.

| Loop | W3 behavior |
|---|---|
| Orchestration | Reads `_summary.json` snapshot at start-of-round. Sonnet picks next category from that frozen view. Decision happens between campaigns, never mid-campaign. |
| Replay | Unbuilt. Existed as a CLI thought (`python -m redteam.replay --attempt <uuid>`) that I punted on for Friday. |
| Regression handling | `adversarial_loader.py` scans the sidecar dir at *test time*, builds W2 cases from whatever files are present. Batch-scan, not event-driven. |
| Judge consistency | Per-attempt deterministic check + LLM verdict. No drift detector — if the LLM Judge's calibration shifts across runs, nothing flags it. |

All four are observability-driven *in spirit* (the data they
read IS observability data) but the pattern is **read-at-fixed-
moments**, not **react-to-live-signals**.

The grader's word "autonomously from observability signals" is
the asymmetry. A platform that's truly observability-driven
should have a *signal subscriber* whose state changes the
moment a signal fires, and a *decision layer* that consults the
subscriber instead of re-deriving state from raw files. Today's
Orchestrator does the latter; the W4 work is the former.

## W4 plan + what's already shipped in this commit

**Choice:** focus W4 narrowly on *one* signal stream wired end-
to-end, rather than touching all four loops at MVP depth. The
grader's word was "stronger proof" — one well-built loop is more
proof than four half-wired ones, and the architectural pattern
that lands on the first loop is reusable for the other three.

**What this commit ships:**

1. **`agent-service/src/redteam/signals.py`** — in-process
   `SignalBus` (pub/sub + JSONL persistence) plus
   `CoverageMonitor` (rolling-window verdict aggregation +
   shift detection). The runner publishes events at four
   canonical points (`campaign.started`, `attempt.recorded`,
   `verdict.delivered`, `campaign.ended`) plus a fifth signal
   the Orchestrator emits about its own decision
   (`orchestrator.decided`, carrying `used_live_signals` +
   `shifts_consulted`).
2. **`agent-service/src/redteam/orchestrator.py`** —
   `OrchestratorContext` gains an optional `live_monitor`. When
   present:
   - The LLM prompt grows a `# Live signal stream` section
     listing shifts since the last decision. The LLM can react
     to "cross_patient just gained traction" verbatim.
   - The deterministic fallback gains a *live-signal override*:
     a positive partial-rate shift on an available category wins
     over the default lowest-attempt-count pick. This is the
     deterministic-path proof that the observability hook
     actually influences the decision.
3. **`agent-service/src/redteam/run_campaign.py`** —
   `--orchestrate` mode now instantiates the bus + monitor at
   run start, subscribes the monitor to the bus, snapshots the
   monitor's state before each Orchestrator round (so the next
   round's `recent_shifts()` compares against that baseline),
   and emits `orchestrator.decided` after each decision with
   `used_live_signals` + the shift list as evidence.
4. **`agent-service/src/copilot/adversarial_visibility.py`** —
   `signals_snapshot()` tails the most-recent run's
   `signals.jsonl` and returns aggregate counts + the
   Orchestrator-decision sub-stream.
5. **`agent-service/src/copilot/main.py`** — new
   `GET /adversarial/signals` JSON route.
6. **`agent-service/src/copilot/static/adversarial.html`** —
   new **Live signal stream** tab with two sub-panels:
   - Orchestrator decisions, with the `used_live_signals` column
     being the *proof* the grader asked for — green-yes/red-no
     on every Orchestrator decision in the run, plus the
     specific shifts each decision consulted.
   - Event timeline tail (last 50 events) refreshing every 8s.
7. **`agent-service/tests/test_redteam_signals.py`** — 14 tests
   covering bus delivery + JSONL persistence + replay,
   `CoverageMonitor` rolling-window math + shift detection
   (positive-shift, sub-threshold-noop, min-attempts filter,
   sort order), Orchestrator integration (deterministic
   fallback picks shifted category, ignores negative shifts,
   prompt-builder includes shift section only when monitor is
   present).

**What this commit does NOT ship** (W4 follow-ups in priority
order):

- **Intra-campaign reaction.** Today the Orchestrator only fires
  between campaigns; a mid-campaign hop mutation that reacts to
  a fresh partial verdict at hop 3 would close the rest of the
  loop. Same SignalBus, hook the runner mid-loop.
- ~~**Judge drift detector.** A second subscriber that watches
  Judge confidence + verdict-class agreement against the
  deterministic check, and flags calibration drift. Same bus,
  different listener — that's the architectural payoff of doing
  the bus first.~~ **Shipped in v2 (below).**
- **Auto-replay on deploy.** A subscriber that, on a
  `deploy.fired` signal (not yet wired), kicks off the
  regression suite against the new target build. The replay CLI
  I punted on becomes the replay *subscriber* in W4.
- **Pub/sub across processes.** Today the bus is in-process; the
  dashboard reads the JSONL log on disk. Redis Streams would let
  the dashboard subscribe live without the file-tail indirection.
  Lowest priority — the current setup is honest about being
  "tail the run log" and the dashboard polling cadence is fine
  for ops use.

## How the proof reads on the dashboard

The Live signal stream tab on `/adversarial` has the
Orchestrator-decisions table with a column `used_live_signals`.
Every Orchestrator decision in a run is one row. When a row
shows **yes** plus a non-empty `shifts_consulted` list, that
row IS the proof — a single Orchestrator decision pointing at
the exact live signals it incorporated. The deterministic
fallback adds a rationale string when the live-signal override
fires (`"Live signal override: <category> just gained traction
(partial_rate +X.XX over +N attempts since last decision)."`).

That's the answer to "stronger proof that orchestration
decisions are driven autonomously from observability signals":
the platform now emits, persists, and surfaces *the link
between signal and decision*, every single time an Orchestrator
round fires.

---

## W4 v2 — JudgeDriftMonitor (second subscriber on the bus)

The architectural payoff of building the bus first was the
claim that **the next observability-driven loop is a new
subscriber, not a new infrastructure layer.** v2 cashes that
claim.

`JudgeDriftMonitor` (in `agent-service/src/redteam/signals.py`)
subscribes to the same `verdict.delivered` events the
`CoverageMonitor` already reads, but does a different
observation: rolling-window mean LLM-Judge confidence per
category, with drift detection against a baseline pinned on the
same cadence as the Orchestrator's coverage snapshot.

Two design choices worth flagging:

1. **Exclude deterministic-shortcut verdicts.** The deterministic
   path in `judge.py` returns `confidence == 1.0` exactly. If
   the monitor counted those, a steady stream of deterministic
   decisions would peg the rolling mean at 1.0 regardless of
   how Haiku's calibration drifts on attempts that DO reach the
   LLM. The monitor filters by `confidence < 0.999` so only LLM-
   decided verdicts feed the drift math. The W3-final Judge bug
   the platform found in itself (six false-positive critical
   verdicts → trust gate caught them all → deterministic check
   added) is the reason this distinction matters: deterministic
   decisions are the *good* ones, and they should be invisible
   to the drift detector that's watching the LLM.
2. **Drift = mean of new-since-baseline samples vs baseline
   mean.** The naive version (cumulative_mean − baseline_mean)
   undercounts drift because the baseline samples are still in
   the window dampening the average. The correct math is "the
   mean of just the samples received since baseline" vs "the
   mean at baseline." This came out of a failing test
   (`test_drift_monitor_sort_by_magnitude`) that exposed the
   weighted-average dampening immediately — a small but
   load-bearing semantic fix.

**Surfaces:**

- New `judge_drift_snapshot()` in
  `agent-service/src/copilot/adversarial_visibility.py` —
  replays the most-recent run's `signals.jsonl` through a fresh
  `JudgeDriftMonitor` and returns both the current per-category
  rolling stats and any drift signals that fired between rounds.
- New `GET /adversarial/judge-drift` JSON route.
- New **Judge drift** card on the Live signal stream tab:
  per-category mean-confidence table + drift-signals table.
  Polled at the same 8s cadence as the rest of the live stream.

**Tests (in `tests/test_redteam_signals.py`):** 9 new — rolling-
confidence aggregation, deterministic-shortcut exclusion, no-
signal-before-baseline, decrease-detection, increase-detection,
sub-threshold-noop, min-new-samples filter, min-baseline-samples
filter, magnitude-sort ordering. 69/69 redteam tests passing.

**What this still does NOT ship** (W4 v3+ candidates):

- **Intra-campaign reaction.** Same as v1's deferred list.
- **Auto-replay on deploy.** Same as v1's deferred list.
- **Pub/sub across processes.** Same as v1's deferred list.
- **Baseline persistence across runs.** Today the drift
  baseline lives in-memory during one run; the dashboard
  replays the run's JSONL to reconstruct it. For meaningful
  cross-run drift detection (Haiku's calibration shifting over
  weeks, not within a single 15-attempt run), the baseline
  needs to live on disk in a known location and the
  `JudgeDriftMonitor` needs a `seed_baseline_from_disk()`
  method. Worth doing once there are enough runs accumulated
  for the cross-run comparison to be statistically meaningful.
