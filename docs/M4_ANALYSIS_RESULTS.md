# M4 analysis and blog assets results

| | |
|---|---|
| Milestone | M4: Analysis and assets (see [ROADMAP_AND_PLAN.md](ROADMAP_AND_PLAN.md) §1) |
| Date | 2026-10-21 |
| Commit | `6ebff1f` (the report generator and assets) |
| Gate verdict | **Passed with open items** (2026-10-21): all 8 assets exist. UC2 EX misses the B4 precision target, so UC2 accuracy is published as paired differences with CIs (§1, §7) |
| Tokens / spend | **R5: $0.0079** (30 Mercury 3 calls; dry-run expected $0.013). Everything else offline |
| Answer | All 8 assets exist. Every number in the blog can now be regenerated with one offline command, `uv run dllm-eval report`, which writes all assets into [reports/](../reports/README.md) with a dated "as of" footnote on each |

> **Update 2026-10-21: UC2 assets now come from R4c.** `configs/report.yaml` points `uc2` at `r4c-uc2-full-20261021T214456Z` (all 496 scorable questions; M3_UC2_RESULTS §6.5) instead of R4 (150 questions). Regenerated with `dllm-eval report`: fig2, fig3 (right panel), fig4 (right panel), the reliability table, the UC2 failure modes, gallery cases 5–7 (new R4c examples with notes checked against the traces), and the decision matrix. The decision-matrix generator now handles several arms qualifying, lists arms significantly *behind* Mercury 3 `low`, and derives the agent-cost runner-up from the data. With R4c, paired EX differences have CIs of ±2–3 points, which meets B4. The UC2 numbers below this note are R4's, kept as the record of the original gate.

## 1. Gate checklist

The exit gate from ROADMAP_AND_PLAN §1: every item in the BLOG_ALIGNMENT §5 asset checklist exists and passes TEST_PLAN B4.

| Asset (BLOG_ALIGNMENT §5) | Exists | B4 | Evidence |
|---|---|---|---|
| `fig1_pareto_uc1.png` | ✅ | ✅ | [reports/assets/fig1_pareto_uc1.png](../reports/assets/fig1_pareto_uc1.png) + `.csv`, from `r2-uc1-main-20261021T034053Z` |
| `fig2_task_vs_call_speedup_uc2.png` | ✅ | 🟡 latency ✅, EX not shown | [reports/assets/fig2_task_vs_call_speedup_uc2.png](../reports/assets/fig2_task_vs_call_speedup_uc2.png) + `.csv`, from `r4-uc2-main-20261021T165313Z` |
| `fig3_latency_cdf.png` | ✅ | ✅ | [reports/assets/fig3_latency_cdf.png](../reports/assets/fig3_latency_cdf.png) + `.csv`, from R2 and R4 |
| `fig4_cost_per_success.png` (promo + regular) | ✅ | 🟡 UC2 panel shows EX | [reports/assets/fig4_cost_per_success.png](../reports/assets/fig4_cost_per_success.png) + `.csv`, from R2 and R4 |
| `diffusion_2026.gif` | ✅ | ✅ | [reports/assets/diffusion_2026.gif](../reports/assets/diffusion_2026.gif) + [diffusion_dynamics.csv](../reports/assets/diffusion_dynamics.csv), from `r5-diffusion-capture-20261021T181518Z` (§3) |
| `table_reliability.md` | ✅ | 🟡 UC2 | [reports/table_reliability.md](../reports/table_reliability.md), from R2, R4, R4b |
| `failure_gallery.md` (6–10 examples) | ✅ (9) | ✅ | [reports/failure_gallery.md](../reports/failure_gallery.md), notes in [reports/failure_tags.csv](../reports/failure_tags.csv) |
| `decision_matrix.md` | ✅ | 🟡 UC2 rows | [reports/decision_matrix.md](../reports/decision_matrix.md), from R2, R3, R4, R4b |

How each asset meets B4:

| B4 criterion | Result | Evidence |
|---|---|---|
| 1. Source run's manifest passes B1 | ✅ | R2, R3, R4, R4b, R5 manifests: clean tree (`git.dirty: false`), live probes recorded (M2 §6, M3 §6, §3 here) |
| 2. CI half-widths (≤ 3 pp pass@1/EX; ≤ 10% p50 latency) | 🟡 | UC1 met (pass@1 ≤ 2.6 pp, latency ≤ 4.5%; M2 §6.1). UC2 latency met (≤ ±8.9%). **UC2 EX not met: ±7 to ±8 points per arm** (M3 §9). Every UC2 accuracy number in the assets carries its CI |
| 3. Regenerates offline | ✅ | `uv run dllm-eval report` reads run directories and score files only; no API calls |
| 4. "As of" footnote: model id, date, tier, region | ✅ | Every figure and table ends with run id, commit, model ids, service tier, client region, price-list date, and as-of date (`report.footnote`). The region is the client's ("one macOS arm64 host, US West"), set in [configs/report.yaml](../configs/report.yaml) |
| 5. Reproducible by someone else from the run directory | ✅ | Two consecutive `dllm-eval report` builds give byte-identical files, PNGs included (md5 of all 14 outputs before R5; adding R5 left every earlier asset unchanged) |

## 2. Offline verification

`make check`: **260 tests pass** (246 at the end of M3, plus 14 in the new test file), 94% coverage. New modules: `analysis/assets.py` 100%, `report.py` 98%, `diffusion.py` 99%, `failures.py` 94%, `charts.py` 99%. [tests/unit/test_report.py](../tests/unit/test_report.py) covers:

- Every UC1 and UC2 failure rule, including that whitespace and case don't make a repeated query new.
- Manual tag overrides, and the three ways a manual file is rejected: a tag on a job that passed, an unknown tag, a job not in the run.
- Paired EX differences, cost per success (no-success arms stay undefined), CDF quantile tables.
- The label placer (no overlaps, leader-line crossing test) and header/footnote spacing.
- The GIF renderer and dynamics on the real R0 diffusing recording.
- `dllm-eval report` end to end on synthetic runs, with and without a diffusion run, and its failure when scores are missing.

## 3. R5 diffusion capture

| | |
|---|---|
| Run id | `r5-diffusion-capture-20261021T181518Z` (local only), commit `1532800` |
| Config | [configs/experiments/r5_diffusion_capture.yaml](../configs/experiments/r5_diffusion_capture.yaml): 5 prompts in the original article's style ("Write a Python function that splits an image into left and right halves.", the same request R0 recorded, plus top/bottom, quarters, grayscale, centre crop) × 3 repeats × Mercury 3 `instant` and `low`, `diffusing: true`, temperature 0.5, 4,096-token cap |
| Jobs / result | **30/30 ok**: 0 errors, 0 retries, 0 warnings; every call stopped normally and carries frames |
| Spend | **$0.0079** (dry-run expected $0.0127, worst case $0.0431) |
| Network baseline | Inception connect 14 ms, TTFB 15 ms |
| GIF | `mercury-3:instant-diffusing/split-halves/0` (10 frames), set as `diffusion_job` in configs/report.yaml |

Diffusing-mode timings are never used for latency (PRD R7). Denoising dynamics (F7.2, [diffusion_dynamics.csv](../reports/assets/diffusion_dynamics.csv)), median (range) per call:

| Arm | Frames | Span (s) | Final chars | Distinct text lengths | Steps that move away from the final | First-frame similarity to final |
|---|---:|---:|---:|---:|---:|---:|
| Mercury 3 `instant` | 8 (6–10) | 0.45 (0.17–2.26) | 3,036 | 5 (4–7) | 2 (1–3) | 0.34 (0.22–0.44) |
| Mercury 3 `low` | 7 (5–11) | 0.34 (0.06–1.00) | 1,849 | 5 (4–7) | 1 (1–3) | 0.18 (0.02–0.55) |

What it shows:
- **The answer resolves in 5–11 visible steps in well under a second** at the median, and it grows in blocks: a call has 4–7 distinct text lengths, so the visible text extends a few times while earlier blocks are still being refined.
- **Refinement is not monotonic.** Every one of the 30 calls has at least one step that ends further from the final text than the step before (M4-F6). That is consistent with remasking: tokens revealed early can be replaced later.
- **At `low`, the first visible frame is further from the final** (median similarity 0.18 vs 0.34). A plausible reading, not tested here: after hidden reasoning, the first visible frame of a shorter answer is mostly noise.

## 4. What the assets show

All from the runs named in each asset's footnote. These repeat M2 and M3 findings where the asset is a new view of them; the new analysis is the failure taxonomy, the cost-per-success view, and the latency tails.

- **Fig 1 (UC1 frontier).** Unchanged numbers from M2 §6; only the layout changed (§5).
- **Fig 2 (UC2 speed-up).** Now labels each task bar with mean turns: Mercury 3 `low` 5.1, Haiku 3.2–3.4, Gemini 6.6, gpt-oss 6.3–6.7. That makes the call-to-task ratio change readable at a glance (M3 open item).
- **Fig 3 (latency CDF).** Mercury 3 `medium`/`high` have by far the longest UC1 tails (p95 52–54 s, M2 §6.1), from runaway hidden reasoning. Mercury 3 `low`'s p95 of 10.4 s is close to GPT-6 Luna's (9.9–10.6 s), despite its 0.83 s median. In UC2, Mercury 3 `instant`/`low` and gpt-oss `medium` lead until about the 80th percentile.
- **Fig 4 (cost per success).** Mercury 3 is not the cheapest per right answer in either use case, even at the promo price:
  - UC1: Mercury 3 `low` costs $0.59 per 1,000 passing answers ($1.19 regular), against $0.07 for GPT-6 Luna `none` and $0.14 for Haiku thinking off.
  - UC2: Mercury 3 `instant` costs $1.31 per 1,000 correct tasks ($2.62 regular), against $0.74 for Luna. gpt-oss and Gemini are the most expensive here ($4.83–6.56), because more turns mean more input tokens.
- **Failure modes ([reports/failure_modes.md](../reports/failure_modes.md)).**
  - Only Mercury 3 runs away in UC1: hidden-reasoning budget-out on 44 / 240 / 275 of 3,185 calls at `low` / `medium` / `high`, and visible think-aloud runaways on 17 calls at `instant`. Every other arm has zero of either.
  - Failed UC2 tasks are mostly wrong answers. Turn-cap failures are mostly still exploring, not circling: Gemini 129 exploring vs 10 repeating a query; gpt-oss `medium` 115 vs 8; Mercury 3 `instant` 35 vs 11.
- **Failure gallery.** 9 examples: 4 from UC1, 3 from the R4 agent loop, 2 from the R4b strict-schema sub-run. The new observation is case 2. On an ill-posed MBPP problem (Mbpp/430, whose one example contradicts the textbook formula), the AR models commit to a wrong answer, and Mercury 3 `medium`/`high` reason until the cap on all 10 samples.

## 5. Changes made in this milestone

- **`dllm-eval report`** ([src/dllm_eval/analysis/report.py](../src/dllm_eval/analysis/report.py), [configs/report.yaml](../configs/report.yaml)): builds every asset, with the footnote and a CSV for each figure (F6.7).
- **New figures** ([analysis/assets.py](../src/dllm_eval/analysis/assets.py)): the latency CDF and cost per success (F6.4).
- **Failure tagging** ([analysis/failures.py](../src/dllm_eval/analysis/failures.py)): rule tags for every failed job, a manual override and annotation file, per-arm aggregation, and gallery export (F6.6).
- **Diffusion replay and dynamics** ([analysis/diffusion.py](../src/dllm_eval/analysis/diffusion.py)): the GIF renderer (F6.5) and per-call step count, block growth, step edit distance and convergence trace (F7.2).
- **Chart polish.**
  - Pareto labels avoid markers, CI bars, connecting lines and each other's leader lines. Far labels get a leader. A label nearer another point than its own is penalised. Several placement orders are tried, and the cheapest layout wins (M2 open item).
  - Titles and footnotes are spaced in inches.
  - Text is drawn with `parse_math=False`, so dollar signs render.
- **Paired EX differences** in code (`uc2.paired_ex_delta`): before, they came from an ad hoc calculation.
- **Docs.**
  - FEATURES F6.4–F6.7 and F7.2 are done (41 of 44).
  - BLOG_ALIGNMENT §5 is ticked except the GIF.
  - The TEST_PLAN B5 R5 row matches the config.
  - The M2 R2 commit is corrected (M4-F1).

## 6. Findings

| # | Finding | Impact | Where handled |
|---|---|---|---|
| M4-F1 | **R2 ran on `07dce9e`, not `446e61b`** as M2 §6 said. The manifest is the record; `07dce9e` adds the M2-F4 extraction hardening on top of `446e61b` | Doc error only; the numbers are unchanged | M2 §6 corrected; footnotes take the commit from the manifest |
| M4-F2 | **gpt-oss `medium` UC1 median latency is 0.345 s**, which M2 rounds to 0.35 s and the generated assets round to 0.34 s | Cosmetic | Assets are the source for the blog; quote 0.34 s or 0.345 s |
| M4-F3 | **The Haiku-vs-Mercury paired EX lead is borderline.** This bootstrap (2,000 resamples over questions) gives +4.7 points, CI +0.0 to +9.3; M3 §6.1's ad hoc calculation gave +0.2 to +9.1 | Don't write "clearly ahead" in the blog: the lower bound sits at zero | Decision matrix flags it as borderline; the calculation now lives in `uc2.paired_ex_delta` |
| M4-F4 | **Mercury 3's runaway reasoning concentrates on problems that are hard or ill-posed.** 119 problems account for every `medium`/`high` budget-out. On Mbpp/430, all 10 `medium`/`high` samples run away, while AR models answer wrongly at once | A "new psychology" point for C11: diffusion-with-reasoning fails by not stopping, AR fails by committing | Gallery cases 1–2; failure_modes.md |
| M4-F5 | **Mercury 3 is not the cheapest per correct answer** in either use case at the promo price, and doubles at the regular price (§4) | Corrects a possible reading of "cheap per token" as "cheap per result" | Fig 4; decision matrix |
| M4-F6 | **Diffusing frames grow in blocks and refinement is not monotonic.** In R5, all 30 calls have at least one step that moves *away* from the final text (median 2 at `instant`, 1 at `low`), with 4–7 distinct text lengths per call. R0's recording showed the same (3 of 9 steps, for example 0.84 → 0.74 before the last frame) | Evidence for C2's "coarse-to-fine" claim with a caveat: generation is block-wise, with remasking, rather than a steady fade-in | §3; `diffusion.dynamics` |

## 7. Open items

1. **UC2 EX precision (TEST_PLAN B4.2), decision needed.** There are two options:
   - Extend R4 to all 496 scorable BIRD questions: about $21 and ~60M Mercury tokens (M3 §9).
   - Publish UC2 accuracy as paired differences with their CIs, which is what the assets do now.
2. **Harness hardening, decision needed.** Non-API exceptions inside a provider call are retried as connection errors (M3 §9, M3-F1b). They should stop the run. It's a small change in `providers/`, and it should land before any further live run.
3. **Client region wording.** configs/report.yaml says "one macOS arm64 host, US West", based on the San Jose CDN edge seen in M2 §5. Confirm or correct it before publishing; every footnote picks it up.

## 8. Where the evidence lives

| What | Where | In git? |
|---|---|---|
| This write-up | `docs/M4_ANALYSIS_RESULTS.md` | ✅ |
| Blog assets (figures, CSVs, tables, gallery, matrix, index) | `reports/`, `reports/assets/` | ✅ |
| Manual failure tags and gallery notes | `reports/failure_tags.csv` | ✅ |
| Report inputs | `configs/report.yaml` | ✅ |
| R5 config | `configs/experiments/r5_diffusion_capture.yaml` | ✅ |
| R5 raw run | `results/runs/r5-diffusion-capture-20261021T181518Z/` (frames in `calls.jsonl`) | ❌ local only |
| Source runs | `results/runs/{r2-uc1-main-20261021T034053Z, r3-uc1-length-20261021T152238Z, r4-uc2-main-20261021T165313Z, r4b-uc2-strict-20261021T170547Z, r5-diffusion-capture-20261021T181518Z}/` | ❌ local only |
| Score files the report reads | `results/analysis/<run>/scores.jsonl`, `uc2_scores.jsonl` | ❌ local only (regenerate with `dllm-eval score`) |
