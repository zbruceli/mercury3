# M<n> <topic> results

<!-- Copy to docs/M<n>_<TOPIC>_RESULTS.md when the milestone's gate runs (CLAUDE.md rule).
     Record what was actually observed. Every number needs a run id or a command that
     reproduces it. Delete sections that don't apply instead of leaving them empty. -->

| | |
|---|---|
| Milestone | M<n>: <name> (see [ROADMAP.md](../ROADMAP.md)) |
| Date | YYYY-MM-DD |
| Commit | `<sha>` (the code the gate ran on) |
| Gate verdict | **passed / failed / passed with open items** |
| Tokens / spend | <total>, or "none (offline only)" |

## 1. Gate checklist

The exit gate from ROADMAP.md, one row per criterion:

| Criterion | Result | Evidence |
|---|---|---|
| <criterion> | ✅ / ❌ / 🟡 | <run id, test command, or file> |

## 2. Offline verification

`make check` result: tests, coverage, and what the new tests cover.

## 3. Live runs

One subsection per run.

| | |
|---|---|
| Run id | `<run_id>` (results/runs/ is local only unless copied to results/published/) |
| Config | `configs/experiments/<x>.yaml` |
| Jobs / result | <n> jobs: <ok> ok, <errors> errors, <retries> retries, <warnings> warnings |
| Spend | $<x> (runner total vs. `analyze` sum) |
| Network baseline | Median connect / TTFB per provider from `analyze` |

What it verified, and anything surprising.

## 4. Findings

| # | Finding | Impact | Where handled |
|---|---|---|---|
| M<n>-F1 | | | |

## 5. Changes made because of this milestone's results

## 6. Open items

Carried to the next milestone, each with a feature ID or doc link.

## 7. Where the evidence lives

| What | Where | In git? |
|---|---|---|
| | | |
