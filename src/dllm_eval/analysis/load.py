"""calls.jsonl -> one flat row per job (F6.1)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from dllm_eval.logging_store import RunStore

COLUMNS = [
    "job_id", "arm", "model", "item_id", "repeat", "warmup", "status", "retries",
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
    "ttft_s", "decode_s", "total_s", "wall_s", "n_chunks", "cost_usd", "cost_usd_regular",
    "server_model", "finish_reason", "n_tool_calls", "error_kind", "error_status",
]  # fmt: skip


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
    }


def load_calls(store: RunStore) -> pd.DataFrame:
    """Latest record per job (resumed jobs supersede earlier attempts)."""
    rows = [flatten(r) for r in store.latest_by_job().values()]
    return pd.DataFrame(rows, columns=COLUMNS)
