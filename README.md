# mercury3: Diffusion vs. autoregressive LLM evaluation

A harness for benchmarking Inception Labs **Mercury 3** (a diffusion LLM) against autoregressive LLMs. It covers two use cases:
- **UC1:** code-generation speed and quality.
- **UC2:** latency in a multi-step tool-calling agent.

The results will feed a follow-up to *What is Diffusion LLM and why it matters* (The Low End Disruptor, Feb 2025).

**Status:** M0 and M1 done (harness core, live smoke 24/24). M2 (UC1 code generation) is next. Per-feature status: [docs/FEATURES.md](docs/FEATURES.md).

| Doc | Contents |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Requirements and the two use cases |
| [docs/BLOG_ALIGNMENT.md](docs/BLOG_ALIGNMENT.md) | How the results map onto the original article; outline for the follow-up |
| [docs/FEATURES.md](docs/FEATURES.md) | Prioritised feature list |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | Software tests and experiment protocol |
| [docs/BUDGET_ESTIMATE.md](docs/BUDGET_ESTIMATE.md) | Token and cost estimate for the planned runs (`make budget`) |
| [docs/M0_SMOKE_RESULTS.md](docs/M0_SMOKE_RESULTS.md) | M0: R0 live smoke results and API findings |
| [docs/M1_SMOKE_RESULTS.md](docs/M1_SMOKE_RESULTS.md) | M1: harness core gate (offline tests, fake pipeline, live harness smoke) |
| [docs/templates/MILESTONE_RESULTS.md](docs/templates/MILESTONE_RESULTS.md) | Template for each milestone's results doc |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones and gates |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Stack, layout, interfaces, Mercury API notes |
| [CLAUDE.md](CLAUDE.md) | Working rules for this repo |
