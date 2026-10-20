# M0 / R0 live smoke results

| | |
|---|---|
| Run | 2026-10-20, one pass from the project laptop; GPT-6 Luna re-run after OpenAI credits were added |
| Script | [scripts/smoke_r0.py](../scripts/smoke_r0.py) (`make smoke-live`; spends tokens) |
| Recordings | [tests/fixtures/recordings/r0/](../tests/fixtures/recordings/r0/) (31 files; no credentials, scanned automatically), plus [errors/openai_429_insufficient_quota.json](../tests/fixtures/recordings/errors/openai_429_insufficient_quota.json) |
| Tokens used | **~3.4k total** (budget was < 50k) |
| Result | **30 / 30 checks passed.** GPT-6 Luna first failed on an empty OpenAI balance, then passed after credits were added |

> These are single samples on trivial prompts. The latencies below confirm the plumbing works; they are **not results** and must not be quoted in the blog.

## 1. Check matrix

| Model | plain | stream | tool call | strict JSON schema | extras |
|---|:-:|:-:|:-:|:-:|---|
| `mercury-3` | ✅ | ✅ | ✅ | ✅ | ✅ all 4 efforts · ✅ diffusing · ✅ temperature warning · ✅ `realtime` |
| `mercury-2.5` | ✅ | ✅ | — | — | (lineage model: plain and stream only) |
| `claude-haiku-5-5` | ✅ | ✅ | ✅ | ✅ | ✅ medium effort + adaptive thinking |
| `gpt-6-luna` | ✅ | ✅ | ✅ | ✅ | `reasoning_effort: none` → 0 reasoning tokens. First attempt failed with 429 `credit_balance_exhausted` (see F1) |
| `gemini-3.5-flash-lite` | ✅ | ✅ | ✅ | ✅ | — |
| `gpt-oss-120b-cerebras` | ✅ | ✅ | ✅ | ✅ | — |

## 2. Open questions resolved

- **PRD OQ4 (Mercury 3 tool calling): resolved, works.** `mercury-3` returned a valid OpenAI-format `tool_calls` entry (`get_weather`, `{"city": "Paris"}`), with `finish_reason: "tool_calls"`. It also emits a short preamble sentence before the call; the UC2 agent loop must tolerate text alongside tool calls.
- **Strict JSON schema works on all six models.** Every output parsed with exactly the required keys.

## 3. Findings that change the harness design

| # | Finding | Impact | Where to handle |
|---|---|---|---|
| F1 | **OpenAI returns HTTP 429 for an empty balance** (`type: insufficient_quota`, `code: credit_balance_exhausted`). That is the same status code as a real rate limit, so the SDK retried it. | A naive "retry 429" policy loops forever on a billing problem. | M1 `providers/base.py`: treat `insufficient_quota` as fatal. Contract test from `recordings/errors/openai_429_insufficient_quota.json` |
| F2 | **Mercury's temperature reset is confirmed**, with this exact text: `"Warning: Requested temperature 0.2 is not within [0.5, 1]. Temperature has been set to 1."` (top-level `warning` field). | Confirms PRD R2. A wrong temperature would silently change Mercury's arm. | M1: fail the run on any unexpected `warning` |
| F3 | **Gemini's OpenAI-compatible endpoint reports no `completion_tokens_details` or `prompt_tokens_details`.** Reasoning tokens and cached tokens are not visible. | We can't separate visible from reasoning tokens for Gemini (PRD R3), and the cost engine can't see cache hits. This is minor while Gemini runs only at `minimal`. | M1: record as unknown (not 0). Cost assumes no cache, which is conservative. Consider the native Gemini API if it matters |
| F4 | **gpt-oss-120b adds about 64 prompt tokens of fixed overhead** (74 prompt tokens for a 10-token message), and uses reasoning tokens even at `low` (9–21 per call). | Small fixed bias in input tokens and TTFT. Report it; don't "correct" it. | Analysis note |
| F5 | **Haiku reports thinking separately** in `usage.output_tokens_details.thinking_tokens`. | Lets us report reasoning tokens for Claude without estimating them. | M1 Anthropic adapter usage mapping |
| F6 | **Mercury reports `cached_tokens` even on tiny prompts** (9 of 285 on the tool call), and `reasoning_tokens` at every effort. | Mercury's usage data is complete. | — |
| F7 | **Mercury streams very few chunks** (3 chunks for a 5-token answer), consistent with block decoding. | Confirms PRD R7: throughput must come from `usage`, not chunk counts. | Already in CLAUDE.md |

## 4. Diffusing mode (blog asset preview, BLOG_ALIGNMENT C2)

Prompt (same style as the original article's animation): *write a Python function that splits an image into left and right halves.*

- 755 completion tokens. 10 text frames between 0.74 s and 1.38 s after the request.
- **The canvas grows in about five extensions** (949 → 1,711 → 2,439 → 2,886 → 3,118 → 3,127 characters). **The whole canvas keeps changing**: even the opening words stayed noisy (`"Here's a C%5KpZ ;u;fH;oG ruat splits..."`) until late frames.
- So it refines the whole text while extending it. It is not a left-to-right fill. This is good material for the follow-up article's "what changed since 2025" and GIF sections.
- Caveat: the diffusing stream is a visualization the API provides. We should not claim it shows the model's literal internal denoising steps without confirmation from Inception.

## 5. Not conclusive yet (needs real samples in R1/R3)

- **Reasoning-effort ordering.** On "17 × 23", `low` used 56 reasoning tokens, `medium` 13, and `high` 19. On a trivial prompt that is noise, not a trend.
- **PRD OQ3 (`realtime: true`).** TTFT was 0.274 s with it and 0.268 s without, on a 5-token answer, which is too small to show anything. Keep the R3 arm.
- **Latency comparisons between models.** These are single calls, so ignore them.

## 6. Actions

1. ~~**You:** add credits to the OpenAI account, then re-run Luna.~~ Done 2026-10-20, 4/4 passed.
2. **M1:** implement F1 (fatal `insufficient_quota`), F2 (fail on `warning`), F3 (unknown-not-zero usage fields), and F5 (thinking-token mapping). Turn these recordings into contract tests (TEST_PLAN A2).
