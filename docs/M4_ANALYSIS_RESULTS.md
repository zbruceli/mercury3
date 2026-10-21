# M4 analysis and blog assets results

| | |
|---|---|
| Milestone | M4: Analysis and assets (see [ROADMAP_AND_PLAN.md](ROADMAP_AND_PLAN.md) §1) |
| Date | 2026-10-21 |
| Commit | `6ebff1f` (the report generator and assets) |
| Gate verdict | **Not yet passed: 7 of 8 assets done.** The diffusion GIF needs the R5 live capture (§3). UC2 EX misses the B4 precision target (§1, §6) |
| Tokens / spend | None (offline only). R5 is drafted and dry-run only: $0.013 expected, $0.043 worst case |
| Answer | Every number in the blog can now be regenerated with one offline command, `uv run dllm-eval report`, which writes all assets into [reports/](../reports/README.md) with a dated "as of" footnote on each |

## 1. Gate checklist

The exit gate from ROADMAP_AND_PLAN §1: every item in the BLOG_ALIGNMENT §5 asset checklist exists and passes TEST_PLAN B4.

| Asset (BLOG_ALIGNMENT §5) | Exists | B4 | Evidence |
|---|---|---|---|
| `fig1_pareto_uc1.png` | ✅ | ✅ | [reports/assets/fig1_pareto_uc1.png](../reports/assets/fig1_pareto_uc1.png) + `.csv`, from `r2-uc1-main-20261021T034053Z` |
| `fig2_task_vs_call_speedup_uc2.png` | ✅ | 🟡 latency ✅, EX not shown | [reports/assets/fig2_task_vs_call_speedup_uc2.png](../reports/assets/fig2_task_vs_call_speedup_uc2.png) + `.csv`, from `r4-uc2-main-20261021T165313Z` |
| `fig3_latency_cdf.png` | ✅ | ✅ | [reports/assets/fig3_latency_cdf.png](../reports/assets/fig3_latency_cdf.png) + `.csv`, from R2 and R4 |
| `fig4_cost_per_success.png` (promo + regular) | ✅ | 🟡 UC2 panel shows EX | [reports/assets/fig4_cost_per_success.png](../reports/assets/fig4_cost_per_success.png) + `.csv`, from R2 and R4 |
| `diffusion_2026.gif` | ❌ | — | Renderer done and tested on the R0 recording (F6.5); the asset needs R5 (§3) |
| `table_reliability.md` | ✅ | 🟡 UC2 | [reports/table_reliability.md](../reports/table_reliability.md), from R2, R4, R4b |
| `failure_gallery.md` (6–10 examples) | ✅ (9) | ✅ | [reports/failure_gallery.md](../reports/failure_gallery.md), notes in [reports/failure_tags.csv](../reports/failure_tags.csv) |
| `decision_matrix.md` | ✅ | 🟡 UC2 rows | [reports/decision_matrix.md](../reports/decision_matrix.md), from R2, R3, R4, R4b |

How each asset meets B4:

| B4 criterion | Result | Evidence |
|---|---|---|
| 1. Source run's manifest passes B1 | ✅ | R2, R3, R4, R4b manifests: clean tree (`git.dirty: false`), live probes recorded (M2 §6, M3 §6) |
| 2. CI half-widths (≤ 3 pp pass@1/EX; ≤ 10% p50 latency) | 🟡 | UC1 met (pass@1 ≤ 2.6 pp, latency ≤ 4.5%; M2 §6.1). UC2 latency met (≤ ±8.9%). **UC2 EX not met: ±7 to ±8 points per arm** (M3 §9). Every UC2 accuracy number in the assets carries its CI |
| 3. Regenerates offline | ✅ | `uv run dllm-eval report` reads run directories and score files only; no API calls |
| 4. "As of" footnote: model id, date, tier, region | ✅ | Every figure and table ends with run id, commit, model ids, service tier, client region, price-list date, and as-of date (`report.footnote`). The region is the client's ("one macOS arm64 host, US West"), set in [configs/report.yaml](../configs/report.yaml) |
| 5. Reproducible by someone else from the run directory | ✅ | Two consecutive `dllm-eval report` builds give byte-identical files, PNGs included (md5 of all 14 outputs) |

## 2. Offline verification

`make check`: **260 tests pass** (246 at the end of M3, plus 14 in the new test file), 94% coverage. New modules: `analysis/assets.py` 100%, `report.py` 98%, `diffusion.py` 99%, `failures.py` 94%, `charts.py` 99%. [tests/unit/test_report.py](../tests/unit/test_report.py) covers:

- Every UC1 and UC2 failure rule, including that whitespace and case don't make a repeated query new.
- Manual tag overrides, and the three ways a manual file is rejected: a tag on a job that passed, an unknown tag, a job not in the run.
- Paired EX differences, cost per success (no-success arms stay undefined), CDF quantile tables.
- The label placer (no overlaps, leader-line crossing test) and header/footnote spacing.
- The GIF renderer and dynamics on the real R0 diffusing recording.
- `dllm-eval report` end to end on synthetic runs, with and without a diffusion run, and its failure when scores are missing.

## 3. R5 diffusion capture (drafted, not run)

| | |
|---|---|
| Config | [configs/experiments/r5_diffusion_capture.yaml](../configs/experiments/r5_diffusion_capture.yaml) |
| Workload | 5 prompts in the original article's style ("Write a Python function that splits an image into left and right halves.", the same request R0 recorded, plus top/bottom, quarters, grayscale, centre crop) × 3 repeats × Mercury 3 `instant` and `low`, `diffusing: true`, temperature 0.5, 4,096-token cap |
| Dry-run | 30 jobs; **$0.0127 expected, $0.0431 worst case**; budget guard $1.00 |
| Needs | The user's go-ahead (CLAUDE.md rule 1), then `uv run --env-file .env dllm-eval run configs/experiments/r5_diffusion_capture.yaml --live`, set `runs.diffusion` in configs/report.yaml, and rerun `dllm-eval report` |

Diffusing-mode timings are never used for latency (PRD R7); the GIF and the dynamics CSV are the only outputs.

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
| M4-F6 | **R0's diffusing frames grow in blocks and are not monotonic.** 10 frames over 0.64 s, 6 distinct text lengths, and 3 of 9 steps move *away* from the final text by similarity (for example 0.84 → 0.74 before the last frame) | First evidence for C2's "coarse-to-fine" mechanics: generation is block-wise, with remasking. One smoke recording, not a published run | `diffusion.dynamics`; R5 will measure it over 30 calls |

## 7. Open items

1. **R5 live capture (needs your go-ahead):** $0.013 expected (§3). Without it, the gate can't pass and the blog hook (BLOG_ALIGNMENT §4.1) has no GIF.
2. **UC2 EX precision (TEST_PLAN B4.2), decision needed.** There are two options:
   - Extend R4 to all 496 scorable BIRD questions: about $21 and ~60M Mercury tokens (M3 §9).
   - Publish UC2 accuracy as paired differences with their CIs, which is what the assets do now.
3. **Harness hardening, decision needed.** Non-API exceptions inside a provider call are retried as connection errors (M3 §9, M3-F1b). They should stop the run. It's a small change in `providers/`, and it should land before any further live run.
4. **Client region wording.** configs/report.yaml says "one macOS arm64 host, US West", based on the San Jose CDN edge seen in M2 §5. Confirm or correct it before publishing; every footnote picks it up.

## 8. Where the evidence lives

| What | Where | In git? |
|---|---|---|
| This write-up | `docs/M4_ANALYSIS_RESULTS.md` | ✅ |
| Blog assets (figures, CSVs, tables, gallery, matrix, index) | `reports/`, `reports/assets/` | ✅ |
| Manual failure tags and gallery notes | `reports/failure_tags.csv` | ✅ |
| Report inputs | `configs/report.yaml` | ✅ |
| R5 config (not run) | `configs/experiments/r5_diffusion_capture.yaml` | ✅ |
| Source runs | `results/runs/{r2-uc1-main-20261021T034053Z, r3-uc1-length-20261021T152238Z, r4-uc2-main-20261021T165313Z, r4b-uc2-strict-20261021T170547Z}/` | ❌ local only |
| Score files the report reads | `results/analysis/<run>/scores.jsonl`, `uc2_scores.jsonl` | ❌ local only (regenerate with `dllm-eval score`) |
