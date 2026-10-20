# Project roadmap

Planned start: week of **2026-10-26**. Total duration about 6 weeks, part-time. Each milestone ends with a gate that must pass before the next one starts.

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
| **M0: Project setup and API verification** (wk 1) ✅ done 2026-10-20 | Accounts/keys for all providers; verify model IDs, prices, and limits; resolve PRD OQ1/OQ4; repo scaffolding (uv, ruff, pytest, pre-commit) | — | `configs/models.yaml` filled with `verified_on`; a manual curl to each provider works; CI-style `make check` passes on an empty package |
| **M1: Harness core** (wk 1–2) ✅ done 2026-10-20 | Provider layer, timing, logging, runner, budget guard, cost engine, CLI skeleton | F1.1–F1.8, F2.1–F2.5, F3.1–F3.6, F6.1–F6.3 | A1 + A2 + A4 pass; R0 smoke passes; one end-to-end mini run produces a manifest and a calls.jsonl |
| **M2: UC1 code generation** (wk 2–3) | Datasets, sandbox, scoring, length sweep; R1 pilot, then R2 and R3 | F4.x, F6.4 (Pareto) | Pilot reviewed; R2/R3 complete; Pareto chart regenerates offline; CI targets met |
| **M3: UC2 SQL agent** (wk 3–4) | BIRD subset, tools, agent loop, scorers, strict-schema sub-run; R4 | F5.x, F6.4 (speed-up chart) | R4 complete; traces replayable; reliability table produced |
| **M4: Analysis and assets** (wk 5) | Statistics, cost tables (promo and regular), diffusion GIF (R5), failure tagging, decision matrix | F2.6, F6.5–F6.7, F7.2 (optional) | Every item in the BLOG_ALIGNMENT asset checklist exists and passes B4 |
| **M5: Blog article** (wk 6) | Draft following the BLOG_ALIGNMENT outline; fact-check every number against its run ID; publish the repo/results | — | Every number in the draft is footnoted with a run ID; a reproducibility check is done from a clean clone |
| **Stretch** (after M5 or in parallel with M4) | Reversal probe, FIM/next-edit addendum | F7.1, F7.3 | Separate mini-report |

## Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Mercury promo pricing ends mid-project | Cost story changes | Report both prices; frozen price snapshot per run |
| Model IDs or behaviour change during the study | Results not comparable | Pin IDs; record the server-returned model; finish each use case's main run within one week |
| Rate limits (free tier: 100k output tok/min) | Slow runs | Throttle; upgrade to pay-as-you-go if needed |
| Baseline API spend overruns | Budget | Budget guard; dry-run estimates; pilot first |
| Sandbox security (executing model code) | Host compromise | Docker, no network, resource limits, non-root, read-only FS |
| Mercury tool-calling quirks (FAQ references `mercury-2.5` for MCP) | UC2 blocked | Verify in M0; fall back to documenting the limitation as a finding |
