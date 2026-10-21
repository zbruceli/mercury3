"""Failure-mode tagging (F6.6): rule-based tags for every failure, a manual tag file that
overrides or annotates them, per-arm aggregation, and the failure-gallery export.

Every failed job gets exactly one tag. The rules below assign it from the raw log and the
scores; `reports/failure_tags.csv` (manual) can replace a rule tag and carries the notes
and gallery picks. Offline only: reads run directories and score files.

UC1 (code generation), from scores.jsonl status + the call's finish reason and text:
  budget_out_hidden   stopped at the output cap with no visible text: hidden reasoning ran out
  think_aloud_runaway stopped at the output cap with visible text (prose or code that never ended)
  no_code             stopped normally but no code could be extracted
  wrong_answer        code ran and failed tests
  runtime_error       code raised
  timeout             code exceeded the sandbox time limit
  api_error           the call itself failed

UC2 (SQL agent), from the task outcome, the trace and uc2_scores.jsonl:
  budget_out          a turn stopped at the output cap
  empty_response      a turn with no text and no tool call
  schema_violation    response_format mode: the final reply didn't match the schema
  turn_cap_repeat     turn cap, and the same SQL ran more than once (circling)
  turn_cap_prose      turn cap, and at least two replies were prose with no tool call
  turn_cap_exploring  turn cap otherwise (still exploring when the budget ran out)
  answer_sql_error    the submitted query errors or times out
  blind_wrong         a wrong answer submitted without a single exploring tool call
  wrong_result        a wrong answer after exploring
  api_error           a provider error ended the task
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UC1_TAGS = [
    "budget_out_hidden", "think_aloud_runaway", "no_code", "wrong_answer", "runtime_error",
    "timeout", "api_error",
]  # fmt: skip
UC2_TAGS = [
    "budget_out", "empty_response", "schema_violation", "turn_cap_repeat", "turn_cap_prose",
    "turn_cap_exploring", "answer_sql_error", "blind_wrong", "wrong_result", "api_error",
]  # fmt: skip
TAG_HELP = {
    "budget_out_hidden": "hit the output cap on hidden reasoning; no visible answer",
    "think_aloud_runaway": "hit the output cap while writing visible text",
    "no_code": "finished but no code to extract",
    "wrong_answer": "code ran and failed tests",
    "runtime_error": "code raised an exception",
    "timeout": "code exceeded the time limit",
    "api_error": "the API call failed",
    "budget_out": "an agent turn hit the output cap",
    "empty_response": "an agent turn with no text and no tool call",
    "schema_violation": "final reply broke the strict JSON schema",
    "turn_cap_repeat": "8 turns without an answer, re-running the same SQL",
    "turn_cap_prose": "8 turns without an answer, replying in prose instead of tools",
    "turn_cap_exploring": "8 turns without an answer, still exploring",
    "answer_sql_error": "submitted SQL errors or times out",
    "blind_wrong": "wrong answer submitted without exploring the database",
    "wrong_result": "wrong answer after exploring",
}
LENGTH_STOPS = frozenset({"length", "max_tokens"})
MANUAL_FIELDS = ["run_id", "job_id", "tag", "gallery", "title", "note"]


@dataclass(frozen=True)
class Failure:
    run_id: str
    job_id: str
    arm: str
    item_id: str
    tag: str
    source: str  # "rule" or "manual"


def uc1_tag(rec: dict[str, Any], score: dict[str, Any] | None) -> str | None:
    """Tag for one UC1 job, or None when it passed (or was a harness error)."""
    if rec["status"] != "ok":
        return "api_error"
    if score is None or score["status"] in ("pass", "harness_error"):
        return None
    res = rec.get("result") or {}
    if res.get("finish_reason") in LENGTH_STOPS:
        return "think_aloud_runaway" if (res.get("content") or "").strip() else "budget_out_hidden"
    return {
        "no_code": "no_code",
        "fail": "wrong_answer",
        "error": "runtime_error",
        "timeout": "timeout",
    }.get(score["status"], "wrong_answer")


def _run_sql_texts(rec: dict[str, Any]) -> list[str]:
    """The SQL of every run_sql call, from the assistant messages (tool records don't keep
    arguments)."""
    out = []
    for m in (rec.get("task") or {}).get("messages") or []:
        for c in m.get("tool_calls") or []:
            if c.get("name") == "run_sql":
                args = c.get("arguments")
                try:
                    parsed = json.loads(args) if isinstance(args, str) else args
                except json.JSONDecodeError:
                    parsed = None
                sql = parsed.get("query") if isinstance(parsed, dict) else args
                out.append(" ".join(str(sql).split()).lower())
    return out


def uc2_tag(rec: dict[str, Any], score: dict[str, Any] | None) -> str | None:
    """Tag for one UC2 task, or None when it was correct (or a harness error)."""
    if rec["status"] != "ok":
        return "api_error"
    task = rec.get("task") or {}
    outcome = task.get("outcome")
    if outcome == "error":
        return "api_error"
    if outcome == "budget_out":
        return "budget_out"
    if outcome == "empty_response":
        return "empty_response"
    if outcome == "invalid_answer":
        return "schema_violation"
    if outcome == "turn_cap":
        sqls = _run_sql_texts(rec)
        if len(sqls) != len(set(sqls)):
            return "turn_cap_repeat"
        if sum(bool(x.get("nudged")) for x in task.get("turns") or []) >= 2:
            return "turn_cap_prose"
        return "turn_cap_exploring"
    if score is None or score["status"] == "harness_error" or score["ex"]:
        return None
    if score.get("pred_status") in ("sql_error", "timeout", "too_many_rows"):
        return "answer_sql_error"
    explored = any(
        c["name"] != "submit_answer" for x in task.get("turns") or [] for c in x.get("tools") or []
    )
    return "wrong_result" if explored else "blind_wrong"


def read_manual(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if not (r.get("run_id") or "").startswith("#")]
    for r in rows:
        missing = [k for k in ("run_id", "job_id") if not r.get(k)]
        if missing:
            raise ValueError(f"{path}: row without {missing}: {r}")
    return rows


def tag_run(
    run_id: str,
    records: Iterable[dict[str, Any]],
    scores: Iterable[dict[str, Any]],
    *,
    kind: str,
    manual: Iterable[dict[str, str]] = (),
) -> list[Failure]:
    """One Failure per failed job. `kind` is "uc1" or "uc2". A manual row with a tag
    replaces the rule tag for that job; a manual tag on a passing job is an error."""
    by_job = {s["job_id"]: s for s in scores}
    rule = uc1_tag if kind == "uc1" else uc2_tag
    overrides = {m["job_id"]: m["tag"] for m in manual if m["run_id"] == run_id and m.get("tag")}
    allowed = set(UC1_TAGS if kind == "uc1" else UC2_TAGS)
    out = []
    seen = set()
    for rec in records:
        jid = rec["job_id"]
        tag = rule(rec, by_job.get(jid))
        seen.add(jid)
        if jid in overrides:
            if tag is None:
                raise ValueError(f"manual tag on a job that didn't fail: {run_id} {jid}")
            if overrides[jid] not in allowed:
                raise ValueError(f"unknown {kind} tag {overrides[jid]!r} for {jid}")
            out.append(Failure(run_id, jid, rec["arm"], rec["item_id"], overrides[jid], "manual"))
        elif tag is not None:
            out.append(Failure(run_id, jid, rec["arm"], rec["item_id"], tag, "rule"))
    unknown = set(overrides) - seen
    if unknown:
        raise ValueError(f"manual tags for jobs not in {run_id}: {sorted(unknown)[:3]}")
    return out


def aggregate(
    failures: Iterable[Failure], jobs_per_arm: dict[str, int], tags: list[str]
) -> list[dict[str, Any]]:
    """Per arm: jobs, failures, and the count of each tag (share of all jobs in the arm)."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for f in failures:
        counts[f.arm][f.tag] += 1
    rows = []
    for arm in sorted(jobs_per_arm):
        c = counts[arm]
        rows.append(
            {"arm": arm, "jobs": jobs_per_arm[arm], "failures": sum(c.values())}
            | {t: c.get(t, 0) for t in tags}
        )
    return rows


def aggregate_markdown(rows: list[dict[str, Any]], tags: list[str], label: Any = str) -> str:
    used = [t for t in tags if any(r[t] for r in rows)]
    lines = [
        "| Arm | Jobs | Failed | " + " | ".join(f"`{t}`" for t in used) + " |",
        "|---|---:|---:|" + "---:|" * len(used),
    ]
    for r in sorted(rows, key=lambda r: r["failures"]):
        cells = ["·" if not r[t] else f"{r[t]} ({100 * r[t] / r['jobs']:.1f}%)" for t in used]
        lines.append(
            f"| {label(r['arm'])} | {r['jobs']:,} | {100 * r['failures'] / r['jobs']:.1f}% | "
            + " | ".join(cells)
            + " |"
        )
    return "\n".join(lines)


def write_failures_csv(failures: Iterable[Failure], out_csv: Path) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "job_id", "arm", "item_id", "tag", "source"])
        for x in failures:
            w.writerow([x.run_id, x.job_id, x.arm, x.item_id, x.tag, x.source])
    return out_csv


# --- gallery --------------------------------------------------------------------------


def excerpt(text: str, head: int = 600, tail: int = 300) -> str:
    """The start and end of a long text with the middle elided (character counts)."""
    text = text.replace("\r\n", "\n")
    if len(text) <= head + tail + 120:  # not worth cutting
        return text
    cut = len(text) - head - tail
    end = f"\n\n{text[-tail:]}" if tail else ""
    return f"{text[:head]}\n\n[… {cut:,} characters omitted …]{end}"


def uc1_evidence(rec: dict[str, Any], score: dict[str, Any] | None) -> dict[str, Any]:
    res = rec.get("result") or {}
    usage = res.get("usage") or {}
    timing = res.get("timing") or {}
    content = res.get("content") or ""
    tests = f"{score['passed']}/{score['total']}" if score and score.get("total") else None
    return {
        "facts": [
            ("finish", res.get("finish_reason")),
            ("output tokens", usage.get("output_tokens")),
            ("of which reasoning", usage.get("reasoning_tokens")),
            ("visible characters", len(content)),
            ("time", f"{timing.get('t_done', 0):.1f} s" if timing.get("t_done") else None),
            ("grade", score["status"] if score else None),
            ("tests passed", tests),
        ],
        "text": excerpt(content) if content.strip() else "(no visible text)",
    }


def uc2_evidence(rec: dict[str, Any], score: dict[str, Any] | None) -> dict[str, Any]:
    task = rec.get("task") or {}
    turns = task.get("turns") or []
    steps = []
    for x in turns:
        res = x["result"]
        calls = ", ".join(f"{c['name']}→{c['status'] or c['validity']}" for c in x["tools"])
        said = " ".join((res.get("content") or "").split())
        bits = [f"turn {x['index'] + 1}: {calls or 'no tool call'}"]
        if said:
            bits.append(f'says "{said[:160]}{"…" if len(said) > 160 else ""}"')
        if x.get("nudged"):
            bits.append("(nudged to use a tool)")
        steps.append(" · ".join(bits))
    last = turns[-1]["result"].get("content") if turns else ""
    return {
        "facts": [
            ("outcome", task.get("outcome")),
            ("turns", len(turns)),
            ("task time", f"{task.get('wall_s', 0):.1f} s"),
            ("submitted SQL", task.get("answer_sql")),
            ("EX", None if score is None else score.get("ex")),
        ],
        "steps": steps,
        "text": excerpt(last or "", 500, 200) if (last or "").strip() else None,
    }
