# mercury3: Diffusion vs. autoregressive LLM evaluation

A harness for benchmarking Inception Labs **Mercury 3** (a diffusion LLM) against autoregressive LLMs. It covers two use cases:
- **UC1:** code-generation speed and quality.
- **UC2:** latency in a multi-step tool-calling agent.

The results will feed a follow-up to *What is Diffusion LLM and why it matters* (The Low End Disruptor, Feb 2025).

**Status:** project initialisation (M0). No implementation yet.

| Doc | Contents |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Requirements and the two use cases |
| [docs/BLOG_ALIGNMENT.md](docs/BLOG_ALIGNMENT.md) | How the results map onto the original article; outline for the follow-up |
| [docs/FEATURES.md](docs/FEATURES.md) | Prioritised feature list |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | Software tests and experiment protocol |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones and gates |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Stack, layout, interfaces, Mercury API notes |
| [CLAUDE.md](CLAUDE.md) | Working rules for this repo |
