# Blog alignment: from "What is Diffusion LLM and why it matters" to measured results

**Source article:** *What is Diffusion LLM and why it matters*, by Zheng "Bruce" Li, The Low End Disruptor (Medium), Feb 28, 2025.
https://medium.com/the-low-end-disruptor/what-is-diffusion-llm-and-why-it-matters-749033d1efb1

**Purpose.** The original piece was an explainer written on launch day. It described the mechanism (coarse-to-fine denoising), quoted the vendor's speed claim, collected expert reactions, and listed *potential* advantages and open concerns. This project turns those potentials into measurements. This doc maps each claim to the requirement that tests it, finds the gaps, and proposes the follow-up article's structure.

---

## 1. Claim → evidence map

The claims below are paraphrased from the article.

| # | Claim in the original article | Status in Feb 2025 | How this project tests it | PRD ref | Blog asset |
|---|---|---|---|---|---|
| C1 | dLLMs generate in parallel and are much faster; vendor said 1,000+ tok/s, 5–10x faster than competitors | Vendor claim only | UC1 throughput and latency on real coding prompts, measured end-to-end over the network against speed-tier AR models | UC1, R5–R9 | Throughput bar chart; latency CDF |
| C2 | Generation is coarse-to-fine: text emerges from noise | Shown with one animated example | Diffusing-mode capture on the *same* style of prompt as the original (split an image into halves in Python), plus step-count/edit-distance analysis | P2, R7 | Side-by-side GIF: 2025 Mercury Coder vs. 2026 Mercury 3 |
| C3 | Speed makes dLLMs ideal for real-time apps such as chatbots and **coding assistants** | Prediction | UC1 speed-quality Pareto frontier: is Mercury 3 on the frontier, or just fast? | UC1 | Pareto scatter (headline chart #1) |
| C4 | dLLMs may enable **agentic applications** | Prediction | UC2: does per-call speed compound into task-level speed in an 8-turn tool loop? | UC2 | Task vs. call latency-ratio chart (headline chart #2) |
| C5 | Seeing the whole output at once may give better reasoning and **structured responses** | Hypothesis | UC2 tool-call validity rate and strict JSON-schema adherence; UC1 pass@1 across reasoning efforts | UC2, R3 | Reliability table |
| C6 | Iterative refinement may give **error correction and fewer hallucinations** | Hypothesis | UC2 execution accuracy and self-repair rate after SQL errors; failure-mode taxonomy | UC2 | Failure gallery |
| C7 | Better **controllability**, generating tokens in any order | Hypothesis | Stretch P1: reversal/bidirectional-constraint probe | P1 | Small qualitative box |
| C8 | LLaDA research showed dLLMs handle the **reversal curse** | Research citation | P1 reuses a reversal-poem-style probe on Mercury 3 vs. AR baselines | P1 | Optional sidebar |
| C9 | Open concerns: scaling, handling diverse tasks, interpretability | Open | Quality gap versus AR baselines at matched speed; honest limits section | §6 of PRD | "What diffusion still can't do" section |
| C10 | Likely outcome: dLLMs **coexist** with AR models, each suited to different tasks | Prediction | A decision matrix built from UC1 + UC2 (when to pick dLLM vs. AR) | Deliverable 3 | Decision matrix (closing) |
| C11 | Karpathy: dLLMs may show "new, unique psychology", with new strengths and weaknesses | Open invitation | A failure-mode taxonomy coded per model: what fails *differently* for diffusion | UC2, UC1 | Failure gallery with annotations |

**Coverage check.** Every core claim (C1–C6, C9–C11) is covered by UC1 or UC2. C7–C8 depend on stretch probe P1, so the article must work without them.

## 2. Gaps the original article could not address (new material for the follow-up)

These are reasons for the follow-up to exist beyond "we measured it":

1. **Product evolution.** Mercury Coder (code-only) has become Mercury 2, then 2.5, then 3. Mercury 3 is a general reasoning model with a 1M context window, reasoning-effort control, tool calling, and structured outputs. Readers need the timeline.
2. **Reasoning changes the speed story.** Reasoning tokens cost latency for *both* architectures. Is a diffusion model still faster once it "thinks"? The `reasoning_effort` sweep answers this directly.
3. **Speed vs. hardware.** In 2025, fast AR inference on specialised hardware (Groq/Cerebras-style) became widely available. The fast-hardware AR control (PRD R13) is essential to an honest answer.
4. **Economics.** Cost per *successful* task matters more than price per token. The article said little about cost; Mercury 3's pricing ($0.10/$0.35 per 1M promo, $0.20/$0.70 regular) needs an apples-to-apples comparison.
5. **Streaming semantics differ.** Diffusion streams *blocks*, not tokens. This affects UX and how TTFT should be defined. It is a subtle point that is good for the blog.

## 3. Requirements added to the PRD because of this review

- Reproduce the original **animated example** with the same prompt style (C2). Saved as `reports/assets/diffusion_2026.gif`.
- Include **Mercury 2.5** in UC1 for a lineage data point (PRD OQ2).
- Report **both promo and regular prices** in cost tables, because the promo will end and the article should stay accurate.
- Keep a **quotable, dated "as of" line** on every number (model ID, date, region, tier).

## 4. Proposed follow-up article outline

**Working title:** *Diffusion LLMs, 20 months later: I put Mercury 3 to the test against autoregressive models*
**Subtitle:** Is the speed real, does it survive agents, and what does it cost per right answer?

1. **Hook (C2).** The original GIF next to the new 2026 GIF. "In February 2025 I wrote that diffusion LLMs might matter. Here's what happened when I measured it."
2. **Recap in 3 bullets.** What a dLLM is. Link back to the original article; don't re-explain it.
3. **What changed since 2025.** Mercury Coder to Mercury 3, the competitive landscape, and fast AR hardware.
4. **The setup.** The two use cases, the models, and the fairness rules (temperature, reasoning parity, network control). Link to the repo.
5. **Result 1: coding assistant (C1, C3).** Headline Pareto chart, the throughput claim versus measured reality, and the effect of reasoning effort.
6. **Result 2: agent loop (C4, C5, C6).** Does speed compound? Task-level vs. call-level speed-up, tool-call reliability, and cost per correct answer.
7. **The "new psychology" (C11).** Failure gallery: how diffusion fails differently.
8. **Caveats (C9).** Products vs. architectures, the fast-hardware control, promo pricing, and no seed.
9. **Verdict (C10).** A decision matrix: when to reach for a dLLM today.
10. **Appendix.** Methodology, configs, and how to reproduce.

**Target length:** 2,000–2,800 words, 4–6 figures. Same audience as the original: technical practitioners and AI-curious builders.

## 5. Asset checklist (produced by M4 in the roadmap)

All assets live in `reports/` (figures and their CSVs in `reports/assets/`) and regenerate with `uv run dllm-eval report`. Status as of 2026-10-21: see docs/M4_ANALYSIS_RESULTS.md.

- [x] `fig1_pareto_uc1.png`: speed-quality frontier
- [x] `fig2_task_vs_call_speedup_uc2.png`
- [x] `fig3_latency_cdf.png`
- [x] `fig4_cost_per_success.png` (promo + regular)
- [ ] `diffusion_2026.gif`: diffusing-mode replay (renderer built; needs the R5 live capture)
- [x] `table_reliability.md`: tool-call / schema validity
- [x] `failure_gallery.md`: 6–10 annotated examples
- [x] `decision_matrix.md`
