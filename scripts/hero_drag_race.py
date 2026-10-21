"""Drag-race hero image for the blog and README.

Numbers come only from reports/assets/fig1_pareto_uc1.csv (run R2). Usage:
    uv run python scripts/hero_drag_race.py reports/assets/fig1_pareto_uc1.csv \
        docs/images/hero_drag_race.png
"""

import csv
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

CSV, OUT = sys.argv[1], sys.argv[2]
with open(CSV, newline="") as f:
    rows = {r["arm"]: r for r in csv.DictReader(f)}

LANES = [  # arm, display name, kind
    ("gpt-oss-120b-cerebras:medium", "gpt-oss-120b on Cerebras", "fast-hardware AR"),
    ("gemini-3.5-flash-lite:minimal", "Gemini 3.5 Flash-Lite", "frontier-lab AR"),
    ("mercury-3:low", "Mercury 3", "diffusion"),
    ("claude-haiku-5-5:thinking-off", "Claude Haiku 5.5", "frontier-lab AR"),
    ("gpt-6-luna:none", "GPT-6 Luna", "frontier-lab AR"),
]
COLOR = {"diffusion": "#3f8be6", "fast-hardware AR": "#e06530", "frontier-lab AR": "#1fa674"}
BG, LANE, INK, INK2, INK3 = "#141517", "#1d1f23", "#f2f2f0", "#b9bab6", "#7d7e7a"

for fam in ("Avenir Next", "Helvetica Neue", "DejaVu Sans"):
    if any(fam == f.name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = fam
        break

data = []
for arm, name, kind in LANES:
    r = rows[arm]
    data.append((name, kind, float(r["latency_p50_s"]), float(r["pass1"]) * 100))
data.sort(key=lambda d: d[2])
t_first = data[0][2]

W, H = 1600, 900
fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=BG)
ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")
ax.add_patch(Rectangle((0, 0), W, H, color=BG, zorder=0))

X0, X1 = 110, 1280  # start line, finish line
TOP, LANE_H = 730, 140  # five lanes centred vertically; only per-car labels, no title
SC = 1.35  # car scale
rng = np.random.default_rng(7)


def car(x, y, color, noisy=False):
    """A side-view dragster whose nose sits at x."""
    L, h = 118 * SC, 30 * SC
    body = FancyBboxPatch(
        (x - L, y - 6 * SC),
        L - 14 * SC,
        h - 8 * SC,
        boxstyle=f"round,pad=0,rounding_size={10 * SC}",
        fc=color,
        ec="none",
        zorder=5,
    )
    nose = FancyBboxPatch(
        (x - 30 * SC, y - 6 * SC),
        30 * SC,
        12 * SC,
        boxstyle=f"round,pad=0,rounding_size={6 * SC}",
        fc=color,
        ec="none",
        zorder=5,
    )
    cabin = FancyBboxPatch(
        (x - L + 30 * SC, y + 12 * SC),
        34 * SC,
        16 * SC,
        boxstyle=f"round,pad=0,rounding_size={7 * SC}",
        fc="#e9eef5",
        ec="none",
        alpha=0.85,
        zorder=6,
    )
    wing = Rectangle((x - L - 4 * SC, y + 16 * SC), 16 * SC, 14 * SC, fc=color, ec="none", zorder=5)
    for p in (body, nose, cabin, wing):
        ax.add_patch(p)
    for wx, r in ((x - L + 18 * SC, 15 * SC), (x - 26 * SC, 11 * SC)):
        ax.add_patch(Circle((wx, y - 8 * SC), r, fc="#0b0b0c", ec="#3a3c40", lw=2, zorder=7))
    if noisy:  # diffusion: the car is still "coming into focus"
        n, spread = 320, L + 200
        px = x - rng.uniform(0, spread, n)
        py = y + 4 * SC + rng.normal(0, 20, n)
        for xi, yi, d in zip(px, py, (x - px) / spread, strict=True):
            s = rng.uniform(4, 9)
            ax.add_patch(
                Rectangle(
                    (xi, yi),
                    s,
                    s,
                    fc=color,
                    ec="none",
                    alpha=float(np.clip(0.9 - d * 0.8, 0.08, 0.9)),
                    zorder=4,
                )
            )


# finish line (chequered)
sq = 16
for i, yy in enumerate(
    np.arange(TOP - LANE_H * len(data) + LANE_H / 2 + 8, TOP + LANE_H / 2 - 8 - sq + 1, sq)
):
    for j in range(2):
        if (i + j) % 2 == 0:
            ax.add_patch(Rectangle((X1 + j * sq, yy), sq, sq, fc=INK, ec="none", zorder=2))

for k, (name, kind, t, _pass1) in enumerate(data):
    yc = TOP - k * LANE_H
    c = COLOR[kind]
    ax.add_patch(
        Rectangle((X0, yc - LANE_H / 2 + 8), W - 2 * X0, LANE_H - 16, fc=LANE, ec="none", zorder=1)
    )
    ax.plot([X0, X0], [yc - LANE_H / 2 + 8, yc + LANE_H / 2 - 8], color=INK3, lw=2, zorder=2)
    for xd in np.arange(X0 + 20, W - X0 - 30, 60):  # lane dashes
        ax.plot([xd, xd + 26], [yc - LANE_H / 2 + 8] * 2, color="#2c2f34", lw=2, zorder=2)
    progress = t_first / t
    xn = X0 + (X1 - X0) * progress  # distance covered at constant speed
    if progress >= 0.999:
        xn = X1 + 36 + 118 * SC  # crossed the line
    car(xn, yc, c, noisy=(kind == "diffusion"))
    # annotate each car with its name and race time
    if progress >= 0.999:  # the winner sits past the line: label it on the track side
        lx, ha = X1 - 28, "right"
    else:
        lx, ha = xn + 26, "left"
    label = ax.text(
        lx, yc + 14, name, color=INK, fontsize=24, fontweight="bold", ha=ha, va="center", zorder=8
    )
    ax.text(lx, yc - 20, f"{t:.2f} s", color=INK2, fontsize=21, ha=ha, va="center", zorder=8)
    trail_end = xn - 118 * SC - 8
    if ha == "right":  # keep the motion trail clear of the label
        fig.canvas.draw()
        bb = label.get_window_extent().transformed(ax.transData.inverted())
        trail_end = min(trail_end, bb.x0 - 24)
    ax.plot(
        [X0 + 8, trail_end],
        [yc + 3, yc + 3],
        color=c,
        lw=4,
        alpha=0.5,
        zorder=3,
        solid_capstyle="round",
    )  # motion trail

fig.savefig(OUT, dpi=100, facecolor=BG)
print("ok", [(d[0], d[2], round(d[3], 1)) for d in data])
