# M5 blog draft results

| | |
|---|---|
| Milestone | M5: Blog article (see [ROADMAP_AND_PLAN.md](ROADMAP_AND_PLAN.md) §1) |
| Date | 2026-10-21 |
| Commit | `7e18ef6` (the docs and assets the draft was checked against) |
| Gate verdict | **Draft done, gate not yet passed**: every number is checked against a run id (§3), and the repo is public (2026-10-21), but the clean-clone reproducibility check has not run (§5) |
| Tokens / spend | None (offline only). One web lookup for Inception's announcement |
| Answer | The draft answers the title question with "fast and good, but not the only speed demon": Mercury 3 beats the frontier labs' fast models on speed, ties them with reasoning off, and loses on speed to gpt-oss-120b on Cerebras |

## 1. Gate checklist

The exit gate from ROADMAP_AND_PLAN §1: every number in the draft is footnoted with a run id, and a reproducibility check is done from a clean clone.

| Criterion | Result | Evidence |
|---|---|---|
| Draft follows the outline | ✅ | The user's 7-part outline (claim, link back, the two tests, findings, surprises, verdict, next tests), which replaces BLOG_ALIGNMENT §4 for this article |
| Every number traced to a run id | ✅ | §3 below. The draft names its runs in figure captions and a closing data note, not per-number footnotes (general-audience style) |
| Figures copied with dated captions | ✅ | fig1, fig2, fig4 and `diffusion_2026.gif` copied unchanged from `reports/assets/`; each caption names the run and "as of 2026-10-21" |
| Caveats from the results docs | ✅ | "Caveats, honestly": products not architectures (PRD R13), LiveCodeBench pre-cutoff, UC2 EX CIs, promo vs regular price, single host and region |
| Reproducibility check from a clean clone | ❌ | Not run. Raw runs are local only, and `results/published/` is not populated (§5) |
| Repo public | ✅ | `zbruceli/mercury3` made public by the user; confirmed 2026-10-21 (GitHub API and the README hero image return 200 without auth) |

## 2. The draft

| | |
|---|---|
| Path | `draft.md` in the author's blog folder (outside the repo, at the user's request; ROADMAP_AND_PLAN §5 had planned `reports/blog/draft.md`) |
| Images | `images/hero_drag_race.png` (hero, §3), `images/fig1_pareto_uc1.png`, `images/fig2_task_vs_call_speedup_uc2.png`, `images/fig4_cost_per_success.png`, `images/diffusion_2026.gif` |
| Length | ~2,400 words including captions and the editor note (`wc -w`) |
| Not used | fig3 (latency CDF), the reliability table and decision matrix as tables (their key numbers are in the text); all three are in the technical report (§7) |

**Opening claim source.** Inception, "Diffusion at Scale: Introducing Mercury 3", https://www.inceptionlabs.ai/blog/introducing-mercury-3, dated Oct 19, 2026 on the page (checked 2026-10-21). Paraphrased: outperforms GPT-6 Luna and Claude Haiku 5.5 while running 4–8× faster, 1,000+ tok/s (1,068 tok/s on NVIDIA GPUs). One search snippet gave Oct 15; the page itself says Oct 19, which the draft uses.

## 3. Fact-check table

Sources: M2 = [M2_UC1_RESULTS.md](M2_UC1_RESULTS.md), M3 = [M3_UC2_RESULTS.md](M3_UC2_RESULTS.md), M4 = [M4_ANALYSIS_RESULTS.md](M4_ANALYSIS_RESULTS.md), R = `reports/` (CSV next to each figure). Run ids: R1 `r1-pilot-uc1-20261021T020019Z`, R1b `r1b-cap-probe-20261021T021543Z`, OAI `oai-priority-ab-20261021T033428Z`, R2 `r2-uc1-main-20261021T034053Z`, R3 `r3-uc1-length-20261021T152238Z`, R4 `r4-uc2-main-20261021T165313Z`, R4c `r4c-uc2-full-20261021T214456Z`, R4b `r4b-uc2-strict-20261021T170547Z`, R5 `r5-diffusion-capture-20261021T181518Z`.

### Hero image (`images/hero_drag_race.png`)

Built by `scripts/hero_drag_race.py` (copy in the blog folder; output also at `docs/images/hero_drag_race.png` for the README) from `reports/assets/fig1_pareto_uc1.csv` only. Each car sits at the fraction of the track it covers by the time the fastest model finishes (0.345 s ÷ its own median), assuming constant speed. The image has no title or footer (2026-10-21, user request): each car is labelled with its model name and median time only; pass rates appear in the article body, not the image.

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| gpt-oss-120b `medium` 0.34 s, 84.9% | 0.3449 s, 0.8493 | fig1 CSV | R2 | ✅ |
| Gemini 3.5 Flash-Lite `minimal` 0.76 s, 82.4% | 0.7644 s, 0.8242 | fig1 CSV | R2 | ✅ |
| Mercury 3 `low` 0.83 s, 86.0% | 0.8275 s, 0.8603 | fig1 CSV | R2 | ✅ |
| Claude Haiku 5.5 thinking off 0.97 s, 86.1% | 0.9710 s, 0.8609 | fig1 CSV | R2 | ✅ |
| GPT-6 Luna `none` 1.28 s, 83.2% | 1.2801 s, 0.8320 | fig1 CSV | R2 | ✅ |

### Intro and diffusion section

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| Launch date Oct 19, 2026; 4–8× faster than Luna/Haiku; >1,000 tok/s | as stated | Inception blog post (§2) | — (vendor) | ✅ |
| About $56 of API spend | 0.29 + 0.29 + 0.002 + 23.37 + 2.03 + 0.24 + 0.45 + 0.04 + 6.42 + 21.78 + 0.61 + 0.0079 = $55.53 at Mercury's promo price (Mercury mostly covered by free credit; R4c went past it) | M2 header, M3 header and §6.5, M4 header | R1, R1b, OAI, R2, R3, R4c, UC2 pilots, R4, R4b, R5 | ✅ |
| More than 62,000 model calls | 62,975 jobs (390 + 12 + 40 + 41,405 + 630 + 148 + 300 + 40 + 4,500 + 14,880 + 600 + 30); UC2 jobs are multi-call tasks, so calls exceed this | M2 §3–7, M3 §4–7 and §6.5, M4 §3 | same | ✅ |
| Diffusing answers resolve in 5–11 visible steps, well under a second at the median | frames 8 (6–10) `instant`, 7 (5–11) `low`; span median 0.45 / 0.34 s | M4 §3 | R5 | ✅ |
| All 30 calls have ≥1 step that moves away from the final | 30/30 | M4 §3, M4-F6 | R5 | ✅ |
| Comparison prompt "split an image into left and right halves" | R5 prompt 1 | M4 §3 | R5 | ✅ |

### The two tests

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| 637 problems (HumanEval+, MBPP+, LiveCodeBench), 5 tries | 164 + 378 + 95 × 5 repeats | M2 §6 | R2 | ✅ |
| Example coding task (close numbers below a threshold) | HumanEval/0 `has_close_elements`, in the 164 | dataset | R2 | ✅ |
| SQL example "total amount of money spent on food" | `bird/1380` (student_club, simple), in all 496 | configs/uc2/bird_all_496.txt | R4c | ✅ |
| All 496 usable questions, 3 tries, up to 8 turns | 148 / 247 / 101 by difficulty | M3 §6.5 | R4c | ✅ |

### Finding 1 (UC1 speed and quality)

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| Mercury 3 `low` 86.0% at 0.83 s | 0.8603, 0.8275 s | M2 §6.1; R fig1 CSV | R2 | ✅ |
| Haiku thinking off 86.1% at 0.97 s | 0.8609, 0.9710 s | same | R2 | ✅ |
| Luna `none` 83.2% at 1.28 s | 0.8320, 1.2801 s | same | R2 | ✅ |
| gpt-oss `medium` 84.9% at 0.34 s | 0.8493, 0.3449 s (M2 rounds to 0.35, assets to 0.34; M4-F2) | fig1 CSV | R2 | ✅ |
| Mercury 3 ~850–900 visible tok/s | 849–905 across `instant`/`low` buckets | M2 §7 | R3 | ✅ |
| ~2× Gemini, 2.8× Haiku, 6× Luna | 893/438 = 2.0; M2 §7 "2.8× Haiku, 6× Luna" (long bucket: 893/324 = 2.8, 893/132 = 6.8; medium: 902/164 = 5.5) | M2 §7 | R3 | ✅ |
| gpt-oss ~2,400–2,500 tok/s, ~2.7× Mercury, flat with length | 2,396–2,471; M2-F8 | M2 §7 | R3 | ✅ |
| Short answers: total 0.30 s vs TTFT 0.29 s | as stated (`instant`, short bucket) | M2 §7, M2-F10 | R3 | ✅ |

### Finding 2 (UC2 agent)

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| Task p50: Mercury `low` 2.05, Haiku off 3.87, Gemini 4.44, Luna 5.69 s | 2.0542 / 3.8704 / 4.4434 / 5.6878 | M3 §6.5; fig2 CSV | R4c | ✅ |
| 1.9–2.8× faster per task than frontier-lab fast models | task ratios 1.88–2.77 | fig2 CSV | R4c | ✅ |
| Haiku 3.2× slower per call, 1.9× per task, 3.2 vs 5.1 turns | 3.15 / 1.88; turns 3.20 vs 5.11 | fig2 CSV | R4c | ✅ |
| gpt-oss `low` 1.7× faster per call, 6.3 turns, 1.92 s per task | call ratio 0.591 (1/0.591 = 1.69); 6.25; 1.9194 s | fig2 CSV | R4c | ✅ |
| gpt-oss `medium` slower than Mercury per task | task ratio 1.16 | fig2 CSV | R4c | ✅ |
| EX range 44.7–57.8% | gpt-oss `medium` 44.7, Haiku `medium` 57.8 | M3 §6.5 | R4c | ✅ |
| Haiku 4–5 points ahead; gpt-oss 6–8 behind; Luna and Gemini level | Haiku `medium` +5.3 (+2.6, +8.1), off +4.2 (+1.8, +6.7); gpt-oss `low` −6.3 (−9.3, −3.3), `medium` −7.8 (−10.8, −4.7); Luna −1.5, Gemini −1.3 (CIs span zero). `uc2.paired_ex_delta` seed 4, as in the decision matrix (M3 §6.5 prints Haiku off +1.7 and Mercury `instant` −0.3 for the bounds at another seed) | decision matrix; M3 §6.5 | R4c | ✅ |

Note: Gemini's mean turns are 6.6 in fig2 (latency-eligible tasks) and 6.7 in M3 §6.1 (all tasks). The draft doesn't quote Gemini's turn count.

### Finding 3 (cost)

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| Mercury price $0.10 / $0.35 promo, $0.20 / $0.70 regular, 50% off | as stated, prices as listed 2026-10-20 | PRD §5 pricing; Inception post | — | ✅ |
| UC1: Mercury `low` $0.59 / $1.19 per 1k passes; Luna `none` $0.07; Haiku off $0.14 | 0.594 / 1.188; 0.068; 0.144 | fig4 CSV | R2 | ✅ |
| UC2: Mercury `low` $1.38 / $2.77; Luna $0.77; Haiku off $1.55 beats every Mercury arm at regular price; gpt-oss and Gemini $5.43–7.07 | 1.384 / 2.768; 0.766; 1.546 (cheapest Mercury at regular price: `low` 2.768); 5.428–7.070 | fig4 CSV | R4c | ✅ |

### Finding 4 (runaway reasoning)

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| 86.0 → 82.3 → 81.4% for `low` / `medium` / `high` | as stated | M2 §6.1 | R2 | ✅ |
| Budget-out 7.5% `medium`, 8.6% `high`, at the 32,768 cap | as stated | M2 §6.1, table_reliability §3 | R2 | ✅ |
| Runaways burned 61% of Mercury 3 output tokens | 61% | M2-F6 | R2 | ✅ |
| p95 past 50 s | 51.9 / 53.6 s | M2 §6.1 | R2 | ✅ |
| 4× cap: 1 of 12 probe calls extra finished | 1 of 12 | M2 §4 | R1b | ✅ |
| No other model ran away in the code test | all other arms 0.0% | table_reliability §3 | R2 | ✅ |
| Directrix (Mbpp/430): Mercury `medium`/`high` hit the cap on all 10 samples; ~32k tokens, close to a minute | 10/10; 32,204 tokens, 53.8 s in the gallery example | failure_gallery case 2, M4-F4 | R2 | ✅ |
| Mercury 3 `low` vs Mercury 2.5 `low`: 86.0 vs 76.9% (+9), 0.83 vs 1.36 s | as stated | M2-F7 | R2 | ✅ |

### Surprises

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| OpenAI `GET /models` ~0.8–1.2 s server processing | 814–1,209 ms | M2 §5 | OAI context probe | ✅ |
| Others' `GET /models` ~15–225 ms | TTFB Inception 14–22, Cerebras 93–159, Google 128–208, Anthropic 155–224 ms | M2 §3, M3 §4.2, §6 and §6.5 | R1, R4, R4c probes | ✅ |
| OpenAI ~0.6 s server-side on real requests | `openai-processing-ms` p50 596 | M2 §5 | OAI | ✅ |
| Fast mode (2× price) trims ~0.15 s at the median, mostly fixes the tail | median paired gain 0.146 s; worst 6.85 → 1.80 s | M2 §5 | OAI | ✅ |
| Luna decodes ~130–160 tok/s, cheapest per success in both tests | 132–164; fig4 lowest in both panels | M2 §7; fig4 CSV | R3, R2, R4c | ✅ |
| ~800-line request: Haiku and Luna ~15k tokens, gpt-oss ~6k | 14,530 / 15,017 / 6,136 | M2 §7 | R3 | ✅ |
| Haiku with strict JSON: 150/150 one-shot, no tool calls, 53.3% | as stated | M3 §7, M3-F5 | R4b | ✅ |
| Mercury strict-JSON violations 6 of 150 (5 markup, 1 empty); others 0 | as stated | M3 §7, M3-F4 | R4b | ✅ |
| Cerebras: under documented 1,000 RPM, 765 429s in the code test | as stated | M2 §6.3 | R2 | ✅ |

### Verdict

| Draft claim | Value in source | Source | Run | ✔ |
|---|---|---|---|---|
| 1.2–1.5× per coding answer at matched quality | 0.97/0.83 = 1.17 (Haiku off), 1.28/0.83 = 1.54 (Luna `none`) | M2 §6.1 | R2 | ✅ |
| Luna `medium` 88.4%, Haiku `medium` 87.9% vs 86.0% | as stated | M2 §6.1 | R2 | ✅ |
| Cerebras control faster than Mercury in both tests | UC1 0.34 vs 0.83 s; UC2 gpt-oss `low` 1.92 vs 2.05 s per task | M2 §6, fig2 CSV | R2, R4c | ✅ |
| Mercury `realtime` made no difference for code | M2-F9 | M2 §7 | R3 | ✅ |
| "~500 scorable BIRD questions" | 496 | M3-F8 | — | ✅ |

## 4. Claims not verified by this study

- **Inception's own numbers** (1,068 tok/s on NVIDIA GPUs, "4–8× faster", "outperforms") are quoted as the vendor's claims and set against our measurements. The draft does not claim Mercury runs on GPUs beyond what the post says.
- **"Diffusion may well be why Mercury beats the big labs' fast models"** is hedged on purpose: the study compares products (PRD R13) and can't separate architecture from serving.
- **"Early research suggested diffusion models handle reversal tasks"** (future tests) refers to LLaDA, cited in the original article (BLOG_ALIGNMENT C8); not tested here (F7.1 open).
- **Client region "US West"**: confirmed by the user on 2026-10-21 (closes M4 open item 3).

## 5. Open items

1. ~~**Make `zbruceli/mercury3` public.**~~ Done by the user, confirmed 2026-10-21.
2. **Reproducibility check from a clean clone (ROADMAP gate).** Raw runs and score files are local only; copy the source runs to `results/published/` (or publish them elsewhere) and run `dllm-eval report` from a fresh clone.
3. **Carried from M4:** UC2 EX precision (the draft publishes the paired difference with its CI), and the harness hardening for non-API exceptions. The client region was confirmed on 2026-10-21.
4. **Editorial review by the user.** Title, tone, and the editor note at the top of the draft.

## 6. Where the evidence lives

| What | Where | In git? |
|---|---|---|
| This fact-check | `docs/M5_BLOG_RESULTS.md` | ✅ |
| Draft and images | The author's blog folder (`draft.md`, `images/`) | ❌ outside the repo |
| Figure sources | `reports/assets/` (+ CSVs) | ✅ |
| Source runs | `results/runs/<run_id>/` for every run in §3 | ❌ local only |

## 7. Technical report (long version)

| | |
|---|---|
| Path | `technical_report.md` in the author's blog folder, next to the draft |
| Length | ~7,300 words of text (8,576 by `wc -w`, which also counts table pipes and the code block) |
| Images | The draft's five, plus `images/fig3_latency_cdf.png` (copied from `reports/assets/`, scaled to 1,600 px) and `images/fig5_decode_speed_r3.png` (new, built by `fig_decode_speed.py` in the blog folder from the R3 table in M2 §7) |
| Format | `$` and `%` escaped outside code; every image PNG or GIF, ≤ 1,600 px on the long side, < 1 MB |

Every table in the report is copied from a results doc or a generated report, so its numbers trace to the run named there. Section by section:

| Report section | Source | Run |
|---|---|---|
| §1 key findings | Rows of §5–§7 below | R2, R3, R4c, R4b |
| §2 the claim | Inception blog post (§2 above); pricing from configs/models.yaml (`verified_on` 2026-10-20) | — |
| §3 diffusion dynamics table and observations | M4 §3, M4-F6; caveat on the diffusing stream from M0 §4 | R5 |
| §4.1 models and settings table, prices | configs/models.yaml; temperatures from configs/experiments/r2_uc1_main.yaml; PRD R1–R3 | — |
| §4.2 measurement | PRD R5–R12; CLAUDE.md measurement rules | — |
| §4.3 run inventory | M2 header and §3–§7, M3 header and §4–§7, M4 §3; totals as in §3 above (62,975 jobs, \$55.53) | all |
| §4.3 network baselines | M2 §3, M3 §4.2, §6 and §6.5 | R1, R4, R4c probes |
| §5.1 UC1 arm table, CI precision | M2 §6.1 | R2 |
| §5.2 per-dataset table, LiveCodeBench budget-out | M2 §6.2 | R2 |
| §5.3 latency tail | M4 §4 (fig 3); fig3 CSV quantiles (UC2 p90/p95 per arm) | R2, R4c |
| §5.4 length sweep table and findings | M2 §7 | R3 |
| §5.5 runaway table | reports/table_reliability.md §3; counts from reports/failure_modes.md | R2 |
| §5.5 61\% of tokens, 119 problems | M2-F6, M4-F4 | R2 |
| §5.5 cap probe table | M2 §4 | R1b |
| §5.5 gallery cases 1–4 | reports/failure_gallery.md cases 1–4 | R2 |
| §5.6 lineage | M2-F7, M2 §6.1 | R2 |
| §6.1 BIRD design and example | M3 §3, §5, §6.5; PRD UC2; `bird/1380` from configs/uc2/bird_all_496.txt | R4c |
| §6.1 trace replay | M3 §6.5 (14,869/14,880) | R4c |
| §6.2 UC2 arm table | M3 §6.5; fig2 and fig4 CSVs | R4c |
| §6.2 paired differences | Recomputed offline on 2026-10-21 with `uc2.paired_ex_delta` (seed 4, the run seed, matching the generated decision matrix) on R4c's `uc2_scores.jsonl` | R4c |
| §6.2 per-call vs per-task ratios | fig2 CSV | R4c |
| §6.2 difficulty breakdown, BIRD labels | M3 session note `uc2-r4-to-r4c-number-changes.md` (from `dllm-eval score`), M3-F7 | R4c, UC2 pilot |
| §6.3 reliability table, turn-cap classification | reports/table_reliability.md §1; reports/failure_modes.md (UC2) | R4c |
| §6.3 gallery cases 5–7 | reports/failure_gallery.md cases 5–7 (new R4c examples) | R4c |
| §6.4 strict-schema table and behaviours | M3 §7, M3-F4, M3-F5; gallery cases 8–9 | R4b |
| §7 cost table | fig4 CSV | R2, R4c |
| §7 caching | M3 §5 | — |
| §8.1 OpenAI front door, Fast-mode table | M2 §5 | OAI |
| §8.2 Cerebras throttling | M2 §6.3 | R2 |
| §8.3 Mercury quirks | M0 F2, F7; ROADMAP_AND_PLAN §6; CLAUDE.md API notes | R0 |
| §8.4 other providers | M3-F1, M3-F2, M3-F8, M0 F1, F3, F4 | UC2 pilot, R0 |
| §9 claims scorecard | Same numbers as the draft (§3 above) | R2, R3, R4c |
| §10 decision table | reports/decision_matrix.md (regenerated from R4c) | R2, R3, R4c, R4b |
| §11 threats to validity | PRD; M2–M4 open items | — |

Derived numbers in the report that don't appear verbatim in a source: gpt-oss `medium` at 42\% of Mercury 3 `low`'s median (0.345 / 0.8275 = 0.417); Mercury 3 `low` takes 15\% less time than Haiku thinking off (1 − 0.8275 / 0.9710 = 0.148); Mercury 2.5 `medium` at 4.6× Mercury 3 `low`'s latency (3.8181 / 0.8275); gpt-oss per-task speed-ups 1.13× and per-call 1.5× (1 / 0.8831, 1 / 0.6789).

## 8. UC2 numbers moved to R4c (2026-10-21)

The M3 session ran R4c (`r4c-uc2-full-20261021T214456Z`, all 496 usable BIRD questions, 14,880 tasks, \$21.78) and regenerated `reports/` from it (commit `9b550f7`). The draft, the technical report, and the README now quote R4c for every UC2 number; fig2, fig3 and fig4 were re-copied into the blog `images/` (scaled to 1,600 px). R4b (strict JSON, on R4's 150 questions) and every UC1 number are unchanged; the report compares R4b's Haiku 53.3\% against R4's 58.9\% because both use the same 150 questions. The rows in §3 and the map in §7 above point at R4c. What changed in the story: Claude Haiku's agent accuracy lead is now clear (+4–5 points), gpt-oss on Cerebras is clearly behind (−6 to −8), and at Mercury's regular price Claude Haiku thinking off is cheaper per correct task than every Mercury setting. The speed story is unchanged.
