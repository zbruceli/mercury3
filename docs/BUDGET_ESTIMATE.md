# Token and cost estimate (pre-pilot)

| | |
|---|---|
| Status | Reviewed. **Full plan approved 2026-10-20.** Every number is an estimate from **assumptions, not measurements** |
| Created | 2026-10-20 |
| Covers | TEST_PLAN B5 runs R0–R5 |
| Prices | `configs/models.yaml`, verified 2026-10-20 |
| Regenerate | `make budget`. Makes no API calls |
| Generated tables | [budget_tables_full.md](budget_tables_full.md), [budget_tables_lean.md](budget_tables_lean.md) |

## 1. Summary

| | Full plan | Lean plan (§4) | Full plan, reasoning tokens 2x (§5) |
|---|---:|---:|---:|
| Mercury tokens (3 + 2.5) | **96.6M** (97% of free credit) | **68.2M** (68%) | 142.4M (142%) |
| Cash for paid baselines | **$69** | **$62** | $113 |
| Mercury at promo price, if no free credit | $26 | $15 | $46 |
| Mercury at regular price (after promo) | $43 | $28 | $76 |
| **Total cash, with free credit** | **≈ $69** | **≈ $62** | **≈ $123** (≈ $10 Mercury overage) |

All figures include a **+25% contingency** for retries, warm-up discards, re-runs, and the provider-default temperature arms.

**Takeaways**

1. **Cash cost is small.** About $70 in total, inside the PRD's $150 cap even in the 2x-reasoning case.
2. **The 100M Mercury free credit is a soft limit, not a hard one.** The full plan uses 97% of it, so no headroom is left. But going over is cheap: about $0.10–0.35 per extra 1M tokens at promo prices. Running out of credit is a nuisance, not a blocker.
3. **The PRD's earlier "≤15M Mercury tokens" figure was far too low.** The cause is the 4-level effort sweep. The high-effort arm with hidden reasoning tokens dominates (R2 alone is about 30M for Mercury 3). I've updated the PRD and TEST_PLAN to point here.
4. **The real constraint is time spent throttled by rate limits.** The free tier allows 100k output tokens/min, shared across all Inception models. The full plan's ~53M Mercury output tokens (3 + 2.5) need about 9 hours of throttled runtime. Pay-as-you-go (300k/min) cuts this to about 3 hours.
5. **Gemini 3.5 Flash-Lite is the most expensive baseline** ($36, half the baseline spend), because its output price is $2.50/M against $0.50 for Haiku and Luna.

## 2. Assumptions (the numbers to challenge)

All of these live in [configs/budget_assumptions.yaml](../configs/budget_assumptions.yaml).

### 2.1 Arms per model (PRD R3)
| Model | Arms (→ reasoning tier) |
|---|---|
| `mercury-3` | instant→off, low, medium, high |
| `mercury-2.5` | low, medium (UC1 only) |
| `claude-haiku-5-5` | thinking-off→off, medium |
| `gpt-6-luna` | none→off, medium |
| `gemini-3.5-flash-lite` | minimal→off, medium |
| `gpt-oss-120b-cerebras` | low, medium (has no "off") |

### 2.2 Tokens per call
| Workload | Input | Visible output | Hidden reasoning (off / low / med / high) |
|---|---:|---:|---|
| UC1 EvalPlus (HumanEval+ / MBPP+) | 300 | 250 | 0 / 300 / 1,200 / 3,000 |
| UC1 LiveCodeBench | 600 | 500 | 0 / 1,500 / 5,000 / 12,000 |
| UC1 length sweep (avg of 256 / 2k / 8k buckets) | 400 | 3,400 | 0 / 300 / 1,200 / 3,000 |
| UC2 agent, **per turn** | 1,500 + 600 × (turn − 1) | 150 | 0 / 150 / 600 / 1,500 |

- UC2 tasks average 5 turns (cap is 8). That gives about 13.5k input tokens per task, with 60% of turn 2+ input served from the prefix cache.
- The reasoning-token guesses are the **largest uncertainty**. They make up over half of all output tokens in the full plan, so R1 pilot data should replace them first.

### 2.3 Run sizes
| Run | Items × repeats | Arms |
|---|---|---|
| R0 smoke | ~6 calls per model | — |
| R1 pilot | UC1 10×3 + UC2 10×3 | all |
| R2 UC1 main | EvalPlus 542×5 + LCB 100×5 | all |
| R3 UC1 length | 30 prompts × 3 (= 30 samples per bucket) | lowest arm per model; Mercury 3 instant, low, low+`realtime` |
| R4 UC2 main | 150 tasks × 3, plus a strict-schema sub-run of 150×1 at the lowest arm | all except Mercury 2.5; Cerebras skips the strict sub-run (PRD OQ5) |
| R5 diffusion capture | 5 prompts × 3 | Mercury 3 only |

## 3. Where the tokens go (full plan, before contingency)

| Run | Mercury 3 | Mercury 2.5 | Each Haiku / Luna / Flash-Lite | Cerebras |
|---|---:|---:|---:|---:|
| R0 smoke | 7k | 7k | 7k | 7k |
| R1 pilot | 2.3M | 0.1M | 1.0M | 1.1M |
| R2 UC1 main | **29.6M** | 11.4M | 9.8M | 11.4M |
| R3 UC1 length | 1.1M | — | 0.3M | 0.4M |
| R4 UC2 main | **32.9M** | — | 16.3M | 14.5M |
| R5 diffusion | 18k | — | — | — |

Mercury 3 uses about 2–3x a baseline's tokens because it runs 4 arms instead of 2. Each baseline uses about the same number of tokens, so the cost differences come from price alone.

See [budget_tables_full.md](budget_tables_full.md) for per-run input, cached, output, and $ figures.

## 4. Lean plan (option)

[configs/budget_assumptions_lean.yaml](../configs/budget_assumptions_lean.yaml) makes three cuts, each marked `LEAN`:

| Change | Mercury tokens saved | Effect on the blog |
|---|---:|---|
| Mercury 3 **`high`** arm dropped from R4 (UC2 agent) | ≈ 12M | Low. High-effort agent loops contradict the latency story. `high` stays in UC1 |
| Mercury 2.5 limited to one arm (`low`) | ≈ 10M | Low. The lineage chart needs one point, not two |
| LiveCodeBench repeats cut from 5 to 3 | ≈ 6M | Medium. Wider CIs on the contamination-resistant subset (n=100 is already small) |

The result is 68M Mercury tokens (32% credit headroom) and $62 for baselines.

## 5. Sensitivity

- **Reasoning tokens 2x the guesses:** Mercury needs 142M tokens (42M over credit, about $10 overage at promo prices), baselines cost $113, and the total is about $123.
- **Fewer agent turns:** each extra or missing average turn in UC2 changes per-task input by about 4.5k tokens. Going from 5 to 6 turns adds about 30% to R4.
- **Promo ends mid-study:** Mercury's regular price is double the promo. That still means only $28–43 if the credit is exhausted.

## 6. Not modelled

- **Anthropic cache writes.** Writes cost 1.25x input (5-minute cache) and depend on `cache_control` placement. The R4 effect is under +$0.50.
- **Gemini context-cache storage fees.** We rely on implicit caching only. If implicit caching doesn't apply, Gemini R4 rises from about $7.60 to about $9.60.
- **Long-context price tiers.** No workload comes near the Haiku (100K) or Luna (272K) thresholds.
- **Docker sandbox and compute costs.** These run locally.
- **Failed requests that are still billed.** These are covered by the contingency.

## 7. Decisions

1. **Full or lean plan?** **Decided 2026-10-20: full plan.** The lean variant stays in the repo for comparison only. Recommendation at the time: Its cash cost is about $70, and any credit overage costs about $10–20. The lean cuts mainly buy credit headroom, which is cheap to replace.
2. **Upgrade Inception to pay-as-you-go before R2?** I recommend yes, if about 9 hours of throttled runtime matters. It triples the rate limit, and adding a payment method doesn't remove the free credit.
3. **Keep the `budget_usd` guard at $150?** I recommend yes. Set it per run from the R1-calibrated estimate, plus 25%.

## 8. After the pilot (R1)

Replace the §2.2 guesses with R1's measured median tokens per call and turn, broken out by arm, then run `make budget` again and revise this doc. This is the gate before starting R2 (ROADMAP M2).
