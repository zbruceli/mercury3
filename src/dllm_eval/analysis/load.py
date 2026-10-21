"""calls.jsonl -> one flat row per job (F6.1)."""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Any

import pandas as pd

from dllm_eval.logging_store import RunStore

COLUMNS = [
    "job_id", "arm", "model", "item_id", "repeat", "warmup", "status", "retries",
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
    "ttft_s", "decode_s", "total_s", "wall_s", "n_chunks", "cost_usd", "cost_usd_regular",
    "server_model", "finish_reason", "n_tool_calls", "error_kind", "error_status",
    "started_at", "spans_pause",
]  # fmt: skip

# A gap this long between consecutive call starts means the run was paused (machine
# sleep, or a stop and resume). Calls in flight across a pause keep their correctness
# result but are excluded from latency stats: on macOS the timer stops during sleep, so
# their durations look normal, but stream timing across a freeze is not trustworthy.
PAUSE_GAP_S = 60.0


def flatten(rec: dict[str, Any]) -> dict[str, Any]:
    res = rec.get("result") or {}
    usage = res.get("usage") or {}
    timing = res.get("timing") or {}
    first, last = timing.get("t_first_content"), timing.get("t_last_content")
    err = rec.get("error") or {}
    return {
        "job_id": rec["job_id"],
        "arm": rec["arm"],
        "model": rec["model"],
        "item_id": rec["item_id"],
        "repeat": rec["repeat"],
        "warmup": rec.get("warmup", False),
        "status": rec["status"],
        "retries": res.get("retries", err.get("attempts", 1) - 1 if err else 0),
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "ttft_s": first,
        "decode_s": (last - first) if first is not None and last is not None else None,
        "total_s": timing.get("t_done"),
        "wall_s": rec.get("wall_s"),
        "n_chunks": res.get("n_chunks"),
        "cost_usd": rec.get("cost_usd"),
        "cost_usd_regular": rec.get("cost_usd_regular"),
        "server_model": res.get("server_model"),
        "finish_reason": res.get("finish_reason"),
        "n_tool_calls": len(res.get("tool_calls") or []),
        "error_kind": err.get("kind"),
        "error_status": err.get("status"),
        "started_at": rec.get("started_at"),
        "spans_pause": False,
    }


def pauses(
    starts: list[dt.datetime], gap_s: float = PAUSE_GAP_S
) -> list[tuple[dt.datetime, dt.datetime]]:
    """(start, end) of every gap longer than gap_s between consecutive call starts."""
    s = sorted(starts)
    return [(a, b) for a, b in itertools.pairwise(s) if (b - a).total_seconds() > gap_s]


def mark_pause_spanning(
    rows: list[dict[str, Any]], gap_s: float = PAUSE_GAP_S
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Set spans_pause on rows whose [started_at, started_at + wall_s] overlaps a pause."""
    starts = [dt.datetime.fromisoformat(r["started_at"]) for r in rows if r.get("started_at")]
    gaps = pauses(starts, gap_s)
    for r in rows:
        if not r.get("started_at") or not gaps:
            continue
        t0 = dt.datetime.fromisoformat(r["started_at"])
        t1 = t0 + dt.timedelta(seconds=r.get("wall_s") or 0)
        r["spans_pause"] = any(t0 < b and t1 > a for a, b in gaps)
    return gaps


def load_calls(store: RunStore) -> pd.DataFrame:
    """Latest record per job (resumed jobs supersede earlier attempts)."""
    rows = [flatten(r) for r in store.latest_by_job().values()]
    mark_pause_spanning(rows)
    return pd.DataFrame(rows, columns=COLUMNS)
