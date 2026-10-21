# M2 UC1 code generation results

| | |
|---|---|
| Milestone | M2: UC1 code generation (see [ROADMAP_AND_PLAN.md](ROADMAP_AND_PLAN.md) §1) |
| Date | 2026-10-21 (in progress) |
| Commit | `446e61b` (the R1 pilot ran on this) |
| Gate verdict | **Not yet run.** R1 pilot done and reviewed; R2 and R3 pending |
| Tokens / spend | R1: $0.29; R1b cap probe: $0.29; OpenAI Fast-mode test: $0.002 |
| Cap probe answer | **A bigger budget barely helps Mercury 3:** at 131,072 tokens, 1 of 12 failures was rescued (§4) |
| OpenAI latency answer | **Not your usage tier:** OpenAI adds ~0.45–0.6 s server-side per request; paid Fast mode trims ~0.15 s at the median (§5) |

## 1. Gate checklist

The exit gate from ROADMAP_AND_PLAN §1:

| Criterion | Result | Evidence |
|---|---|---|
| Pilot reviewed | ✅ | §3 (pilot), §4 (cap probe), §5 (OpenAI Fast-mode test), §6 (findings) |
| R2 and R3 complete | ⏳ | — |
| Pareto chart regenerates offline | ⏳ | F6.4 not built yet |
| CI targets met (PRD UC1) | ⏳ | — |

## 2. Offline verification (before R1)

- `make check`: 169 offline tests pass.
- `make test-sandbox`: **all 542 EvalPlus canonical solutions pass in Docker**. Pinned input drops: HumanEval/32 7 of 888, Mbpp/255 1 of 112. The lockdown checks pass: no network, no DNS, no writes outside `/tmp`.
- All 3,866 LiveCodeBench v6 tests are well formed.
- See [ROADMAP_AND_PLAN §8](ROADMAP_AND_PLAN.md) for the 26 grader gaps the canonical check found and fixed.

## 3. R1 pilot run

| | |
|---|---|
| Run id | `r1-pilot-uc1-20261021T020019Z` (local only) |
| Config | [configs/experiments/r1_pilot_uc1.yaml](../configs/experiments/r1_pilot_uc1.yaml) |
| Workload | 4 HumanEval+, 3 MBPP+, 3 LiveCodeBench v6 (seed 1) × 3 repeats × 13 arms |
| Jobs / result | **390/390 ok**: 0 errors, 0 retries, 0 warnings, no abort |
| Spend | **$0.29** (dry-run expected $0.31) |
| Scoring | 390 responses graded in Docker in 2 minutes. Extraction: 383 fenced, 7 empty |
| Network baseline | Median connect 17–24 ms everywhere. Median TTFB of `GET /models`: Inception 22 ms, Cerebras 102, Google 147, Anthropic 177, **OpenAI 1,816** |

The network probe's first live run worked against all five providers. Its TLS path was the open item from M1.

> 10 problems and 3 samples each: the numbers below show how the pipeline behaves and where the money and time go. They are **not results**; don't quote them.

### 3.1 Per arm

| Arm | TTFT p50 (s) | Total p50 / p95 (s) | Out tok/s p50 | Length stops | Pilot $ |
|---|---:|---:|---:|---:|---:|
| `mercury-3:instant` | 0.250 | 0.26 / 5.9 | 375 | 0 | 0.0104 |
| `mercury-3:low` | 0.524 | 0.53 / 14.4 | 479 | 0 | 0.0141 |
| `mercury-3:medium` | 0.678 | 0.70 / 57.7 | 436 | **3** | 0.0389 |
| `mercury-3:high` | 0.754 | 1.17 / 52.2 | 429 | **4** | 0.0521 |
| `mercury-2.5:low` | 0.991 | 1.19 / 5.5 | 351 | 0 | 0.0138 |
| `mercury-2.5:medium` | 2.939 | 3.05 / 8.7 | 416 | 0 | 0.0334 |
| `claude-haiku-5-5:thinking-off` | 0.554 | 1.06 / 6.6 | 150 | 0 | 0.0049 |
| `claude-haiku-5-5:medium` | 0.710 | 1.23 / 12.8 | 162 | 0 | 0.0083 |
| `gpt-6-luna:none` | 0.988 | 1.87 / 30.5 | 27 | 0 | 0.0026 |
| `gpt-6-luna:medium` | 1.977 | 2.42 / 25.0 | 60 | 0 | 0.0056 |
| `gemini-3.5-flash-lite:minimal` | 0.519 | 0.82 / 13.2 | 188 | 0 | 0.0544 |
| `gpt-oss-120b-cerebras:low` | 0.227 | 0.33 / 2.0 | 912 | 0 | 0.0176 |
| `gpt-oss-120b-cerebras:medium` | 0.269 | 0.33 / 4.6 | 1155 | 0 | 0.0360 |

## 4. R1b output-cap probe: does a bigger budget help Mercury 3?

**Answer: barely.** At 4× the budget (131,072 tokens), 1 of 12 calls that failed at the 32,768 cap was rescued. The rest either converged quickly anyway or kept reasoning to the new cap. Raising the cap mostly makes failures slower and more expensive, so R2 keeps the uniform 32,768 cap. Caveat: this rests on 2 problems and 12 calls; R2 shows how common runaways are overall.

| | |
|---|---|
| Run id | `r1b-cap-probe-20261021T021543Z` (local only), commit `8e1d87f` |
| Config | [configs/experiments/r1b_cap_probe.yaml](../configs/experiments/r1b_cap_probe.yaml) |
| Workload | The two R1 budget-out problems × Mercury 3 `medium` and `high` × 3 samples, at a **131,072**-token cap (Mercury 3's maximum) |
| Jobs / result | 12/12 ok, $0.29 |

| Call | Stop | Output tokens (all reasoning unless noted) | Time (s) | Result |
|---|---|---:|---:|---|
| HumanEval/146 `medium` ×3 | stop | 711 / 1,109 / 1,192 | 1.3–2.4 | pass ×3 |
| HumanEval/146 `high` | stop, stop, **length** | 1,317 / 1,381 / **129,390** | 2.6 / 2.7 / **83.7** | pass, pass, budget out |
| abc388_d `medium` ×3 | **length** ×3 | 128,982 / 129,565 / 128,982 | 191–207 | budget out ×3 |
| abc388_d `high` | length, **stop**, length | 130,271 / **36,515** / 129,565 | 184 / **61** / 229 | budget out, **pass**, budget out |

**Detail:** quadrupling the budget rescued **1 of 12** calls (abc388_d `high`, which converged at 36,515 tokens, just above the 32,768 cap). Everything else either converged quickly anyway (HumanEval/146, ~1k tokens) or kept reasoning to the new cap (abc388_d, 3–4 minutes each). So Mercury 3's budget-out is mostly **non-converging reasoning on hard problems**, not a cap that's too tight. This supports keeping the uniform 32,768 cap.

One oddity: several runaways stopped at identical token counts (128,982 twice, 129,565 twice). It's recorded as an observation, with no explanation claimed.

## 5. Side test: GPT-6 Luna standard vs Fast mode (M2-F3)

**Question:** is Luna's slow first token caused by your new OpenAI account's usage tier or queueing, or is it fixed overhead at OpenAI's API front door?

**Answer: mostly fixed overhead, not your tier.**
- OpenAI documents usage tiers (Free / Build / Launch / Grow, set by total credit purchases) as changing **rate limits and monthly caps only, not latency** ([rate-limits guide](https://developers.openai.com/api/docs/guides/rate-limits)). The pilot hit no 429s.
- Paying for **Fast mode** (`service_tier: "fast"`, alias `"priority"`, at 2× the price) trims the median TTFT by about 0.15 s and mainly fixes the slow tail.
- Even with Fast mode, OpenAI spends about **450 ms server-side** before the first token. That's well above Mercury 3 `instant` (0.25 s) and the Cerebras control (0.23 s) in the pilot.

| | |
|---|---|
| Script | [scripts/oai_priority_ab.py](../scripts/oai_priority_ab.py), committed together with this section |
| Raw rows | `results/analysis/oai-priority-ab-20261021T033428Z/calls.jsonl` (local only) |
| Design | 20 short coding prompts, `reasoning_effort: none`, 512-token cap, streamed. Each prompt ran once per tier, in seeded random order, one call at a time |
| Cost | **$0.0022** (40 calls) |
| Served tier | Every response reported the tier requested: no silent fallback |

| Tier | TTFT p50 (s) | TTFT p90 (s) | Worst TTFT (s) | Server `openai-processing-ms` p50 | Total p50 (s) |
|---|---:|---:|---:|---:|---:|
| `default` (standard) | 0.825 | 1.522 | 6.85 | 596 | 1.402 |
| `fast` (2× price) | 0.668 | 0.889 | 1.80 | 450 | 0.967 |

**Paired by prompt:** Fast was quicker on 13 of 20 prompts, with a median gain of 0.146 s. So Fast mode helps, but the effect at the median is modest and noisy; the big change is in the tail (worst case 6.85 s → 1.80 s).

**Context:**
- The free `GET /v1/models` call measured shortly before showed 814–1,209 ms of server processing, with a ~20 ms connect and ~40 ms TLS through Cloudflare's San Jose edge. So the slowness is server-side, not the network.
- The pilot's Luna TTFT (0.99 s median with reasoning off) is in line with the standard tier here; time of day varies.

**Implication for the study:** keep `default` for Luna, as for every model (PRD R1: no provider gets a paid speed tier the others lack). Report Luna's latency next to the network baseline and mention that Fast mode exists. One more factor I can't rule out: account age or undocumented per-account routing. Re-probing in R2 and later runs will show whether the gateway overhead shrinks as the account ages.

## 6. Findings

| # | Finding | Impact | Where handled |
|---|---|---|---|
| M2-F1 | **Mercury 3 sometimes reasons until it hits the output cap.** 7 calls (3 at `medium`, 4 at `high`) spent the whole 32,768-token budget on hidden reasoning and returned no answer (`finish=length`, 0 characters). 6 of them were LiveCodeBench `abc388_d` (every medium and high sample) and 1 was HumanEval/146. Its median latency stays low (0.70 s / 1.17 s), but these calls set its p95 at 52–58 s (worst 76 s). No other arm hit the cap | Lowers Mercury's pass@1 at the efforts meant to be most accurate, and dominates its tail latency. Mercury's own defaults are higher (65,536 at medium, 131,072 at high), but the R1b probe (§4) shows that 4× the room rescues only 1 of 12 calls | Scoring reports `budget out` separately from `no code`; both still fail. **Decided: keep the uniform 32,768 cap and report budget-out as a failure mode (§8).** A candidate blog point: a failure mode specific to reasoning effort |
| M2-F2 | **The fast-hardware AR control is the fastest arm here.** gpt-oss-120b on Cerebras: 0.23 s TTFT and 912–1,155 output tok/s at the median, against Mercury 3's 0.25–0.75 s and 375–479 tok/s | If R2/R3 confirm it, speed comes from serving hardware, not architecture: exactly the PRD R13 question | R2/R3 with full n; the length sweep (R3) tests longer outputs |
| M2-F3 | **OpenAI is slow at the network/gateway level.** `GET /models` TTFB was 1.8 s (others 22–177 ms), and one Luna call took 120 s | Part of Luna's latency is OpenAI's API front door, not the model | **Side test (§5): mostly fixed overhead, not the usage tier.** Fast mode trims TTFT p50 by ~0.15 s and fixes the tail, but ~450 ms server time remains. Keep `default` (PRD R1); report Luna next to the network baseline |
| M2-F4 | **Extraction is clean.** 383 of 390 responses were fenced; the 7 empty ones are exactly the budget-out calls. All 17 real failures had extracted code and ran: 15 on `abc388_d` and 2 on Mbpp/84 | No grader or extraction problems found | Hardened anyway for R2: stdin extraction now prefers a block that reads input or prints. 0 pilot results changed |
| M2-F5 | **Medians are below the token guesses, but rare runaways dominate the means.** Pilot spend was $0.29 against $0.31 expected | Cost depends on how often runaways happen, so R2 cost is uncertain | BUDGET_ESTIMATE §8 recalibrated from the pilot |

## 7. R2 recalibration (BUDGET_ESTIMATE §8)

Projected from the pilot's mean cost per call, scaled to R2 (2,710 EvalPlus and 475 LiveCodeBench calls per arm):
- **Total: about $21.70**, against an $80 budget and a $50 dry-run estimate.
- **Paid baselines: about $9.30.**
- **Mercury: about 31M tokens**, 31% of the free credit.

The pilot sampled only 3 LiveCodeBench problems, one of which (`abc388_d`) caused every runaway, so this projection is rough in both directions. The `budget_usd` guard stays at $80.

## 8. Open items

- **Output-cap policy (M2-F1): decided 2026-10-21.** R2 keeps the uniform 32,768 cap (PRD R1), and "budget out" is reported as its own failure mode. The R1b probe (§4) confirmed it: 131,072 rescued 1 of 12 calls.
- R2 and R3, then the Pareto chart (F6.4).

## 9. Where the evidence lives

| What | Where | In git? |
|---|---|---|
| This write-up | `docs/M2_UC1_RESULTS.md` | ✅ |
| R1 raw run | `results/runs/r1-pilot-uc1-20261021T020019Z/` | ❌ local only |
| R1b raw run + scores | `results/runs/r1b-cap-probe-20261021T021543Z/`, `results/analysis/r1b-cap-probe-20261021T021543Z/` | ❌ local only |
| OpenAI Fast-mode test rows | `results/analysis/oai-priority-ab-20261021T033428Z/calls.jsonl` | ❌ local only (rerun: `scripts/oai_priority_ab.py`) |
| R1 scores + analysis | `results/analysis/r1-pilot-uc1-20261021T020019Z/` (`scores.jsonl`, `calls.parquet`) | ❌ local only (regenerate with `score` / `analyze`) |
