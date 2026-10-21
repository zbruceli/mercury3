"""Blog figures that combine UC1 and UC2 (F6.4): latency CDFs and cost per success.
Offline only: built from the frames `analyze`/`score` produce from run directories.

Both are small multiples (UC1 left, UC2 right), each panel on its own axis. Color keeps the
model-family encoding from charts.py; within a family, line style (CDF) or the bar label
(cost) carries the model, and both figures write their data as CSV.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

from dllm_eval.analysis.charts import (
    FAMILY_COLOR,
    GRID,
    MODEL_STYLE,
    SURFACE,
    TEXT,
    TEXT_2,
    add_footnote,
    add_header,
)
from dllm_eval.analysis.tables import latency_sample
from dllm_eval.analysis.uc2 import latency_tasks
from dllm_eval.stats import percentile

# One line per model at its headline setting, plus Mercury 3's effort extremes.
CDF_ARMS = [
    "mercury-3:instant",
    "mercury-3:low",
    "mercury-3:high",
    "gpt-oss-120b-cerebras:medium",
    "claude-haiku-5-5:thinking-off",
    "gpt-6-luna:none",
    "gemini-3.5-flash-lite:minimal",
]
# Within a family, line style tells models (or Mercury 3 efforts) apart.
LINESTYLE: dict[str, Any] = {
    "mercury-3:instant": (0, (5, 2)),
    "mercury-3:low": "solid",
    "mercury-3:high": (0, (1, 1.5)),
    "gpt-oss-120b-cerebras:medium": "solid",
    "claude-haiku-5-5:thinking-off": "solid",
    "gpt-6-luna:none": (0, (5, 2)),
    "gemini-3.5-flash-lite:minimal": (0, (1, 1.5)),
}
QUANTILES = [q / 100 for q in range(1, 100)] + [0.995, 1.0]


def arm_label(arm: str) -> str:
    model, setting = arm.split(":", 1)
    return f"{MODEL_STYLE.get(model, ('', '', model))[2]} · {setting}"


def arm_family(arm: str) -> str:
    return MODEL_STYLE.get(arm.split(":", 1)[0], ("frontier-lab AR", "", ""))[0]


def _style(ax: Any) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=8)


# --- latency CDF ----------------------------------------------------------------------


def uc1_latency_samples(calls: pd.DataFrame, arms: Iterable[str]) -> dict[str, list[float]]:
    """End-to-end seconds per latency-eligible UC1 call, per arm."""
    lat = latency_sample(calls)
    return {a: sorted(lat.loc[lat["arm"] == a, "total_s"].dropna().tolist()) for a in arms}


def uc2_latency_samples(tasks: pd.DataFrame, arms: Iterable[str]) -> dict[str, list[float]]:
    """Wall seconds per latency-eligible UC2 task, per arm."""
    lat = latency_tasks(tasks)
    return {a: sorted(lat.loc[lat["arm"] == a, "wall_s"].dropna().tolist()) for a in arms}


def cdf_table(panels: dict[str, dict[str, list[float]]]) -> pd.DataFrame:
    """Long table of quantiles (the plotted curves at 1% steps) per panel and arm."""
    rows = []
    for panel, samples in panels.items():
        for arm, xs in samples.items():
            if not xs:
                continue
            for q in QUANTILES:
                rows.append(
                    {"panel": panel, "arm": arm, "n": len(xs), "quantile": q,
                     "seconds": percentile(xs, 100 * q)}
                )  # fmt: skip
    return pd.DataFrame(rows)


def plot_latency_cdf(
    panels: dict[str, dict[str, list[float]]],
    out_png: Path,
    *,
    titles: dict[str, str],
    title: str,
    subtitle: str,
    footnote: str | None = None,
) -> Path:
    """Empirical CDF per arm, one panel per use case, log-scale seconds."""
    fig, axes = plt.subplots(1, len(panels), figsize=(11, 5.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    axes = list(axes) if len(panels) > 1 else [axes]
    arms_seen: list[str] = []
    nice = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200]
    for ax, (panel, samples) in zip(axes, panels.items(), strict=True):
        _style(ax)
        lo = min(xs[0] for xs in samples.values() if xs)
        hi = max(xs[-1] for xs in samples.values() if xs)
        for arm, xs in samples.items():
            if not xs:
                continue
            if arm not in arms_seen:
                arms_seen.append(arm)
            n = len(xs)
            ys = [(i + 1) / n for i in range(n)]
            ax.step(
                xs, [100 * y for y in ys], where="post", linewidth=1.8,
                color=FAMILY_COLOR[arm_family(arm)], linestyle=LINESTYLE.get(arm, "solid"),
                zorder=3,
            )  # fmt: skip
        for q in (50, 95):
            ax.axhline(q, color=TEXT_2, linewidth=0.8, linestyle=(0, (2, 3)), zorder=1)
            ax.annotate(
                f"p{q}", (1.0, q), xycoords=("axes fraction", "data"), xytext=(2, 0),
                textcoords="offset points", va="center", fontsize=7, color=TEXT_2,
            )  # fmt: skip
        ax.set_xscale("log")
        ax.set_xlim(lo / 1.3, hi * 1.3)
        ax.xaxis.set_major_locator(FixedLocator([v for v in nice if lo / 1.3 <= v <= hi * 1.3]))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g} s"))
        ax.set_ylim(0, 101)
        ax.grid(True, which="major", color=GRID, linewidth=0.8, zorder=0)
        ax.set_title(titles.get(panel, panel), loc="left", fontsize=9.5, color=TEXT, pad=6)
        ax.set_xlabel("seconds (log scale)", color=TEXT_2, fontsize=8.5)
    axes[0].set_ylabel("share finished within x seconds (%)", color=TEXT_2, fontsize=8.5)
    handles = [
        Line2D(
            [],
            [],
            color=FAMILY_COLOR[arm_family(a)],
            linestyle=LINESTYLE.get(a, "solid"),
            linewidth=2,
            label=arm_label(a),
        )
        for a in arms_seen
    ]
    top = add_header(fig, title, subtitle, wrap=165)
    bottom = add_footnote(fig, footnote) if footnote else 0.0
    fig.legend(
        handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=8,
        labelcolor=TEXT_2, bbox_to_anchor=(0.5, bottom),
    )  # fmt: skip
    fig.tight_layout(rect=(0, bottom + 0.5 / fig.get_figheight(), 0.98, top))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=SURFACE)
    plt.close(fig)
    return out_png


# --- cost per success -----------------------------------------------------------------


@dataclass(frozen=True)
class CostPoint:
    panel: str
    arm: str
    successes: int
    jobs: int
    success_rate: float  # successes / jobs (UC1: pass rate over calls; UC2: EX over tasks)
    cost_usd: float
    cost_usd_regular: float
    per_1k: float  # USD per 1,000 successes, promo price
    per_1k_regular: float  # same at the regular price (equal unless the model has a promo)


def uc1_cost_points(calls: pd.DataFrame, scores: Iterable[dict[str, Any]]) -> list[CostPoint]:
    """Spend over every call in the arm (warm-ups and retries included: they were paid)
    divided by passing answers."""
    passes: dict[str, int] = defaultdict(int)
    for s in scores:
        if s["status"] == "pass":
            passes[s["arm"]] += 1
    out = []
    for arm, g in calls.groupby("arm", sort=True):
        k = passes.get(str(arm), 0)
        cost = float(g["cost_usd"].fillna(0).sum())
        reg = float(g["cost_usd_regular"].fillna(0).sum())
        out.append(
            CostPoint("uc1", str(arm), k, len(g), k / len(g), cost, reg,
                      1000 * cost / k if k else float("nan"),
                      1000 * reg / k if k else float("nan"))
        )  # fmt: skip
    return out


def uc2_cost_points(table: Iterable[dict[str, Any]]) -> list[CostPoint]:
    """From uc2.arm_table rows: spend per arm over correct tasks."""
    out = []
    for r in table:
        k = int(r["correct"])
        out.append(
            CostPoint("uc2", r["arm"], k, int(r["tasks"]), k / r["tasks"], r["cost_usd"],
                      r["cost_usd_regular"],
                      1000 * r["cost_usd"] / k if k else float("nan"),
                      1000 * r["cost_usd_regular"] / k if k else float("nan"))
        )  # fmt: skip
    return out


def plot_cost_per_success(
    points: list[CostPoint],
    out_png: Path,
    *,
    titles: dict[str, str],
    rate_label: dict[str, str],
    title: str,
    subtitle: str,
    footnote: str | None = None,
) -> Path:
    """Horizontal bars of USD per 1,000 successes, one panel per use case. A model on a
    promotional price gets a solid bar at the promo price and a hatched extension to the
    regular price."""
    panels = list(dict.fromkeys(p.panel for p in points))
    n_max = max(sum(p.panel == k for p in points) for k in panels)
    fig, axes = plt.subplots(1, len(panels), figsize=(11, 0.36 * n_max + 2.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    axes = list(axes) if len(panels) > 1 else [axes]
    for ax, panel in zip(axes, panels, strict=True):
        _style(ax)
        pts = sorted((p for p in points if p.panel == panel), key=lambda p: -p.per_1k)
        xmax = max(max(p.per_1k, p.per_1k_regular) for p in pts)
        for i, p in enumerate(pts):
            color = FAMILY_COLOR[arm_family(p.arm)]
            ax.barh(i, p.per_1k, height=0.62, color=color, zorder=2)
            promo = p.per_1k_regular > p.per_1k * 1.001
            if promo:
                ax.barh(
                    i, p.per_1k_regular - p.per_1k, left=p.per_1k, height=0.62,
                    color=SURFACE, edgecolor=color, hatch="////", linewidth=0.8, zorder=2,
                )  # fmt: skip
            value = f"${p.per_1k:.2f}" + (f" / ${p.per_1k_regular:.2f} regular" if promo else "")
            ax.annotate(
                f"{value} · {100 * p.success_rate:.0f}% {rate_label[panel]}",
                (max(p.per_1k, p.per_1k_regular), i), xytext=(4, 0), textcoords="offset points",
                va="center", fontsize=7, color=TEXT_2, parse_math=False,
            )  # fmt: skip
        ax.set_yticks(range(len(pts)), [arm_label(p.arm) for p in pts], fontsize=7.5, color=TEXT)
        ax.set_xlim(0, xmax * 2.1)
        ax.grid(True, axis="x", color=GRID, linewidth=0.8, zorder=0)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:g}"))
        ax.set_title(titles.get(panel, panel), loc="left", fontsize=9.5, color=TEXT, pad=6)
    handles = [
        Line2D([], [], color=c, marker="s", linestyle="", markersize=8, label=f)
        for f, c in FAMILY_COLOR.items()
    ]
    top = add_header(fig, title, subtitle, wrap=165)
    bottom = add_footnote(fig, footnote) if footnote else 0.0
    fig.legend(
        handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=8,
        labelcolor=TEXT_2, bbox_to_anchor=(0.5, bottom),
    )  # fmt: skip
    fig.tight_layout(rect=(0, bottom + 0.3 / fig.get_figheight(), 1, top))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=SURFACE)
    plt.close(fig)
    return out_png


def write_cost_csv(points: list[CostPoint], out_csv: Path) -> Path:
    pd.DataFrame([p.__dict__ for p in points]).to_csv(out_csv, index=False, float_format="%.6f")
    return out_csv
