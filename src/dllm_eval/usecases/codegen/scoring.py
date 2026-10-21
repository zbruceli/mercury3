"""Offline UC1 scoring: run log -> extracted code -> sandbox -> scores.jsonl -> pass@k.

Never calls a model API. Re-running skips jobs already in scores.jsonl (append-only).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dllm_eval.logging_store import RunStore
from dllm_eval.sandbox.executor import Executor
from dllm_eval.usecases.codegen.datasets import CodeProblem
from dllm_eval.usecases.codegen.extract import build_candidate, extract_code
from dllm_eval.usecases.codegen.passk import pass_at_k

PASS_K = (1, 5)


def grading_job(job_id: str, problem: CodeProblem, content: str) -> tuple[dict[str, Any], str]:
    ex = extract_code(content, problem)
    candidate = build_candidate(problem, ex.code) if ex.code else ""
    job: dict[str, Any] = {
        "job_id": job_id,
        "kind": problem.kind,
        "dataset": problem.dataset,
        "candidate": candidate,
        "entry_point": problem.entry_point,
    }
    if problem.kind == "function":
        job |= {
            "canonical": problem.canonical_solution,
            "inputs": problem.inputs,
            "atol": problem.atol,
        }
        if problem.dataset == "mbpp+":  # EvalPlus converts some MBPP+ inputs per task id
            job["mbpp_task"] = int(problem.id.rsplit("/", 1)[1])
    else:
        job["tests"] = [t.model_dump() for t in problem.tests]
    return job, ex.method


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score_run(
    store: RunStore,
    problems: dict[str, CodeProblem],
    executor: Executor,
    out_path: Path,
    *,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Grade every ok call not yet in out_path. Returns all score rows (old + new)."""
    done = {r["job_id"] for r in _read(out_path)}
    todo: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for rec in store.latest_by_job().values():
        if rec["status"] != "ok" or rec["job_id"] in done:
            continue
        problem = problems[rec["item_id"]]
        job, method = grading_job(rec["job_id"], problem, rec["result"]["content"])
        todo.append((rec, job, method))
    log(f"grading {len(todo)} responses ({len(done)} already scored)")
    if todo:
        results = {r["job_id"]: r for r in executor.run([job for _, job, _ in todo])}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a") as f:
            for rec, _job, method in todo:
                r = results.get(rec["job_id"]) or {
                    "status": "harness_error",
                    "passed": 0,
                    "total": 0,
                    "detail": "missing result",
                }
                row = {
                    "job_id": rec["job_id"],
                    "arm": rec["arm"],
                    "model": rec["model"],
                    "item_id": rec["item_id"],
                    "dataset": problems[rec["item_id"]].dataset,
                    "repeat": rec["repeat"],
                    "extraction": method,
                    "status": r["status"],
                    "passed": r["passed"],
                    "total": r["total"],
                    "detail": r.get("detail"),
                    "grade_s": r.get("grade_s"),
                }
                f.write(json.dumps(row) + "\n")
    return _read(out_path)


def summarize(
    rows: list[dict[str, Any]],
    api_errors: dict[str, int] | None = None,
    finish: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Per (arm, dataset): pass@1 (mean over problems of c/n), pass@5 where n >= 5,
    extraction failures, and harness errors (which are bugs, not model failures).

    `finish` maps job_id -> finish_reason. A response with no code that stopped on
    `length` spent its whole output budget (usually on hidden reasoning): it is counted as
    `budget_out` instead of `no_code`. Both still count as failures in pass@k."""
    finish = finish or {}
    by: dict[tuple[str, str], dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    meta: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        key = (r["arm"], r["dataset"])
        if r["status"] == "harness_error":
            meta[key]["harness_errors"] += 1
            continue
        by[key][r["item_id"]].append(r["status"] == "pass")
        if r["extraction"] in ("none", "truncated_fence"):
            meta[key][f"extraction_{r['extraction']}"] += 1
        status = r["status"]
        if status == "no_code" and finish.get(r["job_id"]) == "length":
            status = "budget_out"
        meta[key][status] += 1
    out = []
    for (arm, dataset), items in sorted(by.items()):
        row: dict[str, Any] = {
            "arm": arm,
            "dataset": dataset,
            "problems": len(items),
            "samples": sum(len(v) for v in items.values()),
        }
        for k in PASS_K:
            eligible = [v for v in items.values() if len(v) >= k]
            row[f"pass@{k}"] = (
                statistics.fmean(pass_at_k(len(v), sum(v), k) for v in eligible)
                if eligible and len(eligible) == len(items)
                else None
            )
        row.update(dict(meta[(arm, dataset)]))
        out.append(row)
    for row in out:
        row["api_errors"] = (api_errors or {}).get(row["arm"], 0)
    return out


# (header, row key) for the count columns of the pass@k table
_COUNT_COLUMNS = [
    ("fail", "fail"),
    ("timeout", "timeout"),
    ("error", "error"),
    ("no code", "no_code"),
    ("budget out", "budget_out"),
    ("truncated", "extraction_truncated_fence"),
    ("harness err", "harness_errors"),
]


def to_markdown(summary: list[dict[str, Any]]) -> str:
    def pct(v: float | None) -> str:
        return "—" if v is None else f"{100 * v:.1f}%"

    heads = ["Arm", "Dataset", "Problems", "Samples", "pass@1", "pass@5"]
    heads += [h for h, _ in _COUNT_COLUMNS]
    lines = ["| " + " | ".join(heads) + " |", "|---|---|" + "---:|" * (len(heads) - 2)]
    for r in summary:
        cells = [f"`{r['arm']}`", r["dataset"], str(r["problems"]), str(r["samples"])]
        cells += [pct(r["pass@1"]), pct(r["pass@5"])]
        cells += [str(r.get(key, 0)) for _, key in _COUNT_COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
