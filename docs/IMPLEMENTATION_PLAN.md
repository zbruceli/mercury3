# Implementation plan

Status: **planned, not started.** This file describes *how* to build what [FEATURES.md](FEATURES.md) lists, in the order given in [ROADMAP.md](ROADMAP.md).

## 1. Tech stack (decisions)

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Every eval dataset and SDK we need is Python-first |
| Env / deps | `uv` + `pyproject.toml`, lockfile committed | Fast, reproducible installs |
| HTTP / SDKs | `openai` SDK for all OpenAI-compatible providers **including Mercury**; `anthropic` SDK for Claude | One code path for most providers keeps SDK overhead out of the comparison. Mercury-only params go in `extra_body`. The `inceptionai` SDK is used only as a smoke cross-check |
| Not chosen | LiteLLM / LangChain for the measured path | Extra layers that hide chunk timing and add overhead. Fine for prototyping, but not for timing |
| Config | YAML + `pydantic` v2 models | Validated, typed, and diffable |
| CLI | `typer` | Simple |
| Data | JSONL (raw, append-only) → Parquet (derived) with `pandas`/`pyarrow` | Raw stays human-readable; analysis stays fast |
| Charts | `matplotlib` (+ `seaborn` optional), GIF with `Pillow` | Static, publishable output |
| Sandbox | Docker (`python:3.12-slim`, `--network none`, cpu/mem/pids limits, read-only rootfs, tmpfs work dir) | Safe execution of model-generated code |
| Quality | `ruff` (lint + format), `mypy --strict` on `src/`, `pytest`, `pytest-cov`, `pre-commit` | Standard |

## 2. Repository layout (target)

```
mercury3/
├── CLAUDE.md
├── README.md
├── pyproject.toml / uv.lock
├── Makefile                      # check, test, test-live, smoke, fmt
├── .env.example                  # INCEPTION_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, CEREBRAS_API_KEY
├── configs/
│   ├── models.yaml               # model ids, endpoints, prices, limits, verified_on
│   └── experiments/
│       ├── r0_smoke.yaml
│       ├── r1_pilot.yaml
│       ├── r2_uc1_main.yaml
│       ├── r3_uc1_length.yaml
│       ├── r4_uc2_main.yaml
│       └── r5_diffusion_capture.yaml
├── src/dllm_eval/
│   ├── cli.py
│   ├── config.py                 # pydantic config models
│   ├── schemas.py                # ChatRequest, Chunk, ChatResult, Usage, ToolCall, Timing
│   ├── providers/
│   │   ├── base.py               # Provider protocol, retry, rate limit
│   │   ├── openai_compat.py      # OpenAI, Gemini-compat, Cerebras
│   │   ├── inception.py          # subclass of openai_compat: extra_body params, diffusing frames
│   │   └── anthropic.py
│   ├── timing.py
│   ├── logging_store.py          # calls.jsonl writer, manifest, redaction
│   ├── runner.py                 # job matrix, interleave, concurrency, resume, budget guard
│   ├── cost.py
│   ├── stats.py
│   ├── usecases/
│   │   ├── codegen/              # datasets.py, prompts.py, extract.py, sandbox.py, score.py
│   │   └── sql_agent/            # datasets.py, tools.py, agent.py, score.py
│   ├── analysis/                 # load.py, tables.py, charts.py, gif.py, report.py
│   └── probes/                   # stretch: reversal.py, denoise.py
├── tests/{unit,contract,live,fixtures}/
├── data/                         # gitignored: downloaded datasets
├── results/runs/<run_id>/        # gitignored by default; publishable runs copied to results/published/
└── reports/                      # generated charts, tables, blog assets
```

## 3. Core interfaces (sketch)

```python
class ChatRequest(BaseModel):
    model_key: str                       # key into models.yaml, not the raw id
    messages: list[Message]
    max_completion_tokens: int
    temperature: float | None = None
    reasoning_effort: Literal["instant","low","medium","high"] | None = None
    tools: list[ToolSpec] | None = None
    tool_choice: str | dict | None = None
    response_format: dict | None = None
    stream: bool = True
    provider_extras: dict = {}           # e.g. {"diffusing": True, "realtime": True}

class Timing(BaseModel):
    t_sent: float; t_first_byte: float | None; t_first_content: float | None
    t_last_content: float | None; t_done: float
    # derived: ttft, decode_s, total_s

class ChatResult(BaseModel):
    content: str; tool_calls: list[ToolCall]; finish_reason: str | None
    usage: Usage; timing: Timing; server_model: str
    warnings: list[str]; retries: int; frames: list[Frame] | None  # diffusing only

class Provider(Protocol):
    async def chat(self, req: ChatRequest) -> ChatResult: ...
```

Every use case depends only on `Provider.chat`. Nothing outside `providers/` knows which vendor it is talking to.

## 4. Work breakdown

### M0: Setup (≈1 day)
1. `git init`, `uv init --package dllm_eval`, add dev tooling, Makefile, pre-commit, `.env.example`.
2. Create provider accounts and keys; put them in `.env` (never committed).
3. Fill in `configs/models.yaml` from the vendors' docs, with a `verified_on` date. PRD OQ1 (baseline IDs) resolved 2026-10-20.
4. Manually curl each provider. Check Mercury tool calling and strict JSON schema on `mercury-3` (PRD OQ4). Save the raw responses as contract fixtures.

### M1: Harness core (≈4 days)
1. `schemas.py`, `config.py`, and their unit tests.
2. `timing.py` with an injectable clock.
3. `providers/openai_compat.py`: streaming parse, usage chunk, tool-call delta assembly, retry/backoff, rate limiter.
4. `providers/inception.py`: `extra_body` mapping; diffusing-frame capture (each chunk replaces the text; store frames with timestamps); `warning` capture.
5. `providers/anthropic.py`: message/tool mapping, streaming events, usage mapping (cache read tokens → `cached_tokens`).
6. `logging_store.py`: JSONL writer, manifest, redaction of `Authorization` and keys.
7. `runner.py`: job matrix expansion, seeded shuffle, per-provider semaphores, resume, budget guard, dry-run.
8. `cost.py`, `stats.py`; `cli.py` with `smoke`, `run`, `analyze`.
9. Exit: A1/A2/A4 green, R0 smoke done.

### M2: UC1 (≈5 days)
1. Loaders for EvalPlus (HumanEval+, MBPP+) and a LiveCodeBench subset filtered by date. Cache under `data/`.
2. Frozen prompt template (`prompts.py`, version-stamped). Code extraction.
3. Docker sandbox runner with timeouts; parallel execution pool.
4. pass@k scorer. Join quality with timing per call.
5. Length-sweep prompt set.
6. R1 pilot → review → size n → R2, R3.
7. Pareto chart.

### M3: UC2 (≈5 days)
1. BIRD Mini-Dev SQLite download plus stratified sampler (fixed seed, list of IDs committed).
2. Tools with a read-only `sqlite3` URI connection (`mode=ro`), statement timeout through a progress handler, and a row cap.
3. Agent loop: one function per turn; normalised tool calls; per-turn `ChatResult` stored in the trace.
4. EX scorer; tool-validity checker; strict-schema sub-run.
5. R4 run; trace replay CLI; speed-up chart (task-level ratio vs. median per-call ratio).

### M4: Analysis and assets (≈4 days)
1. Cost tables (promo and regular), latency CDFs, reliability table.
2. R5 diffusion capture → GIF renderer (one frame per diffusion step, text overwritten in place to match the original article's animation style).
3. Failure tagging: `reports/failure_tags.csv` (manual), aggregated per model, gallery export.
4. Decision matrix and Markdown report with "as of" footnotes.

### M5: Blog (≈3 days)
Draft in `reports/blog/draft.md`, following BLOG_ALIGNMENT §4. Every number links to `results/published/<run_id>`.

## 5. Key implementation notes from the Mercury API docs (verified 2026-10-20)

- Base URL `https://api.inceptionlabs.ai/v1`; auth `Authorization: Bearer $INCEPTION_API_KEY`.
- Endpoints: `/chat/completions` (Mercury 3), `/fim/completions` and `/edit/completions` (Mercury Edit 2), `/models`.
- `mercury-3`: 1M context, max output 131,072. `max_completion_tokens` includes reasoning tokens. When it is unset, the default depends on effort (instant 8,192; low 24,576; medium 65,536; high 131,072), so **always set it explicitly** for comparable runs.
- `temperature` range 0.5–1.0, default 1. Out-of-range values are silently reset and a `warning` is returned. No `seed`.
- `reasoning_effort`: instant/low/medium/high. The docs disagree on the default (low vs. medium), so always set it.
- Streaming emits *blocks*. `diffusing: true` (with `stream: true`) sends the **full current text in each chunk**, so do not concatenate deltas in that mode.
- `stream_options: {"include_usage": true}` adds a final usage chunk. `usage.completion_tokens` already includes `reasoning_tokens`, and `prompt_tokens` already includes `cached_tokens`.
- Extra flags: `realtime` (lower TTFT), `reasoning_summary`, `reasoning_summary_wait`.
- Errors: 429 rate limit and 503 engine overloaded are retryable; 400 `context_length_exceeded`; 402 billing.

### Cross-provider findings from R0 (2026-10-20, see M0_SMOKE_RESULTS.md)

- OpenAI signals an empty balance as **429 `insufficient_quota`**. Treat it as fatal, not as a rate limit.
- The Gemini OpenAI-compatible endpoint returns no `completion_tokens_details` or `prompt_tokens_details`. Record reasoning and cached tokens as unknown, not 0.
- Anthropic reports thinking in `usage.output_tokens_details.thinking_tokens`. Map it to `reasoning_tokens`.
- gpt-oss-120b on Cerebras adds about 64 fixed prompt tokens and spends reasoning tokens even at `low`.
- Free tier per minute: 1,000 requests, 1M input tokens, 100k output tokens. 100M free tokens one-time.
- Full doc index for agents: https://docs.inceptionlabs.ai/llms.txt; OpenAPI: https://api.inceptionlabs.ai/openapi.json.

## 6. M1 as built (2026-10-20)

**Modules** (all under `src/dllm_eval/`)

| Module | What it does |
|---|---|
| `schemas.py` | `ChatRequest` / `ChatResult` / `Message` / `ToolCall` / `Usage` / `Timing` / `Frame`. `Usage` fields are `None` when the provider did not report them, never a silent 0 (M0 F3) |
| `experiment.py` | Experiment YAML: arms (model + fixed settings), prompt workload, repeats, seed, budget, concurrency, `fail_on_warning`, warm-up, `record_raw`. `check_against(registry)` rejects unsupported settings before anything runs |
| `timing.py` | `Stopwatch`, all marks relative to send. `t_first_byte` = response headers received (the SDK returns the stream object then). TTFT = first non-empty text or tool-call delta; thinking deltas don't count |
| `providers/base.py` | Validation, error classification, full-jitter retries (SDK retries off, so every retry is counted), and a sliding-window rate limiter shared per provider |
| `providers/openai_compat.py`, `inception.py`, `anthropic.py` | Pure stream assemblers over dumped dicts, plus thin I/O. Mercury knobs go in `extra_body`; Gemini uses `max_tokens` (`budget_param` in models.yaml); Anthropic tool results are merged into one user message |
| `providers/fake.py` | Deterministic offline provider with scripted failures/warnings/tool calls |
| `logging_store.py` | Run dir: `manifest.json` (config, frozen pricing, git SHA + dirty flag, package versions) and append-only `calls.jsonl`, with key and secret-value redaction |
| `runner.py` | Seeded interleaved job matrix, per-provider semaphores, resume, budget guard, dry-run |
| `cost.py`, `stats.py` | Frozen-price cost (long-context tier, promo vs regular, unknown cache = uncached); percentiles + seeded bootstrap CIs |
| `analysis/` | `calls.jsonl` -> DataFrame -> per-arm table; latency stats exclude warm-up and retried calls |
| `cli.py` | `validate-config`, `run` (dry-run always printed; `--live` required to call APIs; `--resume`), `analyze` |

**Error policy.** Error kinds are retry / call-fatal / run-fatal:

| Status | Kind | Runner action |
|---|---|---|
| 408, 409, 429, 5xx, 529, connection errors | retry | Retry with backoff; becomes call-fatal after 5 attempts |
| 429 with `insufficient_quota` | run-fatal | Abort the run (M0 F1) |
| 401, 402, 403, 404 | run-fatal | Abort the run |
| Other 4xx | call-fatal | Record and continue |

A provider `warning` aborts the run when `fail_on_warning` is set (M0 F2). A request-validation error or an unexpected exception also aborts it, so a harness bug doesn't burn budget.

**Resume semantics.** A job counts as done only when its latest record has `status: ok`. Anything else is re-run and appended, and analysis uses the latest record per job. Resuming with a changed experiment config is refused.

**Budget guard.** The runner stops launching jobs once actual spend reaches `budget_usd`. In-flight calls can overshoot by at most concurrency x one call's cost.

**Differences from §4 (M1 plan)**

- Added `experiment.py` (experiment schema) and `Message.raw` / `ChatResult.as_message()` for provider-native replay. The R0 recordings showed Gemini thought signatures, which multi-turn tool loops must echo.
- `dllm-eval smoke` was not added as a separate command. The harness smoke is an ordinary experiment (`configs/experiments/r0_smoke.yaml`); `scripts/smoke_r0.py` stays as the raw capability probe.
- `pandas` + `pyarrow` were added for F6.1 (`calls.parquet`).
- **Not done (carried over):** F2.5, the network baseline probe (P1); F1.9, the `inceptionai` SDK cross-check (P2).

**Tests.** 107 offline tests, 96% coverage; providers are 95–100%.
- Contract tests replay every R0 recording through the production parsers.
- The I/O-path tests drive the real adapters through a stub SDK client.
- The fake-provider tests cover the runner and CLI: interleaving, resume, the budget guard, and aborts on warnings and billing errors.
