"""Execution-accuracy comparison (F5.4): does the predicted query return the gold result?

Two verdicts per answer:
- `ex` (ours, primary): rows compared as a multiset, or as an ordered list when the gold
  query has a top-level ORDER BY. Floats match after rounding to FLOAT_DIGITS decimals.
- `ex_bird` (the official BIRD evaluator's rule, for comparability): `set(pred) == set(gold)`
  on raw values, which ignores order and duplicates.
Both compare rows as tuples, so column order matters and column names don't (as in BIRD).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

FLOAT_DIGITS = 6

Row = tuple[Any, ...]


def _cell(v: Any) -> Any:
    """Canonical cell: ints and integral floats compare equal (as in SQL and Python);
    other floats are rounded; NULL stays None (NULL == NULL here, unlike in SQL)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return v
        r = round(v, FLOAT_DIGITS)
        return int(r) if r.is_integer() else r + 0.0  # + 0.0 turns -0.0 into 0.0
    return v


def canonical(rows: list[Row]) -> list[Row]:
    return [tuple(_cell(v) for v in r) for r in rows]


_STRING = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]")
_PARENS = re.compile(r"\([^()]*\)")


def has_top_level_order_by(sql: str) -> bool:
    """True if ORDER BY applies to the outermost result (not just inside a subquery)."""
    s = _STRING.sub("''", sql)
    s = re.sub(r"--[^\n]*|/\*.*?\*/", " ", s, flags=re.S)
    while True:
        t = _PARENS.sub(" ", s)
        if t == s:
            break
        s = t
    return re.search(r"\border\s+by\b", s, flags=re.I) is not None


def ex_match(pred: list[Row], gold: list[Row], *, ordered: bool) -> bool:
    p, g = canonical(pred), canonical(gold)
    if ordered:
        return p == g
    return Counter(p) == Counter(g)


def ex_bird(pred: list[Row], gold: list[Row]) -> bool:
    return set(pred) == set(gold)
