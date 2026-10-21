"""Blog asset and report generator (F6.7). Offline only.

`dllm-eval report` reads the runs named in configs/report.yaml (run directories plus the
score files `score` wrote) and writes every BLOG_ALIGNMENT section 5 asset into reports/:
figures with their CSV data under reports/assets/, the reliability table, failure-mode
tables, failure gallery, decision matrix, and reports/README.md, which links them all.
Every figure and table carries a dated "as of" footnote (TEST_PLAN B4.4): run id, commit,
model ids, service tier, client region, and the price snapshot date.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict

from dllm_eval.analysis import assets, diffusion, failures, uc2
from dllm_eval.analysis.charts import pareto_points, plot_pareto, write_points_csv
from dllm_eval.analysis.load import load_calls
from dllm_eval.analysis.tables import latency_sample
from dllm_eval.logging_store import RunStore
from dllm_eval.stats import percentile

DEFAULT_REPORT_CONFIG = Path("configs/report.yaml")


class ReportRuns(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    uc1: str  # scored UC1 main run (R2)
    uc1_length: str  # UC1 length sweep (R3)
    uc2: str  # scored UC2 main run (R4c: all 496 questions; R4 used 150)
    uc2_strict: str  # scored UC2 strict-schema sub-run (R4b)
    diffusion: str | None = None  # R5 diffusing-mode capture, once it exists


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: dt.date
    client: str  # where the client ran (TEST_PLAN B4.4 "region")
    service_tier: str  # e.g. "standard API tiers, no paid priority"
    runs: ReportRuns
    manual_tags: Path = Path("reports/failure_tags.csv")
    diffusion_job: str | None = None  # job replayed as the GIF (default: the first with frames)


def load_report_config(path: Path = DEFAULT_REPORT_CONFIG) -> ReportConfig:
    return ReportConfig.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    date: str
    commit: str
    model_ids: list[str]
    prices_verified: str

    @classmethod
    def from_store(cls, store: RunStore) -> RunInfo:
        m = store.read_manifest()
        used = list(dict.fromkeys(a["model"] for a in m["experiment"]["arms"]))
        models = m["models"]
        return cls(
            run_id=m["run_id"],
            date=m["created_at"][:10],
            commit=(m.get("git") or {}).get("sha", "")[:7],
            model_ids=[models[k]["model_id"] for k in used],
            prices_verified=min(models[k]["verified_on"] for k in used),
        )


def footnote(cfg: ReportConfig, *runs: RunInfo) -> str:
    """One dated provenance line: every source run, the model ids, tier, client, prices."""
    ids = list(dict.fromkeys(i for r in runs for i in r.model_ids))
    src = "; ".join(f"run {r.run_id} (commit {r.commit}, {r.date})" for r in runs)
    return (
        f"Source: {src}. Models: {', '.join(ids)}. {cfg.service_tier}; client {cfg.client}; "
        f"prices as listed {min(r.prices_verified for r in runs)}. As of {cfg.as_of}."
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: run `dllm-eval score` first")
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{100 * v:.{digits}f}%"


def _md_table(head: list[str], rows: list[list[str]], align: str = "l") -> str:
    seps = ["---"] + ["---:" if align == "r" else "---" for _ in head[1:]]
    return "\n".join(
        ["| " + " | ".join(head) + " |", "| " + " | ".join(seps) + " |"]
        + ["| " + " | ".join(r) + " |" for r in rows]
    )


@dataclass
class Data:
    """Everything the assets need, loaded once."""

    cfg: ReportConfig
    info: dict[str, RunInfo]
    stores: dict[str, RunStore]
    uc1_calls: pd.DataFrame
    uc1_scores: list[dict[str, Any]]
    uc2_tasks: pd.DataFrame
    uc2_turns: pd.DataFrame
    uc2_scores: list[dict[str, Any]]
    uc2_table: list[dict[str, Any]]
    strict_table: list[dict[str, Any]]
    strict_scores: list[dict[str, Any]]
    length_calls: pd.DataFrame


def load_data(cfg: ReportConfig, runs_dir: Path, analysis_dir: Path) -> Data:
    r = cfg.runs
    keys = {"uc1": r.uc1, "uc1_length": r.uc1_length, "uc2": r.uc2, "uc2_strict": r.uc2_strict}
    stores = {k: RunStore(runs_dir / v) for k, v in keys.items()}
    for k, s in stores.items():
        if not s.exists():
            raise FileNotFoundError(f"run {keys[k]} not found under {runs_dir}")
    uc2_scores = _read_jsonl(analysis_dir / r.uc2 / "uc2_scores.jsonl")
    strict_scores = _read_jsonl(analysis_dir / r.uc2_strict / "uc2_scores.jsonl")
    tasks, turns = (pd.DataFrame(x) for x in uc2.task_rows(stores["uc2"]))
    s_tasks, s_turns = (pd.DataFrame(x) for x in uc2.task_rows(stores["uc2_strict"]))
    seed = stores["uc2"].read_manifest()["experiment"].get("seed", 0)
    return Data(
        cfg=cfg,
        info={k: RunInfo.from_store(s) for k, s in stores.items()},
        stores=stores,
        uc1_calls=load_calls(stores["uc1"]),
        uc1_scores=_read_jsonl(analysis_dir / r.uc1 / "scores.jsonl"),
        uc2_tasks=tasks,
        uc2_turns=turns,
        uc2_scores=uc2_scores,
        uc2_table=uc2.arm_table(tasks, turns, uc2_scores, seed=seed),
        strict_table=uc2.arm_table(s_tasks, s_turns, strict_scores, seed=seed),
        strict_scores=strict_scores,
        length_calls=load_calls(stores["uc1_length"]),
    )


# --- derived numbers ------------------------------------------------------------------


def length_decode_rates(calls: pd.DataFrame) -> dict[str, dict[str, float]]:
    """R3: median visible decode rate (visible tokens / time after the first visible token)
    per arm and length bucket (item id prefix)."""
    lat = latency_sample(calls).copy()
    lat["bucket"] = lat["item_id"].str.split("-").str[0]
    visible = lat["output_tokens"] - lat["reasoning_tokens"].fillna(0)
    lat["rate"] = visible / (lat["total_s"] - lat["ttft_s"])
    out: dict[str, dict[str, float]] = {}
    for (arm, bucket), g in lat.groupby(["arm", "bucket"]):
        rates = g.loc[g["bucket"] != "short", "rate"].dropna().tolist() or g["rate"].tolist()
        out.setdefault(str(arm), {})[str(bucket)] = statistics.median(rates)
    return out


def uc1_p95(calls: pd.DataFrame) -> dict[str, float]:
    lat = latency_sample(calls)
    return {str(a): percentile(g["total_s"].dropna().tolist(), 95) for a, g in lat.groupby("arm")}


def tag_all(d: Data) -> dict[str, list[failures.Failure]]:
    manual = failures.read_manual(d.cfg.manual_tags)
    return {
        "uc1": failures.tag_run(
            d.info["uc1"].run_id, d.stores["uc1"].latest_by_job().values(), d.uc1_scores,
            kind="uc1", manual=manual,
        ),
        "uc2": failures.tag_run(
            d.info["uc2"].run_id, d.stores["uc2"].latest_by_job().values(), d.uc2_scores,
            kind="uc2", manual=manual,
        ),
        "uc2_strict": failures.tag_run(
            d.info["uc2_strict"].run_id, d.stores["uc2_strict"].latest_by_job().values(),
            d.strict_scores, kind="uc2", manual=manual,
        ),
    }  # fmt: skip


def _jobs_per_arm(store: RunStore) -> dict[str, int]:
    return dict(Counter(r["arm"] for r in store.latest_by_job().values()))


# --- writers --------------------------------------------------------------------------


def write_figures(d: Data, assets_dir: Path) -> dict[str, Path]:
    cfg, info = d.cfg, d.info
    out: dict[str, Path] = {}
    seed = d.stores["uc1"].read_manifest()["experiment"].get("seed", 0)
    points = pareto_points(d.uc1_calls, d.uc1_scores, seed=seed)
    n_problems = max((p.problems for p in points), default=0)
    out["fig1"] = plot_pareto(
        points,
        assets_dir / "fig1_pareto_uc1.png",
        title="Speed vs. correctness on code generation",
        subtitle=(
            f"{n_problems} problems (HumanEval+, MBPP+, LiveCodeBench v6 pre-cutoff) x 5 samples; "
            "one point per model and reasoning setting; bars = 95% bootstrap CI over problems"
        ),
        footnote=footnote(cfg, info["uc1"]),
    )
    write_points_csv(points, assets_dir / "fig1_pareto_uc1.csv")

    sp = uc2.speedup_points(d.uc2_tasks, d.uc2_turns, seed=seed)
    n_q = d.uc2_tasks["item_id"].nunique()
    out["fig2"] = uc2.plot_speedup(
        sp,
        assets_dir / "fig2_task_vs_call_speedup_uc2.png",
        title="Does per-call speed carry through to whole agent tasks?",
        subtitle=(
            f"SQL agent on {n_q} BIRD Mini-Dev questions x 3 (max 8 turns). Median latency per "
            f"model call and per task, relative to {uc2.DEFAULT_REFERENCE}; mean turns per "
            "task beside each task bar; whiskers = 95% bootstrap CI"
        ),
        footnote=footnote(cfg, info["uc2"]),
    )
    uc2.write_speedup_csv(sp, assets_dir / "fig2_task_vs_call_speedup_uc2.csv")

    arms2 = set(d.uc2_tasks["arm"])
    panels = {
        "uc1": assets.uc1_latency_samples(d.uc1_calls, assets.CDF_ARMS),
        "uc2": assets.uc2_latency_samples(d.uc2_tasks, [a for a in assets.CDF_ARMS if a in arms2]),
    }
    out["fig3"] = assets.plot_latency_cdf(
        panels,
        assets_dir / "fig3_latency_cdf.png",
        titles={"uc1": "Code generation: one call per problem", "uc2": "SQL agent: whole task"},
        title="How long the wait is, including the slow tail",
        subtitle=(
            "Empirical CDF of end-to-end latency. Left: every latency-eligible UC1 call "
            "(warm-up, retried and pause-spanning calls excluded). Right: every latency-eligible "
            "UC2 task, all turns plus local SQL. Mercury 3 high's p95 is "
            f"{uc1_p95(d.uc1_calls)['mercury-3:high']:.0f} s on code: hidden reasoning that runs "
            "to the 32,768-token cap"
        ),
        footnote=footnote(cfg, info["uc1"], info["uc2"]),
    )
    assets.cdf_table(panels).to_csv(
        assets_dir / "fig3_latency_cdf.csv", index=False, float_format="%.4f"
    )

    cost = assets.uc1_cost_points(d.uc1_calls, d.uc1_scores) + assets.uc2_cost_points(d.uc2_table)
    out["fig4"] = assets.plot_cost_per_success(
        cost,
        assets_dir / "fig4_cost_per_success.png",
        titles={
            "uc1": "Code generation: $ per 1,000 passing answers",
            "uc2": "SQL agent: $ per 1,000 correct tasks",
        },
        rate_label={"uc1": "pass", "uc2": "EX"},
        title="What a right answer costs",
        subtitle=(
            "All spend in the run (warm-up and retried calls included) divided by successes. "
            "Mercury is on a promotional price: the solid bar is the promo price, the hatched "
            "extension the regular price ($0.10/$0.35 vs $0.20/$0.70 per 1M tokens in/out)"
        ),
        footnote=footnote(cfg, info["uc1"], info["uc2"]),
    )
    assets.write_cost_csv(cost, assets_dir / "fig4_cost_per_success.csv")
    return out


def reliability_md(d: Data, tags: dict[str, list[failures.Failure]]) -> str:
    cfg, info = d.cfg, d.info
    perfect = [r for r in d.uc2_table if r["tool_call_validity"] == 1]
    others = ", ".join(
        f"{assets.arm_label(r['arm'])} {_pct(r['tool_call_validity'], 2)}"
        for r in d.uc2_table
        if r["tool_call_validity"] != 1
    )
    validity_line = (
        f"{len(perfect)} of {len(d.uc2_table)} arms made only schema-valid tool calls"
        + (f"; {others}" if others else "")
    )
    label = assets.arm_label
    t2 = sorted(d.uc2_table, key=lambda r: -(r["submitted"] or 0))
    uc2_rows = [
        [
            label(r["arm"]),
            f"{r['tool_calls']:,}",
            _pct(r["tool_call_validity"], 2),
            _pct(r["invalid_sql_rate"]),
            _pct(r["submitted"]),
            _pct(r["turn_cap"]),
            _pct(r["budget_out"]),
            f"{r['malformed_task_count']} ({r['recoveries']})",
            _pct(r["pred_sql_error"]),
        ]
        for r in t2
    ]
    strict_viol = Counter(f.arm for f in tags["uc2_strict"] if f.tag == "schema_violation")
    st = sorted(d.strict_table, key=lambda r: strict_viol[r["arm"]])
    strict_rows = [
        [
            label(r["arm"]),
            f"{strict_viol[r['arm']]} ({_pct(strict_viol[r['arm']] / r['tasks'])})",
            _pct(r["submitted"]),
            _pct(r["turn_cap"]),
            f"{r['turns_mean']:.1f}",
            _pct(r["ex"]),
        ]
        for r in st
    ]
    jobs = _jobs_per_arm(d.stores["uc1"])
    agg = {r["arm"]: r for r in failures.aggregate(tags["uc1"], jobs, failures.UC1_TAGS)}
    uc1_rows = [
        [
            label(a),
            f"{jobs[a]:,}",
            _pct(agg[a]["budget_out_hidden"] / jobs[a]),
            _pct(agg[a]["think_aloud_runaway"] / jobs[a]),
            str(agg[a]["no_code"]),
            str(agg[a]["api_error"]),
        ]
        for a in sorted(
            jobs, key=lambda a: agg[a]["budget_out_hidden"] + agg[a]["think_aloud_runaway"]
        )
    ]
    return f"""# Reliability: tool calls, structured output, and runaway generation

Blog claim C5 asks whether seeing the whole output at once gives more reliable structured
output. Three tables answer it from three runs. Generated by `dllm-eval report`; don't
edit by hand.

## 1. Tool-call validity in the SQL agent (R4c)

{
        _md_table(
            [
                "Arm",
                "Tool calls",
                "Valid calls",
                "Invalid SQL (`run_sql`)",
                "Answered",
                "Turn cap",
                "Budget out",
                "Malformed tasks (recovered)",
                "Answer SQL fails",
            ],
            uc2_rows,
            "r",
        )
    }

- **Valid calls**: a known tool with JSON arguments that match its schema.
- **Invalid SQL**: `run_sql` calls that returned an SQLite error.
- **Malformed tasks**: a reply with no tool call (nudged to use one) or an invalid call;
  "recovered" means the task still submitted an answer.
- **Answer SQL fails**: the submitted query errors or times out at scoring.

{validity_line}. The differences are in *finishing*: answering inside 8 turns and
calling `submit_answer` rather
than writing the answer as prose.

*{footnote(cfg, info["uc2"])}*

## 2. Strict JSON-schema output with tools offered (R4b)

The final answer is the reply text under `response_format: json_schema` (strict) instead
of a `submit_answer` call. One repeat per question, lowest setting per model; gpt-oss on
Cerebras is excluded: it rejects requests that combine `tools` and `response_format`
(PRD OQ5).

{_md_table(["Arm", "Schema violations", "Answered", "Turn cap", "Turns", "EX"], strict_rows, "r")}

Mercury 3 is the only arm that broke the schema: valid JSON followed by leaked tool-call
markup (`</|inc_tool_call_ml|parameter>`, `</`), or an empty reply. Claude Haiku never
violated it, but it also never explored: every task was answered in one turn with no tool
calls (see the failure gallery).

*{footnote(cfg, info["uc2_strict"])}*

## 3. Runaway generation in code generation (R2)

{
        _md_table(
            [
                "Arm",
                "Calls",
                "Budget out (hidden reasoning)",
                "Think-aloud runaway (visible)",
                "No code",
                "API errors",
            ],
            uc1_rows,
            "r",
        )
    }

- **Budget out (hidden reasoning)**: the call stopped at the 32,768-token cap with no
  visible text; the reasoning never converged.
- **Think-aloud runaway**: stopped at the cap while writing visible text, which is how the
  same failure shows up at `instant`, where Mercury 3 has no hidden reasoning.

Only Mercury 3 runs away, and the rate rises with reasoning effort. Both count as failures
(PRD R1).

*{footnote(cfg, info["uc1"])}*
"""


def failure_modes_md(d: Data, tags: dict[str, list[failures.Failure]]) -> tuple[str, pd.DataFrame]:
    cfg, info = d.cfg, d.info
    sections, frames = [], []
    for key, kind, title in (
        ("uc1", failures.UC1_TAGS, "Code generation (R2)"),
        ("uc2", failures.UC2_TAGS, "SQL agent, tool mode (R4c)"),
        ("uc2_strict", failures.UC2_TAGS, "SQL agent, strict-schema mode (R4b)"),
    ):
        jobs = _jobs_per_arm(d.stores[key])
        rows = failures.aggregate(tags[key], jobs, kind)
        frames.append(pd.DataFrame(rows).assign(run_id=info[key].run_id))
        used = [t for t in kind if any(r[t] for r in rows)]
        legend = "\n".join(f"- `{t}`: {failures.TAG_HELP[t]}" for t in used)
        sections.append(
            f"## {title}\n\n{failures.aggregate_markdown(rows, kind, assets.arm_label)}\n\n"
            f"{legend}\n\n*{footnote(cfg, info[key])}*"
        )
    manual = sum(f.source == "manual" for fs in tags.values() for f in fs)
    body = "\n\n".join(sections)
    md = f"""# Failure modes per model (F6.6)

Every failed job gets one tag. Tags come from rules over the raw logs and scores
(`dllm_eval.analysis.failures`); the manual file `reports/failure_tags.csv` confirms or
overrides them and picks the gallery examples ({manual} manual tags in this build). Each
cell is a count and its share of all jobs in the arm. Generated by `dllm-eval report`.

{body}
"""
    return md, pd.concat(frames, ignore_index=True)


def gallery_md(d: Data) -> str:
    rows = [r for r in failures.read_manual(d.cfg.manual_tags) if r.get("gallery") == "1"]
    by_run = {i.run_id: k for k, i in d.info.items()}
    scores = {
        "uc1": {s["job_id"]: s for s in d.uc1_scores},
        "uc2": {s["job_id"]: s for s in d.uc2_scores},
        "uc2_strict": {s["job_id"]: s for s in d.strict_scores},
    }
    latest = {k: d.stores[k].latest_by_job() for k in scores}
    parts = []
    for n, r in enumerate(rows, 1):
        key = by_run.get(r["run_id"])
        if key not in latest:
            raise ValueError(f"gallery row from a run not in the report: {r['run_id']}")
        rec = latest[key][r["job_id"]]
        sc = scores[key].get(r["job_id"])
        if key == "uc1":
            ev = failures.uc1_evidence(rec, sc)
            prompt = rec["request"]["messages"][-1]["content"]
            tag = failures.uc1_tag(rec, sc)
        else:
            ev = failures.uc2_evidence(rec, sc)
            prompt = next(m["content"] for m in rec["request"]["messages"] if m["role"] == "user")
            tag = failures.uc2_tag(rec, sc)
        tag = r.get("tag") or tag
        facts = " · ".join(f"{k}: {v}" for k, v in ev["facts"] if v is not None)
        steps = "\n".join(f"  {i}. {s.split(': ', 1)[1]}" for i, s in
                          enumerate(ev.get("steps") or [], 1))  # fmt: skip
        block = [
            f"## {n}. {r['title']}",
            "",
            f"`{r['job_id']}` in run `{r['run_id']}` · tag `{tag}` ({failures.TAG_HELP[str(tag)]})",
            "",
            f"**What happened.** {r['note']}",
            "",
            f"**Facts.** {facts}",
            "",
            "**Prompt (excerpt).**",
            "",
            # the start only: benchmark problem statements are third-party text
            "```text\n" + failures.excerpt(prompt, 260, 0) + "\n```",
        ]
        if steps:
            block += ["", "**Trace.**", "", steps]
        if ev.get("text"):
            block += ["", "**Model output (excerpt).**", "", "```text\n" + ev["text"] + "\n```"]
        parts.append("\n".join(block))
    runs = [d.info[k] for k in ("uc1", "uc2", "uc2_strict")]
    return (
        "# Failure gallery: how diffusion fails differently\n\n"
        "Annotated examples for blog claims C6 and C11. Each one is a real job from a "
        "published run: the notes and picks are manual (`reports/failure_tags.csv`), the "
        "facts and excerpts are pulled from the raw logs by `dllm-eval report`. Long texts "
        "are cut in the middle. Counts per model for every tag are in "
        "[failure_modes.md](failure_modes.md).\n\n"
        + "\n\n".join(parts)
        + f"\n\n---\n\n*{footnote(d.cfg, *runs)}*\n"
    )


def decision_matrix_md(d: Data, tags: dict[str, list[failures.Failure]]) -> str:
    cfg, info = d.cfg, d.info
    seed = d.stores["uc1"].read_manifest()["experiment"].get("seed", 0)
    pp = {p.arm: p for p in pareto_points(d.uc1_calls, d.uc1_scores, seed=seed)}
    p95 = uc1_p95(d.uc1_calls)
    cost = {
        (c.panel, c.arm): c
        for c in assets.uc1_cost_points(d.uc1_calls, d.uc1_scores)
        + assets.uc2_cost_points(d.uc2_table)
    }
    t2 = {r["arm"]: r for r in d.uc2_table}
    dec = length_decode_rates(d.length_calls)
    jobs = _jobs_per_arm(d.stores["uc1"])
    bo = Counter(f.arm for f in tags["uc1"] if f.tag == "budget_out_hidden")
    viol = Counter(f.arm for f in tags["uc2_strict"] if f.tag == "schema_violation")
    strict_n = _jobs_per_arm(d.stores["uc2_strict"])

    def u1(a: str) -> str:
        p = pp[a]
        return f"{100 * p.pass1:.1f}% pass@1 at {p.latency_p50_s:.2f} s median"

    def u2(a: str) -> str:
        r = t2[a]
        return f"{100 * r['ex']:.1f}% EX, {r['task_p50']:.2f} s per task"

    def usd(panel: str, a: str, regular: bool = False) -> str:
        c = cost[(panel, a)]
        return f"${c.per_1k_regular if regular else c.per_1k:.2f}"

    m3, oss, haiku = "mercury-3:", "gpt-oss-120b-cerebras:", "claude-haiku-5-5:"
    seed2 = d.stores["uc2"].read_manifest()["experiment"].get("seed", 0)
    ahead: list[str] = []
    behind: list[str] = []
    for r in d.uc2_table:
        if r["arm"] == uc2.DEFAULT_REFERENCE:
            continue
        delta, lo, hi = uc2.paired_ex_delta(d.uc2_scores, r["arm"], seed=seed2)
        if lo > 0 or hi < 0:
            edge = " (bound at zero: borderline)" if min(abs(lo), abs(hi)) < 0.005 else ""
            (ahead if lo > 0 else behind).append(
                f"{assets.arm_label(r['arm'])} {100 * delta:+.1f} points, CI "
                f"{100 * lo:+.1f} to {100 * hi:+.1f}{edge}"
            )
    if not ahead:
        paired_line = "no arm is ahead with a CI that excludes zero"
    elif len(ahead) == 1:
        paired_line = f"only {ahead[0]} is ahead with a CI that excludes zero"
    else:
        paired_line = "ahead with a CI that excludes zero: " + "; ".join(ahead)
    if behind:
        paired_line += ". Behind: " + "; ".join(behind)
    halves = [100 * (r["ex_ci"][1] - r["ex_ci"][0]) / 2 for r in d.uc2_table]
    ci_lo, ci_hi = min(halves), max(halves)
    ci_range = f"±{ci_hi:.0f}" if round(ci_lo) == round(ci_hi) else f"±{ci_lo:.0f} to ±{ci_hi:.0f}"
    # Agent loop, cheapest per right answer: Luna first so far; the runner-up depends on
    # whether Mercury's promo price applies.
    m3_low, haiku_off = cost[("uc2", f"{m3}low")], cost[("uc2", f"{haiku}thinking-off")]
    if m3_low.per_1k_regular <= haiku_off.per_1k:
        agent_cheap_next = "then Mercury 3 `low`"
    elif m3_low.per_1k < haiku_off.per_1k:
        agent_cheap_next = (
            "then Mercury 3 `low` at promo prices, Claude Haiku thinking off at regular prices"
        )
    else:
        agent_cheap_next = "then Claude Haiku thinking off"
    long_m3 = dec.get(f"{m3}instant", {}).get("long", float("nan"))
    long_oss = dec.get(f"{oss}low", {}).get("long", float("nan"))
    long_haiku = dec.get(f"{haiku}thinking-off", {}).get("long", float("nan"))
    rows: list[list[str]] = [
        [
            "Interactive coding help, short answers, latency first",
            "**gpt-oss-120b on Cerebras** (`medium`); Mercury 3 `low` if the extra point of "
            "accuracy is worth the wait",
            f"gpt-oss `medium`: {u1(oss + 'medium')}. Mercury 3 `low`: {u1(m3 + 'low')}, "
            f"{pp[m3 + 'low'].latency_p50_s / pp[oss + 'medium'].latency_p50_s:.1f}x the median "
            f"latency. Mercury 3 `instant` ({u1(m3 + 'instant')}) is "
            f"{100 * (pp[m3 + 'low'].pass1 - pp[m3 + 'instant'].pass1):.1f} points below `low`",
        ],
        [
            "Code generation, accuracy first",
            "**GPT-6 Luna `medium`** or **Claude Haiku 5.5 `medium`**",
            f"Luna `medium`: {u1('gpt-6-luna:medium')}. Haiku `medium`: "
            f"{u1(haiku + 'medium')}. No Mercury 3 setting beats {u1(m3 + 'low')}",
        ],
        [
            "Code generation, cheapest per right answer",
            "**GPT-6 Luna `none`**, then Haiku thinking off",
            f"Luna `none`: {usd('uc1', 'gpt-6-luna:none')} per 1,000 passing answers; Haiku off "
            f"{usd('uc1', haiku + 'thinking-off')}; Mercury 3 `low` {usd('uc1', m3 + 'low')} "
            f"promo / {usd('uc1', m3 + 'low', True)} regular",
        ],
        [
            "Mercury 3 reasoning effort",
            "**Use `low`** (or `instant` for the lowest latency). Avoid `medium`/`high` for code",
            f"`medium`/`high` run out of budget on {_pct(bo[m3 + 'medium'] / jobs[m3 + 'medium'])}"
            f" / {_pct(bo[m3 + 'high'] / jobs[m3 + 'high'])} of calls, drop to "
            f"{100 * pp[m3 + 'high'].pass1:.1f}% pass@1 and push p95 latency to "
            f"{p95[m3 + 'high']:.0f} s (`low`: {p95[m3 + 'low']:.1f} s)",
        ],
        [
            "Long generated files, raw throughput",
            "**gpt-oss-120b on Cerebras**; Mercury 3 beats the frontier-lab fast models",
            f"Visible decode on ~8k-token answers (R3): gpt-oss {long_oss:,.0f} tok/s, "
            f"Mercury 3 `instant` {long_m3:,.0f}, Claude Haiku {long_haiku:,.0f}. Flat with length",
        ],
        [
            "Multi-step tool agent, latency first",
            "**Mercury 3 `low`/`instant`**, or gpt-oss `low` if you can tolerate turn-cap misses",
            f"Mercury 3 `low`: {u2(m3 + 'low')}; `instant`: {u2(m3 + 'instant')}; gpt-oss `low`: "
            f"{u2(oss + 'low')} but {_pct(t2[oss + 'low']['turn_cap'])} hit the turn cap; Haiku "
            f"off: {u2(haiku + 'thinking-off')}",
        ],
        [
            "Multi-step tool agent, accuracy first",
            "**Claude Haiku 5.5** (thinking off is enough)",
            f"Haiku off: {u2(haiku + 'thinking-off')}. Paired against Mercury 3 `low` on the same "
            f"questions: {paired_line}. Per-arm EX CIs are {ci_range} points",
        ],
        [
            "Strict JSON output with tools also offered",
            "**Not Mercury 3 today**; Luna, Gemini or Haiku (but Haiku stops using tools)",
            f"Mercury 3 broke the schema on {viol[m3 + 'instant']} of "
            f"{strict_n[m3 + 'instant']} tasks; every other arm 0 (R4b)",
        ],
        [
            "Agent loop, cheapest per right answer",
            f"**GPT-6 Luna `none`**, {agent_cheap_next}",
            f"$ per 1,000 correct tasks: Luna {usd('uc2', 'gpt-6-luna:none')}; Mercury 3 `low` "
            f"{usd('uc2', m3 + 'low')} promo / {usd('uc2', m3 + 'low', True)} regular; Haiku off "
            f"{usd('uc2', haiku + 'thinking-off')}; Mercury 3 `instant` "
            f"{usd('uc2', m3 + 'instant')} promo; gpt-oss `low` {usd('uc2', oss + 'low')}",
        ],
    ]
    table = _md_table(["If you need…", "Pick", "Evidence"], rows)
    return f"""# Decision matrix: when to reach for a diffusion LLM today

Blog claim C10 predicted that diffusion and autoregressive models would coexist, each suited
to different jobs. This is the measured version. The picks are judgements written on
{cfg.as_of} against these runs; every number in the Evidence column is regenerated from the
run data by `dllm-eval report`, so re-check the picks if a regeneration changes them.

{table}

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

*{footnote(cfg, info["uc1"], info["uc1_length"], info["uc2"], info["uc2_strict"])}*
"""


def readme_md(d: Data, figs: dict[str, Path], out_dir: Path) -> str:
    cfg, info = d.cfg, d.info
    rel: Callable[[Path], str] = lambda p: p.relative_to(out_dir).as_posix()  # noqa: E731
    runs = _md_table(
        ["Role", "Run id", "Commit", "Date"],
        [[k, f"`{i.run_id}`", f"`{i.commit}`", i.date] for k, i in info.items()]
        + ([["diffusion", f"`{cfg.runs.diffusion}`", "", ""]] if cfg.runs.diffusion else []),
    )
    gif = out_dir / "assets" / "diffusion_2026.gif"
    gif_line = (
        f"![Mercury 3 diffusing-mode replay](assets/{gif.name})\n\nDenoising dynamics per "
        "call (F7.2): [diffusion_dynamics.csv](assets/diffusion_dynamics.csv)."
        if cfg.runs.diffusion and gif.exists()
        else "*Pending: needs the R5 diffusing-mode capture (a live run).*"
    )
    return f"""# Blog assets: Mercury 3 vs autoregressive LLMs

Generated by `uv run dllm-eval report` from the runs below (configs/report.yaml). Every
figure has its data next to it as a CSV and an "as of" footnote. Don't edit generated files by
hand: fix the analysis code or `reports/failure_tags.csv` and regenerate. The asset list is
BLOG_ALIGNMENT §5; findings and caveats are in docs/M4_ANALYSIS_RESULTS.md.

{runs}

## Figure 1: speed vs. correctness on code generation (C1, C3)

![fig1]({rel(figs["fig1"])})

Data: [fig1_pareto_uc1.csv](assets/fig1_pareto_uc1.csv).

## Figure 2: per-call vs. per-task speed in the SQL agent (C4)

![fig2]({rel(figs["fig2"])})

Data: [fig2_task_vs_call_speedup_uc2.csv](assets/fig2_task_vs_call_speedup_uc2.csv).

## Figure 3: latency distributions (C1)

![fig3]({rel(figs["fig3"])})

Data: [fig3_latency_cdf.csv](assets/fig3_latency_cdf.csv) (quantiles at 1% steps).

## Figure 4: cost per success, promo and regular Mercury prices

![fig4]({rel(figs["fig4"])})

Data: [fig4_cost_per_success.csv](assets/fig4_cost_per_success.csv).

## Diffusion replay (C2)

{gif_line}

## Tables

- [table_reliability.md](table_reliability.md): tool-call validity, strict-schema adherence,
  runaway generation (C5)
- [failure_modes.md](failure_modes.md): failure tags per model (F6.6)
- [failure_gallery.md](failure_gallery.md): annotated examples (C6, C11)
- [decision_matrix.md](decision_matrix.md): when to pick a dLLM (C10)

*Report as of {cfg.as_of}; client {cfg.client}; {cfg.service_tier}.*
"""


def write_diffusion(cfg: ReportConfig, runs_dir: Path, assets_dir: Path) -> list[Path]:
    """R5: the diffusing-mode GIF (F6.5) and per-call denoising dynamics (F7.2)."""
    if not cfg.runs.diffusion:
        return []
    store = RunStore(runs_dir / cfg.runs.diffusion)
    if not store.exists():
        raise FileNotFoundError(f"run {cfg.runs.diffusion} not found under {runs_dir}")
    recs = store.latest_by_job()
    rows = diffusion.dynamics_rows(recs.values())
    if not rows:
        raise ValueError(f"run {cfg.runs.diffusion} has no diffusing frames")
    job = cfg.diffusion_job or rows[0]["job_id"]
    rec = recs[job]
    info = RunInfo.from_store(store)
    prompt = next(m["content"] for m in rec["request"]["messages"] if m["role"] == "user")
    gif = diffusion.render_gif(
        diffusion.frames_of(rec),
        assets_dir / "diffusion_2026.gif",
        title=f"{assets.arm_label(rec['arm']).removesuffix('-diffusing')} · diffusing mode",
        footnote=(
            f'Prompt: "{prompt}" Job {job}. Orange characters are not yet final. '
            + footnote(cfg, info)
        ),
    )
    csv_path = assets_dir / "diffusion_dynamics.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False, float_format="%.4f")
    return [gif, csv_path]


def build(
    cfg: ReportConfig,
    runs_dir: Path,
    analysis_dir: Path,
    out_dir: Path,
    log: Callable[[str], Any] = print,
) -> list[Path]:
    """Write every asset; return the paths written."""
    d = load_data(cfg, runs_dir, analysis_dir)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    figs = write_figures(d, assets_dir)
    log("figures: " + ", ".join(p.name for p in figs.values()))
    extra = write_diffusion(cfg, runs_dir, assets_dir)
    tags = tag_all(d)
    modes, modes_df = failure_modes_md(d, tags)
    written = list(figs.values()) + extra
    for name, text in (
        ("table_reliability.md", reliability_md(d, tags)),
        ("failure_modes.md", modes),
        ("failure_gallery.md", gallery_md(d)),
        ("decision_matrix.md", decision_matrix_md(d, tags)),
        ("README.md", readme_md(d, figs, out_dir)),
    ):
        path = out_dir / name
        path.write_text(text)
        written.append(path)
    modes_df.to_csv(assets_dir / "failure_modes.csv", index=False)
    written.append(assets_dir / "failure_modes.csv")
    log(f"wrote {len(written)} files under {out_dir}")
    return written
