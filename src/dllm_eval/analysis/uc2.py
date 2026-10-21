"""UC2 analysis (F6.4 UC2 part): task and turn tables, accuracy, reliability, cost per
correct answer, and the task-level vs call-level latency ratio chart. Offline only: built
from calls.jsonl + uc2_scores.jsonl.

Latency eligibility (CLAUDE.md, extended to tasks): a task counts for latency only if it
finished (run status ok), isn't a warm-up, none of its turns was retried, and it wasn't in
flight across a run pause. Its turns count for per-call latency on the same terms, so both
ratios in the chart are computed over the same set of tasks.
"""

from __future__ import annotations

import random
import statistics
import textwrap
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from dllm_eval.analysis.charts import FAMILY_COLOR, GRID, MODEL_STYLE, SURFACE, TEXT, TEXT_2
from dllm_eval.analysis.load import mark_pause_spanning
from dllm_eval.logging_store import RunStore
from dllm_eval.stats import percentile

DEFAULT_REFERENCE = "mercury-3:low"


def _sum(xs: Iterable[int | None]) -> int | None:
    vals = list(xs)
    return None if any(v is None for v in vals) else sum(v for v in vals if v is not None)


def task_rows(store: RunStore) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(one row per task, one row per model turn) from the latest record of each job."""
    tasks, turns = [], []
    for rec in store.latest_by_job().values():
        t = rec.get("task") or {}
        tr = t.get("turns") or []
        results = [x["result"] for x in tr]
        usage = [r.get("usage") or {} for r in results]
        tool_recs = [c for x in tr for c in x.get("tools", [])]
        run_sql = [c for c in tool_recs if c["name"] == "run_sql" and c["validity"] == "valid"]
        nudges = sum(bool(x.get("nudged")) for x in tr)
        invalid = sum(c["validity"] != "valid" for c in tool_recs)
        row = {
            "job_id": rec["job_id"],
            "arm": rec["arm"],
            "model": rec["model"],
            "item_id": rec["item_id"],
            "repeat": rec["repeat"],
            "warmup": rec.get("warmup", False),
            "status": rec["status"],
            "outcome": t.get("outcome"),
            "turns": len(tr),
            "wall_s": t.get("wall_s"),
            "model_s": t.get("model_s"),
            "tool_s": t.get("tool_s"),
            "retried": any(r.get("retries", 0) > 0 for r in results)
            or (rec.get("error") or {}).get("attempts", 1) > 1,
            "tool_calls": len(tool_recs),
            "invalid_calls": invalid,
            "run_sql": len(run_sql),
            "sql_errors": sum(c["status"] == "sql_error" for c in run_sql),
            "sql_timeouts": sum(c["status"] == "timeout" for c in run_sql),
            "nudges": nudges,
            # a malformed turn (no tool call) or an invalid call, followed by an answer
            "recovered": (nudges + invalid) > 0 and t.get("outcome") == "submitted",
            "had_malformed": (nudges + invalid) > 0,
            "input_tokens": _sum(u.get("input_tokens") for u in usage),
            "cached_input_tokens": _sum(u.get("cached_input_tokens") for u in usage),
            "output_tokens": _sum(u.get("output_tokens") for u in usage),
            "reasoning_tokens": _sum(u.get("reasoning_tokens") for u in usage),
            "cost_usd": rec.get("cost_usd"),
            "cost_usd_regular": rec.get("cost_usd_regular"),
            "started_at": rec.get("started_at"),
            "spans_pause": False,
        }
        tasks.append(row)
        for x, r in zip(tr, results, strict=True):
            timing = r.get("timing") or {}
            turns.append(
                {
                    "job_id": rec["job_id"],
                    "arm": rec["arm"],
                    "index": x["index"],
                    "total_s": timing.get("t_done"),
                    "ttft_s": timing.get("t_first_content"),
                    "retries": r.get("retries", 0),
                    "output_tokens": (r.get("usage") or {}).get("output_tokens"),
                    "finish_reason": r.get("finish_reason"),
                }
            )
    mark_pause_spanning(tasks)
    return tasks, turns


def latency_tasks(tasks: pd.DataFrame) -> pd.DataFrame:
    return tasks[
        (tasks["status"] == "ok")
        & (~tasks["warmup"].astype(bool))
        & (~tasks["retried"].astype(bool))
        & (~tasks["spans_pause"].astype(bool))
    ]


def _boot_mean(xs: list[float], seed: int, n_boot: int = 2000) -> tuple[float, float]:
    if len(xs) < 2:
        return (xs[0], xs[0]) if xs else (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(xs)
    boots = sorted(statistics.fmean(xs[rng.randrange(n)] for _ in range(n)) for _ in range(n_boot))
    return percentile(boots, 2.5), percentile(boots, 97.5)


def _rate(num: float, den: float) -> float | None:
    return num / den if den else None


def arm_table(
    tasks: pd.DataFrame, turns: pd.DataFrame, scores: list[dict[str, Any]], *, seed: int = 0
) -> list[dict[str, Any]]:
    """Accuracy, reliability, latency and cost per arm. EX is the mean over questions of
    each question's pass rate across repeats, with a bootstrap 95% CI over questions;
    API errors count as failures, harness errors (gold query failed) are left out."""
    by_job = {s["job_id"]: s for s in scores}
    lat = latency_tasks(tasks)
    lat_jobs = set(lat["job_id"])
    out = []
    for arm, g in tasks.groupby("arm", sort=True):
        sc = [by_job[j] for j in g["job_id"] if j in by_job]
        sc = [s for s in sc if s["status"] != "harness_error"]
        per_q: dict[str, list[bool]] = defaultdict(list)
        per_q_bird: dict[str, list[bool]] = defaultdict(list)
        per_diff: dict[str, list[bool]] = defaultdict(list)
        for s in sc:
            per_q[s["item_id"]].append(s["ex"])
            per_q_bird[s["item_id"]].append(s["ex_bird"])
            per_diff[s["difficulty"]].append(s["ex"])
        rates = [statistics.fmean(v) for v in per_q.values()]
        lo, hi = _boot_mean(rates, seed)
        correct = sum(s["ex"] for s in sc)
        ok = g[g["status"] == "ok"]
        lt = lat[lat["arm"] == arm]
        tt = turns[turns["job_id"].isin(lat_jobs) & (turns["arm"] == arm)]
        n = len(g)
        known_cache = ok.dropna(subset=["cached_input_tokens", "input_tokens"])
        cost = float(g["cost_usd"].fillna(0).sum())
        cost_reg = float(g["cost_usd_regular"].fillna(0).sum())
        out.append(
            {
                "arm": arm,
                "tasks": n,
                "questions": len(per_q),
                "ex": statistics.fmean(rates) if rates else None,
                "ex_ci": (lo, hi),
                "ex_bird": (
                    statistics.fmean(statistics.fmean(v) for v in per_q_bird.values())
                    if per_q_bird
                    else None
                ),
                "ex_by_difficulty": {d: statistics.fmean(v) for d, v in sorted(per_diff.items())},
                "correct": int(correct),
                "api_errors": int((g["status"] != "ok").sum()),
                "submitted": _rate((g["outcome"] == "submitted").sum(), n),
                "turn_cap": _rate((g["outcome"] == "turn_cap").sum(), n),
                "budget_out": _rate((g["outcome"] == "budget_out").sum(), n),
                "invalid_answer": _rate((g["outcome"] == "invalid_answer").sum(), n),
                "empty_response": _rate((g["outcome"] == "empty_response").sum(), n),
                "pred_sql_error": _rate(
                    sum(s["pred_status"] in ("sql_error", "timeout", "too_many_rows") for s in sc),
                    sum(s["pred_status"] is not None for s in sc),
                ),
                "tool_calls": int(ok["tool_calls"].sum()),
                "tool_call_validity": _rate(
                    ok["tool_calls"].sum() - ok["invalid_calls"].sum(), ok["tool_calls"].sum()
                ),
                "invalid_sql_rate": _rate(ok["sql_errors"].sum(), ok["run_sql"].sum()),
                "sql_timeout_rate": _rate(ok["sql_timeouts"].sum(), ok["run_sql"].sum()),
                "malformed_tasks": _rate(ok["had_malformed"].sum(), len(ok)),
                "recoveries": int(ok["recovered"].sum()),
                "malformed_task_count": int(ok["had_malformed"].sum()),
                "turns_mean": float(ok["turns"].mean()) if len(ok) else None,
                "latency_n": len(lt),
                "task_p50": percentile(lt["wall_s"].tolist(), 50) if len(lt) else None,
                "task_p95": percentile(lt["wall_s"].tolist(), 95) if len(lt) else None,
                "call_p50": percentile(tt["total_s"].dropna().tolist(), 50) if len(tt) else None,
                "call_p95": percentile(tt["total_s"].dropna().tolist(), 95) if len(tt) else None,
                "tool_s_p50": percentile(lt["tool_s"].tolist(), 50) if len(lt) else None,
                "input_tokens_per_task": float(ok["input_tokens"].mean()) if len(ok) else None,
                "output_tokens_per_task": float(ok["output_tokens"].mean()) if len(ok) else None,
                "reasoning_tokens": (
                    None
                    if ok["reasoning_tokens"].isna().all()
                    else int(ok["reasoning_tokens"].fillna(0).sum())
                ),
                "cache_hit_share": _rate(
                    known_cache["cached_input_tokens"].sum(), known_cache["input_tokens"].sum()
                ),
                "cost_usd": cost,
                "cost_usd_regular": cost_reg,
                "cost_per_task": cost / n if n else None,
                "cost_per_correct": cost / correct if correct else None,
                "cost_per_correct_regular": cost_reg / correct if correct else None,
            }
        )
    return out


def _pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{100 * v:.{digits}f}%"


def _num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def accuracy_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Arm | Tasks | EX (95% CI) | EX (BIRD rule) | simple / moderate / challenging | "
        "Turns | Task p50 / p95 (s) | Call p50 (s) | $ / task | $ / correct (promo / regular) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda r: -(r["ex"] or 0)):
        lo, hi = r["ex_ci"]
        d = r["ex_by_difficulty"]
        diff = " / ".join(_pct(d.get(k), 0) for k in ("simple", "moderate", "challenging"))
        lines.append(
            f"| `{r['arm']}` | {r['tasks']} | {_pct(r['ex'])} ({_pct(lo)}-{_pct(hi)}) | "
            f"{_pct(r['ex_bird'])} | {diff} | {_num(r['turns_mean'], 1)} | "
            f"{_num(r['task_p50'])} / {_num(r['task_p95'])} | {_num(r['call_p50'])} | "
            f"{_num(r['cost_per_task'], 5)} | {_num(r['cost_per_correct'], 5)} / "
            f"{_num(r['cost_per_correct_regular'], 5)} |"
        )
    return "\n".join(lines)


def reliability_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Arm | Answered | Turn cap | Budget out | Invalid answer | Empty | API errors | "
        "Tool calls | Valid calls | Invalid SQL (run_sql) | Answer SQL fails | "
        "Malformed tasks (recovered) | Cache hit |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['arm']}` | {_pct(r['submitted'])} | {_pct(r['turn_cap'])} | "
            f"{_pct(r['budget_out'])} | {_pct(r['invalid_answer'])} | "
            f"{_pct(r['empty_response'])} | {r['api_errors']} | {r['tool_calls']} | "
            f"{_pct(r['tool_call_validity'], 2)} | {_pct(r['invalid_sql_rate'])} | "
            f"{_pct(r['pred_sql_error'])} | {r['malformed_task_count']} "
            f"({r['recoveries']}) | {_pct(r['cache_hit_share'], 0)} |"
        )
    return "\n".join(lines)


# --- speed-up chart -------------------------------------------------------------------


@dataclass(frozen=True)
class SpeedupPoint:
    arm: str
    reference: str
    call_ratio: float  # p50 call latency (arm) / p50 call latency (reference)
    call_ci: tuple[float, float]
    task_ratio: float  # p50 task wall time (arm) / p50 task wall time (reference)
    task_ci: tuple[float, float]
    call_p50_s: float
    task_p50_s: float
    turns_mean: float


def _ratio_ci(a: list[float], b: list[float], seed: int, n_boot: int = 1000) -> tuple[float, float]:
    """Bootstrap CI of median(a) / median(b), resampling each side independently."""
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        ma = statistics.median(a[rng.randrange(len(a))] for _ in a)
        mb = statistics.median(b[rng.randrange(len(b))] for _ in b)
        boots.append(ma / mb)
    boots.sort()
    return percentile(boots, 2.5), percentile(boots, 97.5)


def speedup_points(
    tasks: pd.DataFrame, turns: pd.DataFrame, *, reference: str = DEFAULT_REFERENCE, seed: int = 0
) -> list[SpeedupPoint]:
    lat = latency_tasks(tasks)
    tt = turns[turns["job_id"].isin(set(lat["job_id"]))]

    def samples(arm: str) -> tuple[list[float], list[float]]:
        calls = tt.loc[tt["arm"] == arm, "total_s"].dropna().tolist()
        walls = lat.loc[lat["arm"] == arm, "wall_s"].dropna().tolist()
        return calls, walls

    ref_calls, ref_walls = samples(reference)
    if not ref_calls or not ref_walls:
        raise ValueError(f"reference arm {reference!r} has no latency-eligible tasks")
    out = []
    for arm in sorted(set(lat["arm"])):
        calls, walls = samples(arm)
        if not calls or not walls:
            continue
        out.append(
            SpeedupPoint(
                arm=arm,
                reference=reference,
                call_ratio=statistics.median(calls) / statistics.median(ref_calls),
                call_ci=_ratio_ci(calls, ref_calls, seed),
                task_ratio=statistics.median(walls) / statistics.median(ref_walls),
                task_ci=_ratio_ci(walls, ref_walls, seed + 1),
                call_p50_s=statistics.median(calls),
                task_p50_s=statistics.median(walls),
                turns_mean=float(lat.loc[lat["arm"] == arm, "turns"].mean()),
            )
        )
    return out


def _label(arm: str) -> str:
    model, setting = arm.split(":", 1)
    return f"{MODEL_STYLE.get(model, ('', '', model))[2]} · {setting}"


def plot_speedup(points: list[SpeedupPoint], out_png: Path, *, title: str, subtitle: str) -> Path:
    """Paired horizontal bars per arm: call-level and task-level latency ratio against the
    reference arm (1x = as fast as the reference; >1 = slower)."""
    pts = sorted(points, key=lambda p: p.task_ratio)
    fig, ax = plt.subplots(figsize=(10, 0.55 * len(pts) + 1.8), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    h = 0.38
    for i, p in enumerate(pts):
        family = MODEL_STYLE.get(p.arm.split(":", 1)[0], ("frontier-lab AR", "", ""))[0]
        color = FAMILY_COLOR[family]
        for dy, ratio, ci, alpha, kind in (
            (-h / 2, p.call_ratio, p.call_ci, 0.45, "per call"),
            (h / 2, p.task_ratio, p.task_ci, 1.0, "per task"),
        ):
            ax.barh(i + dy, ratio, height=h * 0.92, color=color, alpha=alpha, zorder=2)
            ax.hlines(i + dy, ci[0], ci[1], color=TEXT_2, linewidth=1, zorder=3)
            ax.annotate(
                f"{ratio:.2f}x {kind}", (max(ratio, ci[1]), i + dy), xytext=(4, 0),
                textcoords="offset points", va="center", fontsize=7, color=TEXT_2,
            )  # fmt: skip
    ax.axvline(1.0, color=TEXT, linewidth=1, zorder=1)
    ax.set_yticks(range(len(pts)), [_label(p.arm) for p in pts], fontsize=8, color=TEXT)
    ax.set_xlabel(
        f"Median latency relative to {_label(pts[0].reference) if pts else ''} "
        "(1x = same; higher = slower). Faint bar: per model call; solid bar: per task",
        color=TEXT_2,
        fontsize=8.5,
    )
    ax.grid(True, axis="x", color=GRID, linewidth=0.8, zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=8)
    xmax = max([max(p.call_ci[1], p.task_ci[1]) for p in pts] + [1.0])
    ax.set_xlim(0, xmax * 1.22)
    fig.suptitle(title, x=0.02, y=0.985, ha="left", fontsize=13, color=TEXT, fontweight="bold")
    ax.set_title(textwrap.fill(subtitle, 120), loc="left", fontsize=8, color=TEXT_2, pad=8)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=SURFACE)
    plt.close(fig)
    return out_png


def write_speedup_csv(points: list[SpeedupPoint], out_csv: Path) -> Path:
    rows = [
        {
            **{k: v for k, v in p.__dict__.items() if not k.endswith("_ci")},
            "call_ci_low": p.call_ci[0],
            "call_ci_high": p.call_ci[1],
            "task_ci_low": p.task_ci[0],
            "task_ci_high": p.task_ci[1],
        }
        for p in points
    ]
    pd.DataFrame(rows).to_csv(out_csv, index=False, float_format="%.4f")
    return out_csv
