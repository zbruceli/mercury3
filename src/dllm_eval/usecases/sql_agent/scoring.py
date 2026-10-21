"""UC2 scoring (F5.4): run each submitted query and its gold query on the question's
read-only database and compare result sets. Offline: reads the run log, never calls a
model API. Gold queries run once per question; the predicted ones in a thread pool.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dllm_eval.logging_store import RunStore
from dllm_eval.usecases.sql_agent.compare import ex_bird, ex_match, has_top_level_order_by
from dllm_eval.usecases.sql_agent.datasets import BirdQuestion
from dllm_eval.usecases.sql_agent.tools import ReadOnlyDB, SqlTimeout

# Scoring limits. BIRD's official evaluator times predicted queries out at 30 s; gold
# queries get 60 s (all sampled ones finish in under 3 s, see datasets.GOLD_EXCLUDED).
PRED_TIMEOUT_S = 30.0
GOLD_TIMEOUT_S = 60.0
MAX_ROWS = 200_000


@dataclass(frozen=True)
class Execution:
    status: str  # ok | sql_error | timeout | too_many_rows
    rows: list[tuple[Any, ...]]
    error: str = ""


def execute(db: ReadOnlyDB, sql: str, timeout_s: float) -> Execution:
    try:
        _, rows, truncated = db.query(sql, timeout_s=timeout_s, max_rows=MAX_ROWS)
    except SqlTimeout as e:
        return Execution("timeout", [], str(e))
    except (sqlite3.Error, ValueError, OverflowError) as e:
        return Execution("sql_error", [], f"{type(e).__name__}: {e}"[:300])
    if truncated:
        return Execution("too_many_rows", [], f"more than {MAX_ROWS} rows")
    return Execution("ok", rows)


def score_record(
    rec: dict[str, Any], q: BirdQuestion, gold: Execution, db: ReadOnlyDB
) -> dict[str, Any]:
    task = rec.get("task") or {}
    row: dict[str, Any] = {
        "job_id": rec["job_id"],
        "arm": rec["arm"],
        "item_id": rec["item_id"],
        "repeat": rec["repeat"],
        "difficulty": q.difficulty,
        "db_id": q.db_id,
        "status": rec["status"],  # run status: ok | error | ...
        "outcome": task.get("outcome"),
        "pred_status": None,
        "pred_error": "",
        "ex": False,
        "ex_bird": False,
    }
    if gold.status != "ok":
        row["status"] = "harness_error"  # never counted as a pass or a failure
        row["pred_error"] = f"gold query failed: {gold.status} {gold.error}"
        return row
    sql = task.get("answer_sql")
    if rec["status"] != "ok" or task.get("outcome") != "submitted" or not sql:
        return row
    pred = execute(db, sql, PRED_TIMEOUT_S)
    row["pred_status"], row["pred_error"] = pred.status, pred.error
    if pred.status == "ok":
        row["ex"] = ex_match(pred.rows, gold.rows, ordered=has_top_level_order_by(q.gold_sql))
        row["ex_bird"] = ex_bird(pred.rows, gold.rows)
    return row


def score_run(
    store: RunStore,
    questions: dict[str, BirdQuestion],
    db_paths: dict[str, Path],
    out_path: Path,
    *,
    workers: int = 6,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Score the latest record of every job and (re)write out_path. Derived data only."""
    records = [r for r in store.latest_by_job().values() if r["item_id"] in questions]
    dbs = {i: ReadOnlyDB(p) for i, p in db_paths.items()}
    needed = sorted({r["item_id"] for r in records})
    with ThreadPoolExecutor(workers) as pool:
        golds = dict(
            zip(
                needed,
                pool.map(lambda i: execute(dbs[i], questions[i].gold_sql, GOLD_TIMEOUT_S), needed),
                strict=True,
            )
        )
        log(f"gold queries: {sum(g.status == 'ok' for g in golds.values())}/{len(golds)} ok")
        rows = list(
            pool.map(
                lambda r: score_record(
                    r, questions[r["item_id"]], golds[r["item_id"]], dbs[r["item_id"]]
                ),
                records,
            )
        )
    rows.sort(key=lambda r: r["job_id"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    log(f"scored {len(rows)} tasks -> {out_path}")
    return rows
