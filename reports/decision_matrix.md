# Decision matrix: when to reach for a diffusion LLM today

Blog claim C10 predicted that diffusion and autoregressive models would coexist, each suited
to different jobs. This is the measured version. The picks are judgements written on
2026-10-21 against these runs; every number in the Evidence column is regenerated from the
run data by `dllm-eval report`, so re-check the picks if a regeneration changes them.

| If you need… | Pick | Evidence |
| --- | --- | --- |
| Interactive coding help, short answers, latency first | **gpt-oss-120b on Cerebras** (`medium`); Mercury 3 `low` if the extra point of accuracy is worth the wait | gpt-oss `medium`: 84.9% pass@1 at 0.34 s median. Mercury 3 `low`: 86.0% pass@1 at 0.83 s median, 2.4x the median latency. Mercury 3 `instant` (80.6% pass@1 at 0.31 s median) is 5.4 points below `low` |
| Code generation, accuracy first | **GPT-6 Luna `medium`** or **Claude Haiku 5.5 `medium`** | Luna `medium`: 88.4% pass@1 at 2.42 s median. Haiku `medium`: 87.9% pass@1 at 1.35 s median. No Mercury 3 setting beats 86.0% pass@1 at 0.83 s median |
| Code generation, cheapest per right answer | **GPT-6 Luna `none`**, then Haiku thinking off | Luna `none`: $0.07 per 1,000 passing answers; Haiku off $0.14; Mercury 3 `low` $0.59 promo / $1.19 regular |
| Mercury 3 reasoning effort | **Use `low`** (or `instant` for the lowest latency). Avoid `medium`/`high` for code | `medium`/`high` run out of budget on 7.5% / 8.6% of calls, drop to 81.4% pass@1 and push p95 latency to 54 s (`low`: 10.4 s) |
| Long generated files, raw throughput | **gpt-oss-120b on Cerebras**; Mercury 3 beats the frontier-lab fast models | Visible decode on ~8k-token answers (R3): gpt-oss 2,396 tok/s, Mercury 3 `instant` 893, Claude Haiku 324. Flat with length |
| Multi-step tool agent, latency first | **Mercury 3 `low`/`instant`**, or gpt-oss `low` if you can tolerate turn-cap misses | Mercury 3 `low`: 52.5% EX, 2.05 s per task; `instant`: 50.2% EX, 2.00 s per task; gpt-oss `low`: 46.2% EX, 1.92 s per task but 14.3% hit the turn cap; Haiku off: 56.7% EX, 3.87 s per task |
| Multi-step tool agent, accuracy first | **Claude Haiku 5.5** (thinking off is enough) | Haiku off: 56.7% EX, 3.87 s per task. Paired against Mercury 3 `low` on the same questions: ahead with a CI that excludes zero: Claude Haiku 5.5 · medium +5.3 points, CI +2.6 to +8.1; Claude Haiku 5.5 · thinking-off +4.2 points, CI +1.8 to +6.7. Behind: gpt-oss-120b (Cerebras) · low -6.3 points, CI -9.3 to -3.3; gpt-oss-120b (Cerebras) · medium -7.8 points, CI -10.8 to -4.7; Mercury 3 · instant -2.3 points, CI -4.4 to -0.2 (bound at zero: borderline). Per-arm EX CIs are ±4 points |
| Strict JSON output with tools also offered | **Not Mercury 3 today**; Luna, Gemini or Haiku (but Haiku stops using tools) | Mercury 3 broke the schema on 6 of 150 tasks; every other arm 0 (R4b) |
| Agent loop, cheapest per right answer | **GPT-6 Luna `none`**, then Mercury 3 `low` at promo prices, Claude Haiku thinking off at regular prices | $ per 1,000 correct tasks: Luna $0.77; Mercury 3 `low` $1.38 promo / $2.77 regular; Haiku off $1.55; Mercury 3 `instant` $1.70 promo; gpt-oss `low` $5.43 |

**How to read it.**
- These are products, not architectures. gpt-oss-120b on Cerebras is an autoregressive
  model on specialised hardware, and it is the fastest arm in both use cases. Where
  Mercury 3 wins on speed, it wins against the frontier-lab fast models on standard
  serving hardware (PRD R13).
- Mercury 3 is the speed pick for agents because it is fast per call *and* finishes in a
  moderate number of turns; gpt-oss is faster per call but needs more turns and misses more
  answers.
- Mercury prices are promotional; both prices are shown. The promo makes Mercury 3 about
  half price, not cheapest.

*Source: run r2-uc1-main-20261021T034053Z (commit 07dce9e, 2026-10-21); run r3-uc1-length-20261021T152238Z (commit b9d80e6, 2026-10-21); run r4c-uc2-full-20261021T214456Z (commit 1cada9b, 2026-10-21); run r4b-uc2-strict-20261021T170547Z (commit 3c4b5fc, 2026-10-21). Models: mercury-3, mercury-2.5, claude-haiku-5-5, gpt-6-luna, gemini-3.5-flash-lite, gpt-oss-120b. Standard API tiers (no paid priority); client one macOS arm64 host, US West; prices as listed 2026-10-20. As of 2026-10-21.*
