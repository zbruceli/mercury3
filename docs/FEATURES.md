# Features list

Priority: **P0** = required for the blog's headline results, **P1** = strongly wanted, **P2** = stretch.
IDs are stable. Reference them in commits, tests, and the implementation plan.

## F1 Provider layer
| ID | Feature | Pri |
|---|---|---|
| F1.1 | `Provider` interface: `chat(request) -> stream of Chunk` plus a final `Usage`. Every provider maps into one shared `ChatRequest` / `ChatResult` schema | P0 |
| F1.2 | Inception adapter (`mercury-3`, `mercury-2.5`) using the OpenAI SDK with `base_url=https://api.inceptionlabs.ai/v1`. Passes `reasoning_effort`, `diffusing`, and `realtime` through `extra_body` | P0 |
| F1.3 | OpenAI-compatible adapter (OpenAI, Gemini OpenAI-compat endpoint, Cerebras control) | P0 |
| F1.4 | Anthropic adapter (Claude Haiku 5.5) using the native SDK, with native tool use | P0 |
| F1.5 | Normalised tool-call representation across OpenAI and Anthropic formats | P0 |
| F1.6 | Retry with exponential backoff and jitter on 429/500/502/503. Retries are logged and excluded from latency stats | P0 |
| F1.7 | Client-side rate limiter (requests/min, input and output tokens/min) per provider, from config | P0 |
| F1.8 | Capture response `warning` fields (e.g. Mercury temperature reset) | P0 |
| F1.9 | Cross-check adapter using the official `inceptionai` SDK (smoke test only) | P2 |

## F2 Measurement and logging
| ID | Feature | Pri |
|---|---|---|
| F2.1 | Monotonic-clock timing: request sent, first byte, first content, last content, done | P0 |
| F2.2 | Server-reported usage (`prompt_tokens`, `cached_tokens`, `completion_tokens`, `reasoning_tokens`) through `stream_options.include_usage` | P0 |
| F2.3 | Append-only JSONL call log per run, with secrets redacted | P0 |
| F2.4 | Run manifest: resolved config, pricing snapshot, git SHA, SDK versions, host/region, and start/end time | P0 |
| F2.5 | Network baseline probe per provider (DNS + TLS + minimal request) at run start | P1 |
| F2.6 | Diffusing-mode capture: store every intermediate full-text frame with a timestamp | P1 |

## F3 Experiment runner
| ID | Feature | Pri |
|---|---|---|
| F3.1 | YAML experiment configs (models × params × dataset slice × repeats), validated with pydantic | P0 |
| F3.2 | Randomised interleaving of (model, item) jobs; bounded concurrency per provider | P0 |
| F3.3 | Resumable runs: skip jobs already completed in `calls.jsonl` | P0 |
| F3.4 | Budget guard: estimate cost before the run, stop when `budget_usd` is reached | P0 |
| F3.5 | `--dry-run` that prints the job matrix, estimated tokens, and estimated cost | P0 |
| F3.6 | CLI: `dllm-eval run <config>`, `dllm-eval analyze <run_id>`, `dllm-eval smoke` | P0 |

## F4 UC1: code generation
| ID | Feature | Pri |
|---|---|---|
| F4.1 | Dataset loaders: HumanEval+, MBPP+ (EvalPlus); LiveCodeBench subset filtered by release date | P0 |
| F4.2 | Prompt template plus code extraction from markdown fences (robust to extra prose) | P0 |
| F4.3 | Sandboxed test execution (Docker, no network, CPU/memory/time limits) | P0 |
| F4.4 | pass@1 / pass@k with an unbiased estimator | P0 |
| F4.5 | Output-length sweep prompt set (short/medium/long) | P1 |
| F4.6 | `realtime: true` arm for Mercury | P1 |

## F5 UC2: SQL tool-calling agent
| ID | Feature | Pri |
|---|---|---|
| F5.1 | BIRD Mini-Dev SQLite loader plus a stratified subset sampler | P0 |
| F5.2 | Tools: `list_tables`, `describe_table`, `run_sql` (read-only connection, timeout, row cap), `submit_answer` | P0 |
| F5.3 | Model-agnostic agent loop (max 8 turns), with per-turn timing and full trace | P0 |
| F5.4 | Execution-accuracy scorer (result-set comparison, order-insensitive unless ORDER BY) | P0 |
| F5.5 | Tool-call validity checker (JSON parse + JSON Schema validation) | P0 |
| F5.6 | Strict `response_format: json_schema` sub-run for `submit_answer` | P1 |
| F5.7 | Trace replay viewer (CLI pretty-print of a task trace) | P1 |

## F6 Analysis and reporting
| ID | Feature | Pri |
|---|---|---|
| F6.1 | Offline loader: calls.jsonl → pandas/Parquet tables | P0 |
| F6.2 | Statistics: mean, p50, p95, bootstrap 95% CI | P0 |
| F6.3 | Cost engine: tokens × frozen price table (promo and regular), $ per item and per success | P0 |
| F6.4 | Charts: Pareto (UC1), task-vs-call speed-up (UC2), latency CDF, cost-per-success | P0 |
| F6.5 | Diffusion GIF renderer from F2.6 frames | P1 |
| F6.6 | Failure-mode tagging (manual tag file + aggregation) and failure gallery export | P1 |
| F6.7 | Markdown report generator with dated "as of" footnotes | P1 |

## F7 Stretch probes
| ID | Feature | Pri |
|---|---|---|
| F7.1 | Reversal / bidirectional-constraint probe set and scorer (PRD P1) | P2 |
| F7.2 | Denoising dynamics: step count, edit distance between frames (PRD P2) | P2 |
| F7.3 | FIM / next-edit with Mercury Edit 2 (PRD P3, needs its own addendum) | P2 |
