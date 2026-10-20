"""Descriptive statistics with bootstrap confidence intervals (F6.2). Deterministic given a seed."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, q in [0, 100] (same as numpy's default)."""
    if not values:
        raise ValueError("percentile of empty sequence")
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float] = statistics.fmean,
    *,
    level: float = 0.95,
    n_boot: int = 2000,
    seed: int = 0,
) -> tuple[float, float]:
    if len(values) < 2:
        v = stat(values)
        return v, v
    rng = random.Random(seed)
    n = len(values)
    boots = sorted(stat([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    alpha = (1 - level) / 2
    return percentile(boots, 100 * alpha), percentile(boots, 100 * (1 - alpha))


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    p50: float
    p95: float
    ci_low: float  # bootstrap CI of the mean
    ci_high: float


def summarize(values: Sequence[float], *, seed: int = 0, n_boot: int = 2000) -> Summary | None:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return None
    lo, hi = bootstrap_ci(vals, seed=seed, n_boot=n_boot)
    return Summary(
        n=len(vals),
        mean=statistics.fmean(vals),
        p50=percentile(vals, 50),
        p95=percentile(vals, 95),
        ci_low=lo,
        ci_high=hi,
    )
