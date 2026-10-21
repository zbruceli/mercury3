# PRD: Diffusion LLM vs. Autoregressive LLM Evaluation (Mercury 3)

| | |
|---|---|
| Status | Draft v0.1 |
| Owner | Zheng "Bruce" Li |
| Created | 2026-10-20 |
| System under test | Inception Labs **Mercury 3** (`mercury-3`), a diffusion LLM (dLLM) |
| API reference | https://docs.inceptionlabs.ai/get-started (index: https://docs.inceptionlabs.ai/llms.txt) |
| Downstream output | Follow-up to the Medium article "What is Diffusion LLM and why it matters" (Feb 28, 2025). See [BLOG_ALIGNMENT.md](BLOG_ALIGNMENT.md) |

---

## 1. Background

In February 2025 Inception Labs released Mercury Coder, the first commercial diffusion LLM. It claimed more than 1,000 tokens/s, 5–10x faster than autoregressive (AR) peers. The original article explained *why* diffusion might matter (parallel coarse-to-fine generation, error correction, controllability). It could not show *how much* it matters on real workloads, because there was little to measure yet.

About 20 months later, Mercury 3 ships as a general-purpose reasoning dLLM. It has a 1M-token context window, up to 131,072 output tokens, four reasoning-effort levels (`instant`/`low`/`medium`/`high`), tool calling, structured outputs, and an OpenAI-compatible API. That makes a fair, reproducible comparison against traditional AR LLMs possible.

## 2. Problem statement

Vendor speed claims are measured in isolation: raw tokens/s on a single request. Practitioners need answers to different questions:

1. **Does the speed advantage survive real workloads?** That means network overhead, reasoning tokens, streaming behaviour, and quality-for-quality comparisons.
2. **Does speed compound in multi-step (agentic) workloads**, where AR latency adds up across every turn?
3. **What do you give up in quality**, and what is the cost per *successful* task, not per token?

## 3. Goals and non-goals

### Goals
- G1. Build a reusable, provider-agnostic **benchmark harness** that measures latency, throughput, quality, and cost under identical conditions.
- G2. Evaluate Mercury 3 against 2–3 speed-tier AR LLMs on **two use cases** chosen to exercise the claimed advantages of diffusion (Section 5).
- G3. Produce **publication-grade evidence** (charts, tables, an animated diffusion capture, and a failure gallery) for a follow-up blog article.
- G4. Make every published number **reproducible** from raw logged data and a pinned config.

### Non-goals
- Training, fine-tuning, or self-hosting any model.
- A general leaderboard across many tasks. We test two use cases in depth instead of many shallowly.
- Evaluating Mercury Voice, Mercury Decide, or Mercury Edit 2. FIM and next-edit are optional stretch probes only (Section 8).
- Proving *architectural* causality. We measure deployed API products, and Section 6.4 explains how we separate architecture from serving hardware.

## 4. Systems under test

| Role | Model | Notes |
|---|---|---|
| dLLM (primary) | `mercury-3` @ `instant`, `low`, `medium`, `high` | Temperature range 0.5–1.0 (out-of-range values are silently reset to 1 and return a `warning`). No `seed`. |
| dLLM (lineage, optional) | `mercury-2.5` | Shows generation-over-generation progress for the blog. |
| AR baseline A | Anthropic **Claude Haiku 5.5** (`claude-haiku-5-5`) | Anthropic's fastest tier. Effort `low`–`max`; thinking can be disabled at `high` or below. |
| AR baseline B | OpenAI **GPT-6 Luna** (`gpt-6-luna`) | OpenAI's smallest current tier. Reasoning `none`–`max`. |
| AR baseline C | Google **Gemini 3.5 Flash-Lite** (`gemini-3.5-flash-lite`) | Google's fastest stable tier. Thinking can't be fully turned off; `minimal` is the lowest. **Runs at `minimal` only** (no reasoning-on arm) to cut cost. |
| AR control (fast hardware) | **gpt-oss-120b on Cerebras** | Open-weight AR model on the fastest AR hardware (~3,000 tok/s listed). Separates *architecture* speed from *hardware* speed (Section 6.4). |

All baseline IDs, prices, and limits are pinned in `configs/models.yaml` with a "verified on" date. Never hard-code them in source.

**Selection rule (resolved 2026-10-20):** each AR baseline is the vendor's cheapest and fastest current tier, the closest match to Mercury 3 on price. At list price, Haiku 5.5 and GPT-6 Luna cost about the same as Mercury 3's regular price ($0.10 in / $0.50 out for prompts under their long-context thresholds). Flash-Lite costs more ($0.30 / $2.50). Gemini 3.8 Flash is stronger but 2–3x the price of Flash-Lite, so it is left out unless the blog needs a higher-quality Google point.

## 5. Use cases

We chose these two use cases because each one targets a different claimed advantage of diffusion, and each produces a result that readers can act on.

### UC1: Interactive code generation (speed-quality frontier)

**Scenario.** A developer asks an assistant to write a function and waits for the answer. Perceived latency matters as much as correctness. The original article named coding assistants as the ideal application, and Mercury's origin is Mercury Coder.

**Question.** At equal quality, how much faster is Mercury 3 end-to-end? At equal latency, how much better is it?

**Workload.**
- *Primary:* EvalPlus **HumanEval+** (164 problems) and **MBPP+** (378 problems). These use stronger tests than the originals.
- *Harder set (pre-cutoff):* **LiveCodeBench v6**, easy+medium, 95 problems (AtCoder stdin and LeetCode function-style, contest dates 2025-01-04 to 2025-04-06). **Decided 2026-10-20:** no public LiveCodeBench problems are newer than the models' mid-2026 training cutoffs (the lite dataset was last updated 2025-06-05), so this set is reported as harder, **not** contamination-free. Every model has the same exposure, so relative comparisons still hold.
- Output-length sweep: short (≤256 tokens), medium (≈2k), long (≈8k). Long outputs come from "implement module X with tests" prompts, to see how block decoding scales with length.

**Metrics.**
- Quality: pass@1 (mean of n=5 samples, because there is no seed), and pass@5.
- Latency: TTFT (time to first non-empty content), total wall time, and p50/p95 for each.
- Throughput: visible output tokens/s, total completion tokens/s (including reasoning), and decode tokens/s, measured from first to last chunk.
- Cost: $ per problem and **$ per solved problem**.
- Derived: a **speed-quality Pareto plot** (x = p50 wall time, y = pass@1). Each model × reasoning effort is one point.

**Success criteria (for the study, not for Mercury).**
- Every point has a 95% CI half-width of ≤3 pp on pass@1 and ≤10% on p50 latency.
- The Pareto chart can be generated from raw logs with a single command.

### UC2: Multi-step tool-calling agent (latency compounding)

**Scenario.** An analyst asks a natural-language question about a database. An agent explores the schema with tools, writes SQL, runs it, fixes errors, and answers. Each turn is a full LLM round-trip, so per-call latency multiplies. The original article predicted that diffusion would unlock agentic applications, and Inception ships a matching cookbook ("Natural Language SQL Agent").

**Question.** In an agent loop, does Mercury 3's per-call speed turn into proportionally faster *task completion* without losing accuracy or tool-call reliability?

**Workload.**
- **BIRD Mini-Dev (SQLite, 500 questions)**, stratified by difficulty (simple/moderate/challenging). Start with a 150-question stratified subset and expand if budget allows.
- One fixed agent loop shared by all models. Tools: `list_tables`, `describe_table(name)`, `run_sql(query)` (read-only, 5 s timeout, row cap), and `submit_answer(sql)`. Maximum 8 turns.
- The same system prompt and tool JSON schemas for every model. Tool calls use OpenAI-format function calling, plus native Anthropic tools for Claude.

**Metrics.**
- Quality: **execution accuracy (EX)** against the gold SQL result set, and task success rate.
- Reliability: tool-call validity rate (arguments parse and match the schema), invalid-SQL rate, rate of hitting the 8-turn cap, and malformed-turn recoveries.
- Latency: **end-to-end task wall time** (p50/p95), number of turns, and per-turn latency. The headline number is the latency ratio at the task level versus at the call level.
- Cost: tokens per task, **$ per correct answer**, and cache-hit share (`usage.prompt_tokens_details.cached_tokens`).
- Structured outputs: a sub-run where `submit_answer` uses `response_format: json_schema` (strict) to measure schema adherence.

**Success criteria (for the study).**
- Results for every model come from the identical agent loop and tool set.
- Each task produces a replayable trace (every request, response, and tool result).

## 6. Methodology requirements

### 6.1 Fairness
- R1. Identical prompts, tool definitions, `max_completion_tokens` budgets, and stop conditions across models. Any provider-specific adaptation must be documented in the run config.
- R2. **Temperature.** Mercury 3 accepts only 0.5–1.0. Primary arm: every model at 0.5 where the provider allows it, otherwise the provider default, recorded in the run config. Every response's `warning` field is logged and checked.
- R3. **Reasoning parity.** Sweep all four Mercury efforts. Run each AR baseline at its "off/minimal" setting and one "on" setting. The exception is Gemini 3.5 Flash-Lite, which runs `minimal` only because of its output price (BUDGET_ESTIMATE §7). Report reasoning tokens separately from visible tokens.
- R4. **Always set `reasoning_effort` explicitly.** The docs disagree on the Mercury 3 default: the API schema says `medium`, while the Reasoning Efforts page says `low`.

### 6.2 Measurement
- R5. Timing uses a monotonic clock around the raw HTTP stream. Record `t_request_sent`, `t_first_byte`, `t_first_content`, `t_last_content`, and `t_done`.
- R6. All latency runs use `stream: true` with `stream_options.include_usage: true`, so token counts come from the server rather than a client tokenizer.
- R7. Diffusion streaming emits **blocks**, not tokens, and `diffusing: true` emits the **full text on every step**. Throughput must be computed from server `usage` counts, never from chunk counts. Diffusing mode is used only for visualization and step-count analysis, never for latency numbers.
- R8. Control for network effects. Run everything from one fixed machine/region. Log a per-provider ping/TLS baseline at the start of each run. Interleave models in randomized order and spread runs over at least 3 time-of-day windows.
- R9. Repeats: n ≥ 5 samples per item for quality, and n ≥ 30 timing samples per configuration. Report mean, p50, p95, and bootstrap 95% CIs.

### 6.3 Reproducibility and data
- R10. Every API call is appended to `results/runs/<run_id>/calls.jsonl`. Each record holds the request (secrets redacted), response, timings, usage, model ID returned by the server, SDK version, and git SHA.
- R11. Each run directory holds a frozen copy of the resolved config and pricing table. Cost is recomputed offline from usage and prices, never from live price lookups.
- R12. Analysis runs offline from logs only. Re-running analysis never calls an API.

### 6.4 Separating architecture from infrastructure
- R13. Include a fast-hardware AR control (Section 4). The blog must state clearly that the comparison is between *products* (model + serving stack), and use the control to reason about how much of the gap comes from diffusion itself.

## 7. Constraints and assumptions

- Budget: Inception gives a one-time credit of 100M free tokens, shared across models. The pre-pilot estimate for the full plan is about 97M Mercury tokens and about $41 for AR baselines. See [BUDGET_ESTIMATE.md](BUDGET_ESTIMATE.md); it gets recalibrated after R1. Spend on AR baselines is capped by the `budget_usd` guard in each run config (default $150 total).
- Rate limits (free tier, per minute): 1,000 requests, 1M input tokens, 100k output tokens. The runner must throttle and back off on 429/503.
- Mercury 3 price (launch promo, verified 2026-10-20): $0.10 per 1M input tokens, $0.01 per 1M cached input tokens, $0.35 per 1M output tokens. Regular price: $0.20 / $0.02 / $0.70. The promo will end, so the blog reports both prices.
- Mercury models accept text input only. There is no embeddings endpoint and no `seed`.

## 8. Stretch probes (only if time allows, clearly labelled in the blog)

- P1. **Reversal / bidirectional constraint probe.** Tasks where the end is fixed and the beginning must be written to fit it (for example, "write a poem whose last line is X", or "fill in a function body given its required return statement"). This tests the claimed advantage of any-order generation.
- P2. **Denoising dynamics.** Use `diffusing: true` to count refinement steps and measure how much text changes per step for short and long answers.
- P3. **FIM / next-edit with Mercury Edit 2** against AR FIM models. This is a third use case, so it needs a separate PRD addendum.

## 9. Deliverables

1. Harness code (Python), with unit tests and live smoke tests.
2. Raw run logs and frozen configs for every number published.
3. `reports/`: Pareto chart (UC1), task-vs-call latency-ratio chart (UC2), cost-per-success table, diffusion animation GIF, and a failure-mode gallery.
4. A blog draft that follows the outline in [BLOG_ALIGNMENT.md](BLOG_ALIGNMENT.md).

## 10. Open questions

- ~~OQ1. Which exact OpenAI and Google model IDs belong in the speed tier on run day?~~ Resolved 2026-10-20: `gpt-6-luna` and `gemini-3.5-flash-lite` (Section 4). Re-verify them before the main runs.
- OQ5. gpt-oss-120b on Cerebras rejects requests that combine `tools` and `response_format`. For the UC2 strict-schema sub-run, should the control model skip that sub-run, or enforce the `submit_answer` schema through a strict tool definition instead? Decide in M3.
- OQ2. Should Mercury 2.5 be included for a lineage chart (more tokens, a stronger story)? Default is yes for UC1 only.
- OQ3. What does `realtime: true` (described as lower TTFT) trade off? Test it as an extra Mercury arm in UC1. The R0 smoke test was too small to show a difference.
- ~~OQ4. Does the docs' FAQ statement that the MCP/agent setup uses `mercury-2.5` mean Mercury 3 has tool-calling caveats?~~ Resolved 2026-10-20: `mercury-3` tool calling and strict JSON schema both work (see [M0_SMOKE_RESULTS.md](M0_SMOKE_RESULTS.md)).
