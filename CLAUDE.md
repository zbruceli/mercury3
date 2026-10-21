# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What this project is

A benchmark harness that compares Inception Labs **Mercury 3** (a diffusion LLM) against autoregressive LLMs on two use cases:
- **UC1:** code generation, measuring the speed-quality frontier.
- **UC2:** a multi-step SQL tool-calling agent, measuring how latency compounds across turns.

The results feed a follow-up blog article. The output that matters is **trustworthy, reproducible numbers**, not a product.

Read these first, in order:
1. [docs/PRD.md](docs/PRD.md): goals, use cases, methodology rules (R1–R13)
2. [docs/ROADMAP_AND_PLAN.md](docs/ROADMAP_AND_PLAN.md): roadmap and milestone gates (§1), then stack, layout, interfaces, work breakdown, Mercury API notes, and M1 as built (§2–§7)
3. [docs/TEST_PLAN.md](docs/TEST_PLAN.md): what must pass before a number is publishable
4. [docs/FEATURES.md](docs/FEATURES.md): feature IDs and their Done status
5. [docs/BLOG_ALIGNMENT.md](docs/BLOG_ALIGNMENT.md): how results map onto the article

Current phase: **M2 (UC1 code generation) in progress (2026-10-20):** datasets, prompts, Docker sandbox, and scoring are built and validated (542/542 canonical solutions pass); the R1 pilot ran 2026-10-21 (390/390, $0.29; docs/M2_UC1_RESULTS.md); R2 keeps the uniform 32,768 cap and reports budget-out as a failure (M2-F1); the R1b cap probe confirmed it (131,072 rescued 1 of 12 calls). R2 ran 2026-10-21: 41,405/41,405, $23.37, scored and charted (docs/M2_UC1_RESULTS.md §6). R3 (length sweep) ran 2026-10-21 (630/630, $2.03). **M2 complete. M3 complete (2026-10-21):** UC2 pilot, R4 (4,500 agent tasks, $6.42) and R4b strict sub-run ($0.61) ran, scored, and charted (docs/M3_UC2_RESULTS.md). **M4 (analysis and assets) in progress (2026-10-21):** `dllm-eval report` builds every offline blog asset into `reports/` from configs/report.yaml (figures 1–4 with CSVs, reliability table, failure modes and gallery, decision matrix); the diffusion GIF needs the R5 live run (docs/M4_ANALYSIS_RESULTS.md). Previously: **M1 complete.** The harness core passes 135 offline tests and its live smoke (`r0_smoke.yaml`, 24/24). F2.5 (network baseline probe) and the clean-tree guard are in. Feature status lives in the **Done** column of docs/FEATURES.md. See ROADMAP_AND_PLAN §7 for what was built and how it differs from the plan.

## Commands

```bash
uv sync                      # install (Python 3.12, pinned in .python-version)
make check                   # ruff + mypy + offline tests + validate-config
make fmt                     # ruff format + autofix
make test-live               # live smoke tests: spends real tokens, run only when asked
uv run dllm-eval validate-config [--strict]   # check configs/models.yaml; --strict fails on stale entries

uv run dllm-eval run configs/experiments/<x>.yaml --dry-run          # estimate only; always do this first
uv run --env-file .env dllm-eval run configs/experiments/<x>.yaml --live          # spends tokens: ask first
uv run --env-file .env dllm-eval run <cfg> --live --resume <run_id>  # finish an interrupted run
# --live refuses uncommitted changes (TEST_PLAN B1); --allow-dirty overrides and is recorded in the manifest
uv run dllm-eval analyze <run_id>                                    # offline: arm table + calls.parquet
uv run dllm-eval fetch-data                                          # download + sha256-verify UC1 datasets
uv run dllm-eval score <run_id>                                      # UC1: grade code in Docker -> pass@k; UC2: EX + reliability
uv run dllm-eval charts <run_id>                                     # UC1 Pareto / UC2 task-vs-call speed-up chart
uv run dllm-eval report                                              # all blog assets -> reports/ (configs/report.yaml; offline)
uv run dllm-eval trace <run_id> [<job_id>] [--verify --all]          # UC2: print a task trace / check traces replay
uv run dllm-eval sample-bird --n 150 --seed 2026 --out <ids.txt>     # UC2: stratified BIRD id list (commit it)
make test-sandbox                                                    # grader validation in Docker (no API calls)
make test-bird                                                       # UC2 gold-query check on the real BIRD data (no API calls)
```

Without `--live`, `run` prints the estimate and exits 2. Every live run first probes each provider's network (free `GET .../models`; F2.5). Start probes go in `manifest.json` and all probes in `probes.jsonl`; `analyze` prints them. Tests use the fake provider (`kind: fake`) and never touch the network.

## Golden rules

1. **Never spend tokens without being asked.** Live API calls (smoke, pilot, main runs) need an explicit go-ahead from the user. Default to `--dry-run`, fixtures, and the fake provider.
2. **Never commit secrets.** Keys live only in `.env` (gitignored). Redact `Authorization` headers and keys in logs and fixtures. Check recordings before committing them.
3. **Raw logs are append-only and immutable.** Never edit or delete files under `results/runs/`. Fix analysis code, not data.
4. **Analysis is offline.** Everything in `analysis/` reads from run directories only. If a chart needs a new API call, that is a new run.
5. **No number in the blog without a run ID.** Every published figure must regenerate from `results/published/<run_id>` and meet TEST_PLAN B4.
6. **Fairness rules are fixed.** Do not tune prompts per model in the primary arm, do not change temperature or reasoning settings mid-study, and do not drop "bad" samples. Any deviation becomes a separately labelled arm with a note in the run config.
7. **Don't guess model facts.** Model IDs, prices, limits, and parameter ranges come from `configs/models.yaml` (with `verified_on`). If something is missing or stale, check the vendor docs and update the config. Don't hard-code it.

## Mercury 3 API gotchas (verified 2026-10-20; re-check https://docs.inceptionlabs.ai/llms.txt if in doubt)

- OpenAI-compatible: `base_url="https://api.inceptionlabs.ai/v1"`, model `mercury-3`. Send Mercury-only params (`reasoning_effort`, `diffusing`, `realtime`, `reasoning_summary`) through `extra_body` when using the OpenAI SDK.
- **Always set `reasoning_effort` and `max_completion_tokens` explicitly.** The defaults depend on effort, and the docs contradict each other on the default effort.
- `temperature` must be 0.5–1.0. Out-of-range values are *silently* reset to 1 with a `warning` field. Always log and check `warning`.
- There is no `seed`, so runs are not reproducible bit-for-bit. Use repeated samples and CIs.
- Streaming chunks are **blocks**, not tokens. Compute throughput from server `usage` (`stream_options.include_usage: true`), never from chunk counts.
- With `diffusing: true`, **each chunk is the full current text**. Replace the text on each chunk; don't append. Use diffusing mode only for visualization, never for latency numbers.
- `completion_tokens` already includes `reasoning_tokens`; `prompt_tokens` already includes `cached_tokens`. Don't double-count.
- Retry 429/500/502/503 with exponential backoff and jitter. Don't retry 400/401/402/404. **Exception: a 429 whose error type is `insufficient_quota` (OpenAI's empty-balance error) is fatal. Never retry it.**
- `mercury-3` may emit a short text preamble alongside `tool_calls`; agent loops must accept both in one turn.
- Text-only input; no embeddings endpoint; FIM and next-edit belong to `mercury-edit-2`, not `mercury-3`.

## Measurement best practices

- Time with `time.perf_counter()` around the raw stream. TTFT = first **non-empty** content (or tool-call) delta.
- Randomise the order of (model, item) jobs; never run all of model A, then all of model B.
- Exclude retried calls from latency statistics but count them. Discard the first warm-up call per model per window.
- Report p50 and p95 with bootstrap 95% CIs, not just means. Report visible tokens and reasoning tokens separately.
- Keep comparisons between *products* honest: a dLLM API vs. an AR API includes serving hardware. Use the fast-hardware AR control before attributing a difference to "diffusion".
- Cost = frozen price snapshot × server usage. Report promo and regular Mercury prices.

## Code conventions

- Python 3.12, `uv`, `ruff` (format + lint), `mypy --strict` on `src/`, pydantic v2 for every schema and config.
- Use cases depend only on `BaseProvider.chat()` (via `providers.build_provider`). Multi-turn loops append `result.as_message(provider_key)`, which carries the provider-native turn (`Message.raw`: Anthropic thinking blocks, Gemini thought signatures) that must be echoed back.
- Vendor-specific code stays in `src/dllm_eval/providers/`.
- Async I/O with per-provider semaphores and rate limiters. No global mutable state.
- Prompts are versioned constants (e.g. `PROMPT_V1`). Changing a prompt means a new version, never an edit in place.
- Model-generated code runs **only** inside the Docker sandbox (no network, resource limits, non-root). Never `exec` or `subprocess` it on the host. Downloaded dataset code, including canonical solutions, is treated the same way. `HostExecutor` is only for our own test fixtures.
- After any change to `sandbox/` or `usecases/codegen/`, run `make test-sandbox`. Every EvalPlus canonical solution must still pass, and the pinned input drops must not change.
- Docker Desktop puts its CLI (and `docker-credential-osxkeychain`) in `~/.docker/bin`. Add that to `PATH` for `docker pull`; the executor finds the CLI on its own.
- SQL tools use a read-only SQLite connection (`mode=ro`) with a timeout and a row cap. Model SQL goes through `sql_agent.tools.ReadOnlyDB` only (authorizer, `query_only`, no ATTACH). After any change to `usecases/sql_agent/`, run `make test-bird` too.
- Tests: unit tests for every scorer, parser, and cost function, using recorded fixtures. Live tests use `@pytest.mark.live` and are excluded by default.
- Reference feature IDs (e.g. `F2.6`) in commit messages and PR descriptions.

## Working style for Claude in this repo

- Before starting work, check ROADMAP_AND_PLAN §1 for the current milestone, and don't build ahead of it unless asked.
- When a milestone's gate runs, write `docs/M<n>_<TOPIC>_RESULTS.md` from docs/templates/MILESTONE_RESULTS.md: the gate checklist with evidence, offline and live runs, findings, open items, and where the evidence lives. Take every number from a run id or a command, never from memory.
- When a feature lands, set its **Done** cell in docs/FEATURES.md to `true` and update the status line in the same commit. `make check` fails if the counts don't match the table (`tests/unit/test_features_doc.py`).
- When a doc decision changes (stack, metric definition, model set), update the relevant doc in the same change.
- When the vendor docs disagree with these notes, trust the live docs, then update this file and ROADMAP_AND_PLAN §6 with the date.
- Keep raw data out of the context: summarise run directories with `analyze` instead of `cat`-ing large JSONL files.
- Put scratch experiments in a scratch directory, never in `results/runs/`.
