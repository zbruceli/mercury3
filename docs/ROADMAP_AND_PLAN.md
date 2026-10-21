# Roadmap and implementation plan

**Status (2026-10-21):** M0 ✅ · M1 ✅ · M2 ✅ · M3 ✅ · M4 ✅ · **M5 (blog) 🟡 draft done.** M5 results and fact-check: docs/M5_BLOG_RESULTS.md (repo public; gate open: clean-clone reproducibility check). Per-feature status: the Done column in [FEATURES.md](FEATURES.md).

This doc covers *when* (§1, the roadmap) and *how* (§2–§7, the implementation plan) to build what [FEATURES.md](FEATURES.md) lists.

| § | Contents |
|---|---|
| 1 | Roadmap: milestones, gates, risks |
| 2 | Tech stack decisions |
| 3 | Repository layout |
| 4 | Core interfaces |
| 5 | Work breakdown per milestone |
| 6 | Mercury API notes and cross-provider findings |
| 7 | M1 as built, and how it differs from the plan |
| 8 | M2 as built so far: UC1 data, prompts, sandbox, scoring |
| 9 | M3 as built: UC2 data, tools, agent loop, scoring, analysis |

## 1. Roadmap

Original plan: start the week of **2026-10-26** and finish in about 6 weeks, part-time. In practice M0 and M1 both finished on 2026-10-20, ahead of that start, so the bars below show planned durations, not dates. Each milestone ends with a gate that must pass before the next one starts, and each gate's evidence goes in a `docs/M<n>_<TOPIC>_RESULTS.md` doc ([template](templates/MILESTONE_RESULTS.md)).

```
Week:        1        2        3        4        5        6
M0 Setup   ███
M1 Core    ███████
M2 UC1          ██████████
M3 UC2                   ██████████
M4 Analysis                       ███████
M5 Blog                                 ███████
```

| Milestone | Scope | Features | Exit gate |
|---|---|---|---|
| **M0: Project setup and API verification** (wk 1) ✅ done 2026-10-20 ([results](M0_SMOKE_RESULTS.md)) | Accounts/keys for all providers; verify model IDs, prices, and limits; resolve PRD OQ1/OQ4; repo scaffolding (uv, ruff, pytest, pre-commit) | — | `configs/models.yaml` filled with `verified_on`; a manual curl to each provider works; CI-style `make check` passes on an empty package |
| **M1: Harness core** (wk 1–2) ✅ done 2026-10-20 ([results](M1_SMOKE_RESULTS.md)) | Provider layer, timing, logging, runner, budget guard, cost engine, CLI skeleton | F1.1–F1.8, F2.1–F2.5, F3.1–F3.6, F6.1–F6.3 | A1 + A2 + A4 pass; R0 smoke passes; one end-to-end mini run produces a manifest and a calls.jsonl |
| **M2: UC1 code generation** (wk 2–3) ✅ done 2026-10-21 ([results](M2_UC1_RESULTS.md)) | Datasets, sandbox, scoring, length sweep; R1 pilot, then R2 and R3 | F4.x, F6.4 (Pareto) | Pilot reviewed; R2/R3 complete; Pareto chart regenerates offline; CI targets met |
| **M3: UC2 SQL agent** (wk 3–4) ✅ done 2026-10-21 ([results](M3_UC2_RESULTS.md)) | BIRD subset, tools, agent loop, scorers, strict-schema sub-run; R4 | F5.x, F6.4 (speed-up chart) | R4 complete; traces replayable; reliability table produced |
| **M4: Analysis and assets** (wk 5) | Statistics, cost tables (promo and regular), diffusion GIF (R5), failure tagging, decision matrix | F2.6, F6.5–F6.7, F7.2 (optional) | Every item in the BLOG_ALIGNMENT asset checklist exists and passes B4 |
| **M5: Blog article** (wk 6) | Draft following the BLOG_ALIGNMENT outline; fact-check every number against its run ID; publish the repo/results | — | Every number in the draft is footnoted with a run ID; a reproducibility check is done from a clean clone |
| **Stretch** (after M5 or in parallel with M4) | Reversal probe, FIM/next-edit addendum | F7.1, F7.3 | Separate mini-report |

### Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Mercury promo pricing ends mid-project | Cost story changes | Report both prices; frozen price snapshot per run |
| Model IDs or behaviour change during the study | Results not comparable | Pin IDs; record the server-returned model; finish each use case's main run within one week |
| Rate limits (free tier: 100k output tok/min) | Slow runs | Throttle; upgrade to pay-as-you-go if needed |
| Baseline API spend overruns | Budget | Budget guard; dry-run estimates; pilot first |
| Sandbox security (executing model code) | Host compromise | Docker, no network, resource limits, non-root, read-only FS |
| Mercury tool-calling quirks (FAQ references `mercury-2.5` for MCP) | UC2 blocked | Verify in M0; fall back to documenting the limitation as a finding |

## 2. Tech stack (decisions)

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

## 3. Repository layout (target)

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

## 4. Core interfaces (sketch)

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

## 5. Work breakdown

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
3. Failure tagging: rule tags for every failure, overridden or annotated by `reports/failure_tags.csv` (manual), aggregated per model, gallery export.
4. Decision matrix and Markdown report with "as of" footnotes.

### M5: Blog (≈3 days)
Draft in `reports/blog/draft.md`, following BLOG_ALIGNMENT §4. Every number links to `results/published/<run_id>`.

## 6. Key implementation notes from the Mercury API docs (verified 2026-10-20)

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

## 7. M1 as built (2026-10-20)

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
| `probe.py` | Network baseline (F2.5): times DNS / TCP connect / TLS / TTFB of an authenticated `GET .../models` per provider, 3 samples each, run one provider at a time. The probe is free and records only timings and status, never headers |
| `cli.py` | `validate-config`, `run` (dry-run always printed; `--live` required to call APIs; `--resume`; clean-tree guard with `--allow-dirty`), `analyze` (arm table plus network baseline) |

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

**Differences from §5 (M1 work breakdown)**

- Added `experiment.py` (experiment schema) and `Message.raw` / `ChatResult.as_message()` for provider-native replay. The R0 recordings showed Gemini thought signatures, which multi-turn tool loops must echo.
- `dllm-eval smoke` was not added as a separate command. The harness smoke is an ordinary experiment (`configs/experiments/r0_smoke.yaml`); `scripts/smoke_r0.py` stays as the raw capability probe.
- `pandas` + `pyarrow` were added for F6.1 (`calls.parquet`).
- F2.5 landed after the first M1 commit, as a hand-rolled HTTP/1.1 probe over asyncio streams so each phase can be timed separately. Start and resume probes go in the manifest and `probes.jsonl`. With `network_probe_interval_s` set, the runner re-probes in the background, and a failed probe never stops a run.
- Clean-tree guard: `run --live` refuses a dirty or non-git tree unless `--allow-dirty` is passed. The manifest records `git.allow_dirty`.
- **Not done:** F1.9, the `inceptionai` SDK cross-check (P2).

**Tests.** 127 offline tests, 96% coverage; providers are 95–100% and `probe.py` 96%. An autouse fixture keeps every test offline: a clean git tree, and `probe_all` is a no-op unless a test overrides it.
- Contract tests replay every R0 recording through the production parsers.
- The I/O-path tests drive the real adapters through a stub SDK client.
- The fake-provider tests cover the runner and CLI: interleaving, resume, the budget guard, and aborts on warnings and billing errors.

## 8. M2 as built so far (2026-10-20)

**Modules**

| Module | What it does |
|---|---|
| `configs/datasets.yaml` + `usecases/codegen/datasets.py` | Pinned dataset registry (url, bytes, sha256) and loaders for HumanEval+ v0.1.10, MBPP+ v0.2.0, and LiveCodeBench v6. `dllm-eval fetch-data` downloads and verifies. LCB private tests are pickled, so a restricted unpickler refuses every global |
| `usecases/codegen/prompts.py` | Frozen `uc1-v1` templates (function / stdin / LeetCode). A test pins their sha256, so editing them in place fails `make check` |
| `usecases/codegen/extract.py` | Picks the last fenced block that defines the entry point. Also handles truncated fences, unfenced code, and none. Extraction failures are counted separately from wrong answers |
| `usecases/codegen/workload.py` + `experiment.CodegenWorkload` | `kind: codegen` workloads with a seeded `sample` or explicit `ids` per dataset. The runner resolves them into prompt items |
| `sandbox/harness.py` | Runs inside the container, stdlib only. Each solution runs in its own subprocess with a time limit and a 768 MB address-space cap; outputs come back as tagged JSON (never pickle) |
| `sandbox/executor.py` | `DockerExecutor`: digest-pinned `python:3.12-slim`, `--network none`, read-only root, noexec `/tmp`, non-root, all capabilities dropped. 20 jobs per container, 3 containers in parallel; a dead container's jobs are re-run one per container. `HostExecutor` exists for tests only |
| `usecases/codegen/scoring.py` + `dllm-eval score` | Run log → extraction → sandbox → `results/analysis/<run>/scores.jsonl` (resumable) → pass@1 / pass@5 per arm and dataset |
| `configs/experiments/r1_pilot_uc1.yaml`, `r2_uc1_main.yaml`, `r3_uc1_length.yaml` | R1: 390 calls, ~$0.31 expected. R2: 41,405 calls, ~$50 expected. R3: 630 calls, ~$1.67 expected |

**Grading fidelity (EvalPlus)**

The first sandbox run had 26 canonical solutions failing against themselves. All were grader gaps:
- Missing special oracles: set-equality tasks, not-None tasks, alternative-answer tasks, and HumanEval `find_zero`.
- Missing MBPP+ per-task input deserialization (JSON lists back to tuples, sets, and complex numbers).
- Ints over 4,300 digits.
- Huge outputs filling `/tmp`.
- One OOM-killed container taking 19 results with it.

The fixes mirror EvalPlus's own code (`evalplus/eval/__init__.py`, `_special_oracle.py`, `data/mbpp.py`), including its carried-over float tolerance.

One rule is ours: an input counts only if the reference passes it. That drops 7 of 888 HumanEval/32 inputs (float error) and 1 of 112 Mbpp/255 inputs (memory). A problem left with no valid inputs is a harness error, never a pass.

Result: **542/542 canonical solutions pass in Docker**. All 3,866 LiveCodeBench tests are well formed (LCB has no reference solutions to grade).

**Decisions**
- **LiveCodeBench is pre-cutoff** (PRD UC1). No public post-cutoff release exists.
- **Every arm uses `max_completion_tokens` 32,768** (PRD R1).
- **Temperature 0.5** where accepted (PRD R2). Claude Haiku 5.5 rejects non-default sampling values. GPT-6 Luna is untested, so both run at provider default.
- **Scoring is a separate offline step**, so grading never mixes with timing and can be re-run without API calls.
- **Docker Desktop's CLI lives in `~/.docker/bin`.** The executor finds it there when it isn't on the PATH.

**Not done yet:** R1 pilot → review → recalibrate BUDGET_ESTIMATE → R2 and R3 → Pareto chart (F6.4). That's the M2 gate, recorded in `docs/M2_UC1_RESULTS.md` when it runs.

## 9. M3 as built (2026-10-21)

**Modules** (under `src/dllm_eval/usecases/sql_agent/` unless noted)

| Module | What it does |
|---|---|
| `configs/datasets.yaml` (`bird-minidev`) + `datasets.py` | The official `minidev.zip` (801 MB) pinned by size and sha256. `fetch-data` extracts only `mini_dev_sqlite.json` and the 11 `.sqlite` files, to paths we build ourselves (crafted member names can't escape). The release lists question_id 137 and 138 twice, verbatim, so 500 rows give **498 questions** |
| `datasets.GOLD_EXCLUDED` | `bird/518` and `bird/701`: their gold SQL doesn't finish in 60 s on the read-only connection (the other 496 take ≤ 2.1 s), so they can't be scored and are never sampled |
| `dllm-eval sample-bird` | Difficulty-stratified sample, proportional with largest remainder, seeded per stratum. Committed lists: [configs/uc2/bird_r4_150.txt](../configs/uc2/bird_r4_150.txt) (45 simple, 75 moderate, 30 challenging; seed 2026) and [bird_pilot_10.txt](../configs/uc2/bird_pilot_10.txt) (3/5/2, drawn from the 150) |
| `tools.py` | `list_tables`, `describe_table` (CREATE statement + 3 example rows), `run_sql` (5 s, 50 rows shown), `submit_answer`. `ReadOnlyDB` stacks `mode=ro`, `PRAGMA query_only`, an authorizer (no ATTACH/DETACH, no writes, only read-only PRAGMAs), no attached databases, and a progress-handler deadline. Tool-call validity (F5.5): known tool, arguments parse as JSON, and match the schema (a small JSON Schema subset, so no new dependency) |
| `prompts.py` | Frozen `uc2-v1` system/user prompt and tool schemas; a test pins their sha256. BIRD's `evidence` goes in the user message as "Hint" (standard BIRD setting) |
| `agent.py` | One loop for every model, max 8 model calls. Appends `ChatResult.as_message(provider_key)` each turn. Tool calls run in worker threads so SQL never blocks the event loop timing other streams. Text next to tool calls is kept (Mercury preambles). A reply with no tool call gets one fixed nudge. Outcomes: submitted, turn_cap, budget_out (any turn stopping at the cap; PRD R1), empty_response, invalid_answer (strict sub-run), error |
| `runner.py` | A `sql_agent` job is a whole task: one `calls.jsonl` record with the full trace (every turn's result, tool calls, validity, tool results, and the final message history). The provider semaphore is held for the task. Budget is checked between tasks |
| `scoring.py` + `compare.py` (`dllm-eval score`) | Runs gold (60 s) and predicted (30 s, BIRD's limit) SQL read-only. **EX** (primary): multiset of rows, ordered when the gold query has a top-level ORDER BY, floats rounded to 6 decimals, NULL = NULL. **EX (BIRD rule)**: `set(pred) == set(gold)`, reported for comparability |
| `trace.py` (`dllm-eval trace`) | Pretty-prints a task. `--verify [--all]` rebuilds each turn's history and re-runs every recorded tool call on the database; the results must match byte for byte |
| `analysis/uc2.py` (`analyze`, `score`, `charts`) | Task and turn tables, accuracy and reliability tables, $ per correct answer (promo and regular), and the speed-up chart: median per-call and per-task latency of each arm relative to `mercury-3:low`, with bootstrap CIs |

**Decisions**
- **Strict-schema sub-run (PRD OQ5): Cerebras skips it.** gpt-oss-120b on Cerebras refuses `tools` + `response_format` in one request, and a strict tool definition would measure something else. `models.yaml` records it as `tools_with_structured_outputs: false`, and the experiment check refuses such an arm.
- **Same per-turn output cap as UC1 (32,768)** for every arm (PRD R1). A turn that stops at the cap ends the task as budget-out.
- **Latency eligibility per task:** finished, not warm-up, no retried turn, not across a pause. Per-call latency uses the turns of those same tasks, so both ratios cover the same work.
- **Tool time is part of task time.** Local SQL execution is the same for every model, which dilutes the task-level ratio toward 1. The task record keeps `model_s` and `tool_s` separately.

**Live runs:** UC2 pilot, R4 (4,500 tasks, $6.42), and R4b (600 tasks, $0.61), all recorded in [M3_UC2_RESULTS.md](M3_UC2_RESULTS.md). The pilot found two Gemini stream-parsing bugs (parallel tool calls at index 0 or `null`), now fixed, and GPT-6 Luna's tools-only-at-`none` limit, which removed the Luna `medium` arm from UC2.

**Checks:** `make check` (offline: fake provider, scripted multi-turn turns, a SQLite fixture, a crafted archive) and `make test-bird` (real data: all 150 sampled gold queries return rows and score EX = 1 against themselves; 38 of them are order-sensitive).

