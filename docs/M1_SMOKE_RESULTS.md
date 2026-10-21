# M1 harness core results

| | |
|---|---|
| Milestone | M1: Harness core (see [ROADMAP_AND_PLAN.md](ROADMAP_AND_PLAN.md) §1) |
| Date | 2026-10-20 |
| Commits | `6005a22` (harness core and live smoke), `5fd62f1` (untrack derived output), `cd98b06` (F2.5 network probe and clean-tree guard) |
| Gate verdict | **Passed.** One item never ran live: the F2.5 probe's TLS path, which first runs at the start of the next `--live` run (§6) |
| Tokens / spend | 766 tokens (596 input, 170 output), **$0.00028** (one live harness smoke run) |

## 1. Gate checklist

The exit gate from ROADMAP_AND_PLAN §1:

| Criterion | Result | Evidence |
|---|---|---|
| A1: unit tests pass | ✅ | `make check`: 127 passed, 96% coverage (§2) |
| A2: contract tests pass | ✅ | `tests/contract/`: every R0 recording replays through the production parsers, plus I/O-path tests through a stub SDK client |
| A4: pipeline dry tests pass | ✅ | Fake-provider run → resume → analyze through the real CLI (§3.1); `--dry-run` job counts and cost estimates are tested |
| R0 smoke passes through the harness | ✅ | `r0-smoke-20261020T233834Z`: 24/24 ok (§3.2) |
| End-to-end mini run writes a manifest and calls.jsonl | ✅ | Both the fake run and the live smoke run (§3) |

## 2. Offline verification

`make check` at `cd98b06`:

- **127 tests passed, 96% coverage.** The provider modules are 95–100%, `probe.py` 96%, `runner.py` 93%, and `cli.py` 91%.
- **Contract tests** (`tests/contract/test_recordings.py`) replay R0 recordings: stream, plain, tool call, and JSON schema for all six models, plus Mercury's diffusing, warning, effort, and realtime recordings, and the 429 `insufficient_quota` body. At `cd98b06` they used 24 of the 30 recordings. The other 6 got tests when this doc was written, and `test_every_r0_recording_is_replayed` now fails if a recording has no test.
- **I/O-path tests** (`tests/contract/test_io_paths.py`) drive each adapter's real `_chat_once` through a stub SDK client that serves those recordings. This exercises streaming, timing marks, and raw capture with no network.
- **Runner and CLI tests** use the fake provider. They cover seeded interleaving, warm-up marking, resume, the budget guard, aborts on a provider `warning` and on a billing error, `--live` being required, refusing to resume with a changed config, the clean-tree guard, and start probes landing in the manifest and `probes.jsonl`.
- **Network probe tests** use a scripted fake network plus one real-socket test against a loopback server. An autouse fixture keeps every test offline.
- **`test_features_doc.py`** keeps the FEATURES.md Done column machine-checkable.

## 3. Live and pipeline runs

### 3.1 Fake-provider pipeline (A4)

Run through the real CLI with the fake registry, in the scratchpad:

- 20 jobs (2 arms × 5 prompts × 2 repeats): **20/20 ok**.
- `--resume` on the same run skipped all 20 completed jobs.
- `analyze` wrote `calls.parquet` and `arm_summary.json`. The warm-up call (one per arm) was left out of latency stats: `latency_n` was 9 of 10.

### 3.2 Live harness smoke (R0 via the harness)

| | |
|---|---|
| Run id | `r0-smoke-20261020T233834Z` (local only; `results/runs/` is gitignored) |
| Config | [configs/experiments/r0_smoke.yaml](../configs/experiments/r0_smoke.yaml) |
| Jobs / result | 24: 6 models × 2 prompts × 2 repeats, lowest reasoning arm each. **24 ok**, 0 errors, 0 retries, 0 warnings, no abort |
| Spend | **$0.00028**: the runner total matches the per-arm sum from `analyze` |
| Answers | All correct (`ready`, `391`). The server-reported model id matched the config every time |
| Secrets | None of the 5 API keys appear in `calls.jsonl` or `manifest.json` |
| Network baseline | Not recorded: F2.5 landed after this run |

| Arm | OK | TTFT p50 (s) | Reasoning tokens | Cost $ |
|---|---:|---:|---:|---:|
| `mercury-3:instant` | 4/4 | 0.254 | 0 | 0.000010 |
| `mercury-2.5:instant` | 4/4 | 0.395 | 0 | 0.000025 |
| `claude-haiku-5-5:thinking-off` | 4/4 | 0.821 | 0 | 0.000015 |
| `gpt-6-luna:none` | 4/4 | 2.217 | 0 | 0.000015 |
| `gemini-3.5-flash-lite:minimal` | 4/4 | 0.549 | unknown | 0.000035 |
| `gpt-oss-120b-cerebras:low` | 4/4 | 0.205 | 51 | 0.000180 |

> Each arm has only 4 calls with 4–8-token answers. Latency and tok/s are plumbing checks, not results. Don't quote them.

**What it verified end to end:**
- Every adapter's live streaming path, with timing marks.
- Seeded interleaving, raw chunk capture, and key redaction.
- The frozen manifest: pricing snapshot, git SHA, and package versions.
- `analyze` output: Gemini's reasoning tokens show as `unknown` (M0 F3), and gpt-oss reports reasoning tokens even at `low` (M0 F4).
- Mercury 3 and 2.5 accepted `temperature: 0.5` with no `warning`.

## 4. Findings

| # | Finding | Impact | Where handled |
|---|---|---|---|
| M1-F1 | The smoke manifest recorded `git.dirty: true`: M1 wasn't committed when it ran, and the CLI only recorded this | A real run could carry a SHA that isn't the code that ran (TEST_PLAN B1) | `run --live` now refuses a dirty or non-git tree unless `--allow-dirty`, which the manifest records (`cd98b06`) |
| M1-F2 | The `analyze` cost column printed `0.0000` at smoke scale | Tiny costs were unreadable | 6-decimal cost column (`6005a22`) |
| M1-F3 | `results/analysis/` was not gitignored, so derived output was committed without its raw run | Repo held numbers no clone could regenerate | Ignored and untracked (`5fd62f1`) |
| M1-F4 | R0 recordings show Gemini attaching a `thought_signature` (`extra_content`) to assistant turns | Multi-turn tool loops must echo it, as with Anthropic thinking blocks | `Message.raw` + `ChatResult.as_message()` replay provider-native turns. Needed by the UC2 agent loop (M3) |

M0 findings implemented in M1:
- F1: `insufficient_quota` is fatal.
- F2: the run fails on any provider `warning`.
- F3: usage the provider didn't report is unknown, not 0.
- F5: Anthropic `thinking_tokens` map to reasoning tokens.

Each has a contract test. See [M0_SMOKE_RESULTS.md](M0_SMOKE_RESULTS.md) §3.

## 5. Changes made because of this milestone's results

- **Clean-tree guard** for `run --live` (M1-F1).
- **Network baseline probe (F2.5).** It landed after the smoke run and closes the M1 scope.
- **Per-feature tracking:** FEATURES.md has a **Done** column (23 of 44 at M1 close), checked by `test_features_doc.py`.
- **Milestone results docs** like this one, from [templates/MILESTONE_RESULTS.md](templates/MILESTONE_RESULTS.md).

## 6. Open items

- **The F2.5 probe has never run against real providers.** Its TLS step can't be tested offline. The first `--live` run (the M2 R1 pilot) will exercise it, and that run's results doc should check the probe table from `analyze`.
- **F1.9** (`inceptionai` SDK cross-check, P2): not done. It's optional.

## 7. Where the evidence lives

| What | Where | In git? |
|---|---|---|
| This write-up | `docs/M1_SMOKE_RESULTS.md` | ✅ |
| Smoke config | `configs/experiments/r0_smoke.yaml` | ✅ |
| Tests and recordings | `tests/` (unit, contract, `fixtures/recordings/`) | ✅ |
| Live smoke raw run: `manifest.json` + `calls.jsonl` | `results/runs/r0-smoke-20261020T233834Z/` | ❌ local only |
| Live smoke analysis: `calls.parquet` + `arm_summary.json` | `results/analysis/r0-smoke-20261020T233834Z/` | ❌ local only (regenerate with `dllm-eval analyze`) |
| Fake pipeline run | scratchpad (temporary) | ❌ reproducible via `tests/unit/test_runner.py` |
