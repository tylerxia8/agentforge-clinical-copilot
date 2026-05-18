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
- ~~**Auto-replay on deploy.**~~ **Shipped in v3 (below).**
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

---

## W4 v3 — Replay-on-deploy (third subscriber on the bus)

Closes the third W3-final-grader bullet ("replay automation").
Same pattern as v1 + v2: a new event type, a new subscriber, a
new dashboard surface. The architectural payoff of the bus is
now demonstrated three times — orchestration (v1), Judge
consistency (v2), replay automation (v3).

**Mechanism.** A `deploy.fired` signal triggers the
`ReplaySubscriber` (`agent-service/src/redteam/replay.py`)
which loads every confirmed regression case from
`agent-service/evals/w2/adversarial_findings/`, fires each one
against the just-deployed target via the same transport the W2
eval suite uses (`evals.w2.transport.chat`), grades each
response against its sidecar's rubrics, and emits four event
types end-to-end:

- `deploy.fired` — the trigger (carries `target_url`,
  `image_digest`, `trigger`)
- `replay.started` — case count + include-pending flag
- `replay.case.evaluated` — per-case `passed: bool` +
  `rubric_results: {rubric_name: bool}` + optional `error`
  string for cases that raised mid-chat
- `replay.completed` — summary stats (case_count, passed_count,
  failed_count, error_count, elapsed_seconds)

**Trigger surface.** Today's stand-in is
`POST /adversarial/admin/replay`, gated by the
`REPLAY_ADMIN_TOKEN` env var (the endpoint returns 503 if the
env is unset, refusing to run without any auth at all). The
endpoint constructs a fresh per-request `SignalBus` writing to
`agent-service/evals/replay_runs/<timestamp>/signals.jsonl`,
publishes `deploy.fired`, awaits the subscriber's replay task,
and returns the run dir name. The replay is bounded by the
existing target — there's no looping or fanout.

**Why per-request bus, not lifespan-managed.** The first
design had a long-lived runtime bus instantiated at FastAPI
startup. A per-request bus turned out cleaner: each replay
gets its own dedicated `signals.jsonl` exactly like the
runner's per-campaign log, the dashboard's "most-recent
run" scan logic applies symmetrically to both, and there's no
state to clean up between replays. The trade-off is that a
multi-source future (Railway webhook + manual admin endpoint
fanning into one subscriber) would need a different pattern,
but that's W4 v4+ work.

**Why reuse `evals.w2.transport.chat`.** "Replay" should not
mean "a second, parallel testing path that drifts away from
the real eval gate." It should mean "the same eval suite,
fired from a signal instead of from CI." Reusing the W2
transport guarantees the replay exercises the same HMAC auth,
same `/agent/chat` endpoint, same parse path the regression
gate uses on every PR.

**Surfaces:**

- `agent-service/src/redteam/replay.py` — `ReplayCase`,
  `load_replay_cases(include_pending=False)`,
  `_grade_response()`, `run_replay()`, `ReplaySubscriber`
- New event types + emit helpers in `signals.py`
- `POST /adversarial/admin/replay` — admin-token-gated trigger
- `GET /adversarial/replay` — JSON view of the most-recent
  replay (deploy_fired metadata + replay_started + per-case
  results + replay_completed summary)
- **Replay-on-deploy** card on the Live signal stream tab:
  summary row (trigger + target + case count + result pill +
  elapsed) plus per-case table (case_id, vuln_id,
  live/pending status, pass/fail pill, rubric-result pills)

**Tests (in `tests/test_redteam_replay.py`):** 12 new — case
loading (live-only default, include-pending opt-in, skip
unsupported `kind`), rubric grading (safe_refusal pass,
unknown-rubric-fails-closed, response_length_bounded both
sides), full emission sequence (started + one per-case +
completed), failure + error counting, summary-matches-per-case
invariant, subscriber-ignores-non-deploy, subscriber-schedules-
on-deploy-fired (full async path), subscriber-skips-cleanly-
without-event-loop. **81/81 redteam tests passing** across all
W4 subscribers + the existing W3 suite.

**Open subscriber slots after v3** (W4 v4+ candidates):

- **Real Railway webhook trigger.** Today the admin endpoint
  IS the trigger. Wire Railway's deploy-complete webhook to
  POST to `/adversarial/admin/replay` so the autonomy loop
  closes: a deploy *causes* a replay without operator
  intervention. The replay subscriber itself doesn't need
  changes; only the trigger source does.
- ~~**Auto-promote-on-green.**~~ **Shipped in v4 (below).**
- **Intra-campaign reaction.** Same as v1's deferred list.
- **Pub/sub across processes.** Same as v1's deferred list.
- **Baseline persistence across runs** (for the JudgeDrift
  Monitor). Same as v2's deferred list.

---

## W4 v4 — AutoPromoteSubscriber (closing the fourth grader bullet)

The W3-final grader called out four loops to convert from
structured-sequential to observability-driven: orchestration,
Judge consistency, replay automation, and regression handling.
v1–v3 closed the first three. v4 closes the fourth.

**Mechanism.** A new `AutoPromoteSubscriber` (in
`agent-service/src/redteam/auto_promote.py`) subscribes to
`replay.case.evaluated` and `replay.completed` events. It
accumulates per-case events in an in-memory buffer keyed by
`replay_id`. When the matching `replay.completed` event fires,
the subscriber:

1. Drops the buffer for that replay if the run wasn't fully
   green (`failed_count > 0` OR `error_count > 0`). One bad
   case anywhere in the run suppresses every promotion — the
   operator's manual review becomes the failure mode rather
   than silent half-promotion.
2. For each accumulated case with `passed=True` and
   `is_pending=True`, reads the severity from the sidecar JSON
   and applies the trust-gate rule: `severity == "critical"`
   always stays pending, regardless of replay outcome. This
   preserves the architecture's original intent that
   critical-severity findings require human review.
3. For the surviving cases, moves the sidecar JSON from
   `evals/w2/adversarial_findings/_pending/<VULN>.json` to the
   live dir, and moves the markdown report from
   `vulns/_pending/<VULN>.md` to live. Either artifact may be
   missing; the subscriber moves whichever exists and reports
   what was actually moved in the `finding.promoted` event
   payload.
4. Emits `finding.promoted` per move and
   `finding.promotion_skipped` per consideration that didn't
   promote (with a reason: critical-severity, disabled, no
   pending artifacts, non-green run).

**Opt-in by default.** The `AutoPromoteSubscriber` constructor
takes `enabled: bool = False`. The
`/adversarial/admin/replay` endpoint exposes `auto_promote:
bool` in the request body (also default false). When
`enabled=False`, the subscriber still observes events and
emits `finding.promotion_skipped` events ("disabled; would
have promoted on green replay") so the dashboard shows what
*would have been promoted* — useful for an operator deciding
whether to flip the flag on a future run.

**Why severity comes from the sidecar JSON.** The replay event
shape doesn't carry severity (the event is about the chat
turn's pass/fail, not the finding's metadata). The subscriber
reads severity directly from the sidecar at promotion time —
single source of truth, no caching, no risk of stale severity
from a since-edited sidecar.

**Surfaces:**

- `agent-service/src/redteam/auto_promote.py` —
  `AutoPromoteSubscriber`, `_promote_one_finding`,
  `_read_severity`
- New event types + emit helpers in `signals.py`:
  `finding.promoted`, `finding.promotion_skipped`
- `auto_promote` flag added to the
  `POST /adversarial/admin/replay` request body
- `GET /adversarial/auto-promotions` JSON view
- **Auto-promotions** card on the Live signal stream tab —
  two tables: actual promotions (timestamp, vuln_id, severity,
  sidecar move path, markdown move path) and skipped
  considerations (timestamp, vuln_id, severity, reason)

**Tests (in `tests/test_redteam_auto_promote.py`):** 8 new —
green-replay-promotes-high, critical-stays-pending,
failed-replay-promotes-nothing, errored-replay-promotes-nothing,
live-cases-silently-ignored, disabled-mode-emits-skip-but-no-
move, idempotency (re-run after promotion emits a skip rather
than crashing), multi-case-routing (critical + high + already-
live in one replay end up in the right buckets). **90/90 redteam
tests passing** across all four W4 versions + W3.

**What this does NOT ship** (W4 v5+ candidates):

- **Reversal on later failure.** Today a promoted finding stays
  live forever. If a future deploy regresses and the case fails,
  we emit a per-case-failed event but don't auto-demote. A
  symmetric `AutoDemoteSubscriber` would close the round trip.
- **Persistent dedup of replay-ids.** Re-running the same
  replay would re-evaluate the same cases. The promotion is
  idempotent at the filesystem level (move-or-skip-if-already-
  live), but emits the per-event signal each time. For
  low-frequency operator-triggered runs this is fine; a
  high-frequency webhook future would want a `replay_id` dedup
  table.
- **Same v1-v3 deferred items** still apply (Railway webhook,
  intra-campaign reaction, cross-process pub/sub, cross-run
  baseline persistence for the JudgeDriftMonitor).

## Status of the W3-final-grader bullets after v4

| Bullet | Status |
|---|---|
| Orchestration decisions driven by observability signals | ✅ v1 — `CoverageMonitor` |
| Judge consistency | ✅ v2 — `JudgeDriftMonitor` |
| Replay automation | ✅ v3 — `ReplaySubscriber` |
| Regression handling | ✅ v4 — `AutoPromoteSubscriber` |

The architectural payoff of building the bus first is now
demonstrated four times across four independent observation
loops. Each subscriber is ~50–200 lines of standalone Python
that publishes typed events onto the same in-process bus — no
new infrastructure layers added since v1.
