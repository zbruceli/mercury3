"""Static blog charts (F6.4). Offline only: built from calls.parquet + scores.jsonl.

Color encodes model *family*, not model: a scatter needs every pair of colors to stay
distinguishable, and only three categorical slots pass that check (dataviz palette,
validated all-pairs, light mode). Marker shape and a direct label on every point carry
the individual model, and each chart also writes its data as a CSV table.
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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

from dllm_eval.analysis.tables import latency_sample
from dllm_eval.stats import percentile

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e6e5e1"

FAMILY_COLOR = {
    "diffusion": "#2a78d6",  # slot 1 blue
    "fast-hardware AR": "#eb6834",  # slot 2 orange
    "frontier-lab AR": "#1baf7a",  # slot 3 aqua (below 3:1 contrast: points are labeled)
}
MODEL_STYLE: dict[str, tuple[str, str, str]] = {  # model -> (family, marker, label)
    "mercury-3": ("diffusion", "o", "Mercury 3"),
    "mercury-2.5": ("diffusion", "s", "Mercury 2.5"),
    "gpt-oss-120b-cerebras": ("fast-hardware AR", "P", "gpt-oss-120b (Cerebras)"),
    "claude-haiku-5-5": ("frontier-lab AR", "^", "Claude Haiku 5.5"),
    "gpt-6-luna": ("frontier-lab AR", "D", "GPT-6 Luna"),
    "gemini-3.5-flash-lite": ("frontier-lab AR", "v", "Gemini 3.5 Flash-Lite"),
}
# Order of each model's arms along its connecting line (low to high reasoning).
ARM_ORDER = ["instant", "none", "minimal", "thinking-off", "low", "medium", "high"]


@dataclass(frozen=True)
class ParetoPoint:
    arm: str
    model: str
    setting: str
    latency_p50_s: float
    pass1: float
    ci_low: float
    ci_high: float
    problems: int


def _bootstrap_mean_ci(xs: list[float], seed: int, n_boot: int = 2000) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(xs)
    boots = sorted(statistics.fmean(xs[rng.randrange(n)] for _ in range(n)) for _ in range(n_boot))
    return percentile(boots, 2.5), percentile(boots, 97.5)


def pareto_points(
    calls: pd.DataFrame, scores: Iterable[dict[str, Any]], *, seed: int = 0
) -> list[ParetoPoint]:
    """One point per arm: p50 total latency (latency-eligible calls) vs pass@1 over all
    problems (mean of per-problem pass rates), with a bootstrap 95% CI over problems."""
    per: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in scores:
        if r["status"] == "harness_error":
            continue
        per[r["arm"]][r["item_id"]].append(r["status"] == "pass")
    lat = latency_sample(calls)
    points = []
    for arm, items in sorted(per.items()):
        rates = [sum(v) / len(v) for v in items.values()]
        lo, hi = _bootstrap_mean_ci(rates, seed)
        model, setting = arm.split(":", 1)
        totals = lat.loc[lat["arm"] == arm, "total_s"].dropna().tolist()
        points.append(
            ParetoPoint(
                arm=arm,
                model=model,
                setting=setting,
                latency_p50_s=percentile(totals, 50),
                pass1=statistics.fmean(rates),
                ci_low=lo,
                ci_high=hi,
                problems=len(rates),
            )
        )
    return points


def plot_pareto(points: list[ParetoPoint], out_png: Path, *, title: str, subtitle: str) -> Path:
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    labels: list[tuple[str, float, float]] = []
    by_model: dict[str, list[ParetoPoint]] = defaultdict(list)
    for p in points:
        by_model[p.model].append(p)
    for model, pts in by_model.items():
        family, marker, label = MODEL_STYLE.get(model, ("frontier-lab AR", "o", model))
        color = FAMILY_COLOR[family]
        pts.sort(key=lambda p: ARM_ORDER.index(p.setting) if p.setting in ARM_ORDER else 99)
        xs = [p.latency_p50_s for p in pts]
        ys = [100 * p.pass1 for p in pts]
        if len(pts) > 1:
            ax.plot(xs, ys, color=color, linewidth=2, alpha=0.55, zorder=2, solid_capstyle="round")
        for p in pts:
            ax.vlines(
                p.latency_p50_s,
                100 * p.ci_low,
                100 * p.ci_high,
                color=color,
                linewidth=1.5,
                alpha=0.6,
                zorder=2,
            )
        ax.scatter(
            xs, ys, s=78, marker=marker, color=color, edgecolors=SURFACE, linewidths=2, zorder=3
        )
        for p in pts:
            labels.append((f"{label} · {p.setting}", p.latency_p50_s, 100 * p.pass1))
    ax.set_xscale("log")
    lo = min(p.latency_p50_s for p in points)
    hi = max(p.latency_p50_s for p in points)
    nice = [0.1, 0.2, 0.3, 0.5, 1, 2, 3, 5, 10, 20, 30, 60]
    ax.set_xlim(lo / 1.25, hi * 1.25)
    ax.xaxis.set_major_locator(FixedLocator([v for v in nice if lo / 1.25 <= v <= hi * 1.25]))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g} s"))
    ax.set_xlabel(
        "Median end-to-end latency per problem (s, log scale) — lower is better",
        color=TEXT_2,
        fontsize=9,
    )
    ax.set_ylabel("pass@1 across all problems (%) — higher is better", color=TEXT_2, fontsize=9)
    ax.grid(True, which="major", color=GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=8)
    handles = [
        Line2D([], [], color=c, marker="o", linestyle="", markersize=8, label=f)
        for f, c in FAMILY_COLOR.items()
    ]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=8, labelcolor=TEXT_2)
    fig.suptitle(title, x=0.06, y=0.975, ha="left", fontsize=13, color=TEXT, fontweight="bold")
    ax.set_title(textwrap.fill(subtitle, 140), loc="left", fontsize=8.5, color=TEXT_2, pad=10)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    _place_labels(fig, ax, labels)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=SURFACE)
    plt.close(fig)
    return out_png


# Candidate label offsets (points) around a marker, tried in order.
_OFFSETS = [
    (8, 5),
    (8, -12),
    (-8, 5),
    (-8, -12),
    (8, 16),
    (8, -23),
    (-8, 16),
    (-8, -23),
    (8, 27),
    (8, -34),
]


def _place_labels(fig: Any, ax: Any, labels: list[tuple[str, float, float]]) -> None:
    """Greedy collision avoidance: each label takes the first offset whose box overlaps no
    label already placed (falls back to the first offset)."""
    renderer = fig.canvas.get_renderer()
    area = ax.get_window_extent(renderer)
    placed: list[Any] = []
    for text, x, y in sorted(labels, key=lambda t: (-t[2], t[1])):
        box = None
        for dx, dy in _OFFSETS:
            ann = ax.annotate(
                text, (x, y), xytext=(dx, dy), textcoords="offset points",
                ha="left" if dx > 0 else "right", fontsize=7.5, color=TEXT_2, zorder=4,
            )  # fmt: skip
            candidate = ann.get_window_extent(renderer).expanded(1.04, 1.15)
            inside = (
                area.x0 <= candidate.x0
                and candidate.x1 <= area.x1
                and area.y0 <= candidate.y0
                and candidate.y1 <= area.y1
            )
            if inside and not any(candidate.overlaps(b) for b in placed):
                box = candidate
                break
            ann.remove()
        if box is None:  # nothing free: keep the first position
            ann = ax.annotate(
                text, (x, y), xytext=_OFFSETS[0], textcoords="offset points",
                fontsize=7.5, color=TEXT_2, zorder=4,
            )  # fmt: skip
            box = ann.get_window_extent(renderer)
        placed.append(box)


def write_points_csv(points: list[ParetoPoint], out_csv: Path) -> Path:
    pd.DataFrame([p.__dict__ for p in points]).to_csv(out_csv, index=False, float_format="%.4f")
    return out_csv
