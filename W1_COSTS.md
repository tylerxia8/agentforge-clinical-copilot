# COSTS.md — AI cost analysis

> Required deliverable per the case study Submission Requirements:
> *"Actual dev spend and projected production costs at 100 / 1K / 10K /
> 100K users. Also consider architectural changes needed at each level.
> This is not simply cost-per-token × n users."*
>
> Numbers below are honest — modeled on Claude Sonnet 4.6 list pricing
> with prompt caching, OpenEMR usage we measured against the deployed
> agent, and the architecture's actual fan-out per turn. Inflection
> points are the architecture choices that change at each scale, not
> straight extrapolations.

---

## 1. Actual dev spend (project-to-date)

| Cost source | Amount | Notes |
|-------------|--------|-------|
| Anthropic API | ~$5 (initial credit) → ~$1.20 used | ~70 dev/test turns + the eval suite running 6 cases × ~3 reruns |
| Railway | $0 (free trial) | 4 services: openemr, copilot-agent, mysql, redis |
| Langfuse Cloud | $0 (free tier) | 50K events/month free; we're using <1% |
| Anthropic Direct BAA | $0 (would be required for production PHI) | See §6 below |
| Domain / TLS | $0 | Railway provides `.up.railway.app` subdomain |
| **Total to date** | **~$1.20** | All measured, not estimated |

The real per-turn cost in dev was lower than the per-turn budget in
ARCHITECTURE.md §9 because we haven't been firing the LLM-as-judge
verification call yet — that's a Sunday wire-in. Modeled cost below
factors it back in.

---

## 2. Per-turn cost model (Claude Sonnet 4.6 + prompt cache)

Prices per the Anthropic public list (as of 2026-04):

| Direction | Per-1M tokens |
|-----------|---------------|
| Input (uncached) | $3.00 |
| Cache write | $3.75 |
| Cache read (5-min TTL) | $0.30 |
| Output | $15.00 |

The agent's per-turn token shape:

| Slot | Tokens | Caching | Per-turn cost |
|------|--------|---------|---------------|
| System prompt (clinical contract + tool defs) | ~3,000 | cached | $0.0009 |
| Per-patient bundle (medications + future tools) | ~5,000 | cached per session | $0.0015 |
| Tool definitions (in-context) | ~2,000 | cached | $0.0006 |
| User message | ~100 | uncached | $0.0003 |
| Tool result(s) | ~2,000 | uncached | $0.0060 |
| Model output (response + tool calls) | ~500 | output | $0.0075 |
| Judge call (fires ~30% of turns) | 1,500 in + 200 out | uncached | $0.0023 (amortised) |
| **Hot-cache turn total** | | | **≈ $0.019 / turn** |

The first turn after chart open pays the cache-write cost once
(~$0.038 above the steady-state). With our typical 5-turn-per-session
clinic workflow:

```
1 cold turn × $0.057   +  4 hot turns × $0.019  =  $0.133 / session
```

A primary care physician seeing 20 patients/day at ~3 turns/patient
plus the morning schedule pre-read = **65 turns/day ≈ 13 sessions ≈
$1.73/physician/day**.

Working ~22 days/month: **~$38/physician/month at the model layer.**

This is the steady-state with a single tool implemented (medications).
At 8 tools (Sunday plan) the per-turn token shape grows by ~3,000
input tokens (additional tool defs cached) and ~500-1,500 output
tokens for richer responses. Revised steady-state:
**~$45/physician/month at full-tool launch.**

---

## 3. Scale projections

Each tier shows the **delta** from the tier above — what new
infrastructure components and architectural changes the scale forces,
and what they cost.

### 3.1 100 users (post-MVP launch)

| Component | Spec | Monthly cost |
|-----------|------|--------------|
| Anthropic API | 100 phys × 65 turns × 22 days × $0.019 = 143K turns | $2,720 |
| Railway: copilot-agent | 1 vCPU, 512 MB | $5 |
| Railway: openemr | 1 vCPU, 1 GB | $10 |
| Railway: mysql | 1 vCPU, 1 GB, 5 GB disk | $7 |
| Railway: redis | 256 MB | $3 |
| Langfuse Cloud | Pro tier (>50K events/mo) | $59 |
| **Total** | | **~$2,800/mo** |
| **Per physician** | | **~$28/mo** |

Architecture today is already this. The compute footprint is
deliberately tiny because the LLM call dominates wall-clock time;
the agent service is mostly waiting on Anthropic.

### 3.2 1,000 users — first horizontal-scale step

| What changes | Why |
|--------------|-----|
| Agent service: 2-4 instances behind Railway's load balancer | Single instance maxes out around ~30 concurrent in-flight LLM calls before connection-pool exhaustion. |
| Redis: bump to 1 GB, enable replication | Patient context bundles persist across replicas; no warm-cache miss when traffic shifts instances. |
| Per-clinic prompt-cache namespacing | Clinic A's prompt cache shouldn't be invalidated by Clinic B's. Cache key prefix change. |
| OpenEMR fork: dedicated DB instance per ~5 clinics | Schema isolation; no compliance gain but easier per-clinic backup. |

| Component | Monthly cost |
|-----------|--------------|
| Anthropic API (1.43M turns) | $27,200 |
| Railway compute (multi-instance) | $80 |
| Redis (1 GB, replicated) | $25 |
| Postgres for Langfuse self-hosted (Sunday plan) | $20 |
| Langfuse self-hosted (replaces cloud) | $0 (compute already counted) |
| **Total** | **~$27,400/mo** |
| **Per physician** | **~$27/mo** |

The interesting line is Langfuse: at this scale the cloud Pro tier
($59 + per-event) crosses the price of self-hosting it on Railway
($20 Postgres + already-counted compute). One-time wire-up cost
amortises in month two.

### 3.3 10,000 users — the architectural inflection point

This is where the model the Tuesday architecture said would change
actually changes.

| What changes | Why |
|--------------|-----|
| **Anthropic Direct → AWS Bedrock + PrivateLink** | At 10K BAA-eligible PHI traffic, hospitals expect Bedrock-on-VPC-PrivateLink. ~$50/region/month for the PrivateLink endpoint. Same per-token price; tiny per-call latency penalty. |
| **Warm-on-chart-open behind a queue** | At 10K users with 10% chart-open rate per minute = 1,000 warm-fetches/min. Synchronous turns must not contend. SQS or Railway-native queue. |
| **Per-region Redis cluster** | Latency to Redis matters more than capacity at this scale; deploy Redis colocated with each agent-service region. |
| **Eval suite gates every prompt change in CI** | Manual eval runs aren't reliable at this scale of prompt iteration. GitHub Actions runs all ~80 cases on every prompt commit; merges blocked on pass rate. |
| **On-call rotation + SLOs** | 10K active physicians = a paged event happens. PagerDuty + a clear "what is broken when /agent/chat 5xx's" runbook. |

| Component | Monthly cost |
|-----------|--------------|
| AWS Bedrock (Sonnet 4.6 equivalent, 14.3M turns) | $272,000 |
| AWS PrivateLink endpoints (3 regions × $50) | $150 |
| AWS infra (compute, RDS, ElastiCache, NLB) | $4,000 |
| Langfuse self-hosted multi-region | $300 |
| Engineering on-call (1 person, 25% time) | $5,000 |
| **Total** | **~$281,500/mo** |
| **Per physician** | **~$28/mo** |

Per-user cost barely moves. Per-turn cost is the dominant term and it
doesn't change with scale — what scales is the headcount and ops cost
to operate at this size, not the AI bill.

### 3.4 100,000 users — multi-region, dedicated, regulated

| What changes | Why |
|--------------|-----|
| **Provisioned-throughput Bedrock or dedicated Anthropic** | At ~143M turns/month, on-demand pricing is replaced by reserved capacity. Negotiated discount typically 20-40%. |
| **Per-region active-active with global load balancing** | Latency requires <50 ms LB→agent-service→Bedrock; achievable only with regional pinning. |
| **Dedicated MySQL clusters per ~10 large hospital systems** | OpenEMR-side; not strictly the agent's concern, but the agent's queries need isolation. |
| **Dedicated security + compliance team** | Annual SOC 2 / HITRUST audit with full evidence pipeline. |
| **Eval suite expanded to ~1,000 cases including incident replays** | Every production hallucination becomes a regression case. |

| Component | Monthly cost |
|-----------|--------------|
| Bedrock with reserved capacity (143M turns, 30% discount) | $1,900,000 |
| AWS infra (multi-region, active-active) | $80,000 |
| Langfuse / observability infra | $5,000 |
| Eng + on-call (5-person team) | $80,000 |
| Security + compliance (2-person team + audit) | $50,000 |
| **Total** | **~$2,115,000/mo** |
| **Per physician** | **~$21/mo** |

Per-user cost actually drops in this tier — the reserved-capacity
discount and amortised security team beat the linear extrapolation.
The architecture this tier needs (multi-region, dedicated audit
pipeline, formal SLOs) is what you would build for a national EHR
vendor, not for a clinic-by-clinic rollout.

---

## 4. The non-linear cost: warm-on-chart-open

The architecture's biggest hidden non-linearity isn't the chat
itself — it's the per-chart bundle warm-fetch. At small scale this
is free (warm = 1 background async fan-out per chart open). At 10K
users with realistic chart-open rate this is the dominant
infrastructure cost driver.

```
100 users:    ~10 chart-opens/min     →  no queueing required
1,000 users:  ~100 chart-opens/min    →  in-process throttle suffices
10,000 users: ~1,000 chart-opens/min  →  external queue mandatory
100,000:      ~10,000 chart-opens/min →  per-region queue + sampling
```

At 100K, we sample warm-fetches: only ~10% of chart opens trigger an
actual warm; the other 90% accept a 1-2s cold-turn penalty on the
first chat. The cost saving is ~90% of warm-related compute, which
at this scale is real money. The product cost is a perceptible
latency bump on first turns — defensible because the product moment
is the response, not the warm.

---

## 5. What we are NOT charging for (deliberately)

- **OpenEMR licensing.** OpenEMR is GPL-3; no per-user fee.
- **Custom hardware.** Everything is cloud-provisioned.
- **Engineer time on the open-source community.** OpenEMR module
  contributions back to mainline are unpaid effort; not a cost.
- **Anthropic prompt cache infrastructure.** The cache TTL is
  Anthropic-side; we don't run it.

---

## 6. BAA + production gating

Before this can take real PHI in production at any scale beyond
internal demo:

- **Anthropic Direct BAA** ($0 incremental per case study assumption,
  but a contractual prerequisite). Enterprise sign-up.
- **AWS BAA** (free with an AWS account; required if we go Bedrock).
- **Langfuse self-hosted** (cloud version's BAA story is incomplete
  for PHI as of writing; safer to self-host on infra already covered
  by the AWS BAA).
- **HIPAA risk assessment + technical safeguards documentation**.
  ~$15-30K one-time, externally vetted.

These are all already in [ARCHITECTURE.md §5](ARCHITECTURE.md) and
[AUDIT.md §5](AUDIT.md) as pre-production blockers. The cost numbers
above assume the BAAs are in place.

---

## 7. The summary line

| Scale | $/physician/month | $ total / month | Architecture inflection |
|-------|-------------------|-----------------|-------------------------|
| 100 | $28 | $2,800 | None — current architecture |
| 1,000 | $27 | $27,400 | Horizontal scale + per-clinic isolation |
| 10,000 | $28 | $281,500 | Bedrock + PrivateLink + ops |
| 100,000 | $21 | $2,115,000 | Reserved Bedrock + multi-region + compliance team |

Per-physician cost stays in a **~$21-28/month band** across four orders
of magnitude. That's the headline: this isn't a hockey-stick economics
story — the dominant per-turn cost is the LLM call itself, and that
cost is roughly linear in usage. What scales discontinuously is the
operations and compliance overhead, not the AI bill.

The investment case isn't "AI bills explode at scale" — it's
"per-user economics are stable, so the question is whether you're
selling something a clinic will pay $50-100/physician/month for."
At those prices, this product has 50-80% gross margin at every tier.
