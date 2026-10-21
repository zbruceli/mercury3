"""Unbiased pass@k estimator (F4.4; Chen et al. 2021, "Evaluating LLMs trained on code")."""

from __future__ import annotations

from math import comb


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that at least one of k samples (drawn from n, c correct) passes."""
    if not 0 <= c <= n or k < 1:
        raise ValueError(f"invalid n={n}, c={c}, k={k}")
    if k > n:
        raise ValueError(f"k={k} > n={n} samples")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)
