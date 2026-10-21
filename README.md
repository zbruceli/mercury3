# mercury3: Diffusion vs. autoregressive LLM evaluation

A harness for benchmarking Inception Labs **Mercury 3** (a diffusion LLM) against the fast tiers of autoregressive LLMs: Claude Haiku 5.5, GPT-6 Luna, Gemini 3.5 Flash-Lite, and gpt-oss-120b on Cerebras as a fast-hardware control. It covers two use cases:
- **UC1:** code-generation speed and quality (HumanEval+, MBPP+, LiveCodeBench v6; 637 problems × 5 samples).
- **UC2:** latency in a multi-step SQL tool-calling agent (BIRD Mini-Dev; 150 questions × 3 repeats, up to 8 turns).

The results feed a follow-up to *[What is Diffusion LLM and why it matters](https://medium.com/the-low-end-disruptor/what-is-diffusion-llm-and-why-it-matters-749033d1efb1)* (The Low End Disruptor, Feb 2025).

**Status (2026-10-21):** M0–M4 done; M5 blog draft written and fact-checked. Per-feature status: [docs/FEATURES.md](docs/FEATURES.md).

## Headline results (as of 2026-10-21)

- **Code:** Mercury 3 `low` passes 86.0% at a 0.83 s median, tying Claude Haiku 5.5 with thinking off. gpt-oss-120b on Cerebras passes 84.9% at 0.34 s. Mercury 3 decodes ~850–900 visible tok/s, 2–6× the frontier labs' fast models; Cerebras is ~2.7× faster still.
- **Agent:** Mercury 3 `low` finishes a SQL-agent task in 2.06 s at the median, 1.9–2.9× faster than the frontier labs' fast models. Accuracy is within a few points across models.
- **Cost:** Mercury 3 is not the cheapest per correct answer, even at its promo price.
- **Caveat:** these compare products (model plus serving stack), not architectures.

Every number has a run id. Details: [M2](docs/M2_UC1_RESULTS.md), [M3](docs/M3_UC2_RESULTS.md), [M4](docs/M4_ANALYSIS_RESULTS.md), [M5 fact-check](docs/M5_BLOG_RESULTS.md). Charts and tables: [reports/](reports/README.md).

## Quick start

```bash
uv sync                                                        # Python 3.12
make check                                                     # lint, types, offline tests (no API calls)
uv run dllm-eval run configs/experiments/r2_uc1_main.yaml --dry-run   # cost estimate only
uv run dllm-eval report                                        # rebuild every chart and table in reports/ from scored runs
```

Live runs need API keys in `.env` (see the variable names in `configs/models.yaml`) and spend real money: always dry-run first. Model-generated code runs only inside a locked-down Docker sandbox. Raw run directories (`results/runs/`) and datasets (`data/`) are not in git; `uv run dllm-eval fetch-data` downloads and checksums the datasets from their official sources.

## Docs

| Doc | Contents |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Requirements, the two use cases, and methodology rules |
| [docs/ROADMAP_AND_PLAN.md](docs/ROADMAP_AND_PLAN.md) | Roadmap and milestone gates, then the implementation plan: stack, layout, interfaces, Mercury API notes |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | Software tests and experiment protocol |
| [docs/FEATURES.md](docs/FEATURES.md) | Prioritised feature list with Done status |
| [docs/BLOG_ALIGNMENT.md](docs/BLOG_ALIGNMENT.md) | How the results map onto the original article |
| [docs/BUDGET_ESTIMATE.md](docs/BUDGET_ESTIMATE.md) | Token and cost estimate for the planned runs (`make budget`) |
| [docs/M0_SMOKE_RESULTS.md](docs/M0_SMOKE_RESULTS.md) | M0: live API smoke results and API findings |
| [docs/M1_SMOKE_RESULTS.md](docs/M1_SMOKE_RESULTS.md) | M1: harness core gate |
| [docs/M2_UC1_RESULTS.md](docs/M2_UC1_RESULTS.md) | M2: UC1 code generation (R1 pilot, R2 main run, R3 length sweep) |
| [docs/M3_UC2_RESULTS.md](docs/M3_UC2_RESULTS.md) | M3: UC2 SQL agent (R4 main run, R4b strict-schema sub-run) |
| [docs/M4_ANALYSIS_RESULTS.md](docs/M4_ANALYSIS_RESULTS.md) | M4: blog assets, failure taxonomy, diffusion replay |
| [docs/M5_BLOG_RESULTS.md](docs/M5_BLOG_RESULTS.md) | M5: blog draft fact-check, every number against its run id |
| [reports/README.md](reports/README.md) | Generated charts, tables, failure gallery, decision matrix |
| [docs/templates/MILESTONE_RESULTS.md](docs/templates/MILESTONE_RESULTS.md) | Template for each milestone's results doc |
| [CLAUDE.md](CLAUDE.md) | Working rules for this repo |

## License

Code: [MIT](LICENSE). Benchmark datasets (HumanEval+, MBPP+, LiveCodeBench, BIRD) are not redistributed here and stay under their owners' licenses; see the sources in [configs/datasets.yaml](configs/datasets.yaml).
