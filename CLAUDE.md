# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What this project is

A benchmark harness that compares Inception Labs **Mercury 3** (a diffusion LLM) against autoregressive LLMs on two use cases:
- **UC1:** code generation, measuring the speed-quality frontier.
- **UC2:** a multi-step SQL tool-calling agent, measuring how latency compounds across turns.

The results feed a follow-up blog article. The output that matters is **trustworthy, reproducible numbers**, not a product.

Read these first, in order:
1. [docs/PRD.md](docs/PRD.md): goals, use cases, methodology rules (R1–R13)
2. [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): stack, layout, interfaces, Mercury API notes
3. [docs/TEST_PLAN.md](docs/TEST_PLAN.md): what must pass before a number is publishable
4. [docs/FEATURES.md](docs/FEATURES.md) / [docs/ROADMAP.md](docs/ROADMAP.md): feature IDs and current milestone
5. [docs/BLOG_ALIGNMENT.md](docs/BLOG_ALIGNMENT.md): how results map onto the article

Current phase: **M0, project setup.** The scaffolding is in place, and the model registry is filled in and checked against vendor docs (2026-10-20). Still to do: the first manual API call to each provider, plus the Mercury 3 tool-calling check (PRD OQ4). Both need API keys and the user's go-ahead.

## Commands

```bash
uv sync                      # install (Python 3.12, pinned in .python-version)
make check                   # ruff + mypy + offline tests + validate-config
make fmt                     # ruff format + autofix
make test-live               # live smoke tests: spends real tokens, run only when asked
uv run dllm-eval validate-config [--strict]   # check configs/models.yaml; --strict fails on stale entries
```

Planned for M1 (not implemented yet):

```bash
uv run dllm-eval run configs/experiments/<x>.yaml --dry-run   # always dry-run first
uv run dllm-eval analyze <run_id>                             # offline, never calls APIs
```

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
- Retry 429/500/502/503 with exponential backoff and jitter. Don't retry 400/401/402/404.
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
- Use cases depend only on the `Provider.chat()` interface. Vendor-specific code stays in `src/dllm_eval/providers/`.
- Async I/O with per-provider semaphores and rate limiters. No global mutable state.
- Prompts are versioned constants (e.g. `PROMPT_V1`). Changing a prompt means a new version, never an edit in place.
- Model-generated code runs **only** inside the Docker sandbox (no network, resource limits, non-root). Never `exec` or `subprocess` it on the host.
- SQL tools use a read-only SQLite connection (`mode=ro`) with a timeout and a row cap.
- Tests: unit tests for every scorer, parser, and cost function, using recorded fixtures. Live tests use `@pytest.mark.live` and are excluded by default.
- Reference feature IDs (e.g. `F2.6`) in commit messages and PR descriptions.

## Working style for Claude in this repo

- Before starting work, check ROADMAP.md for the current milestone, and don't build ahead of it unless asked.
- When a doc decision changes (stack, metric definition, model set), update the relevant doc in the same change.
- When the vendor docs disagree with these notes, trust the live docs, then update this file and IMPLEMENTATION_PLAN §5 with the date.
- Keep raw data out of the context: summarise run directories with `analyze` instead of `cat`-ing large JSONL files.
- Put scratch experiments in a scratch directory, never in `results/runs/`.
