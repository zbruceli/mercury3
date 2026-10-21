"""Trace viewer and replay check (F5.7). Offline: reads one task record from calls.jsonl.

`replay` proves a trace is complete. It rebuilds every turn's request from the stored
history and re-runs every recorded tool call against the read-only database, checking
that the model would have seen exactly the recorded results.
"""

from __future__ import annotations

import textwrap
from typing import Any

from dllm_eval.experiment import SqlAgentWorkload
from dllm_eval.schemas import ToolCall
from dllm_eval.usecases.sql_agent import tools
from dllm_eval.usecases.sql_agent.agent import offered_tools


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + f" ... [{len(s) - n} more chars]"


def format_trace(rec: dict[str, Any], *, width: int = 2000) -> str:
    task = rec.get("task") or {}
    lines = [
        f"job {rec['job_id']}  status={rec['status']}  outcome={task.get('outcome')}  "
        f"turns={len(task.get('turns', []))}  wall={task.get('wall_s', 0):.2f}s  "
        f"model={task.get('model_s', 0):.2f}s  tools={task.get('tool_s', 0):.2f}s  "
        f"cost=${rec.get('cost_usd') or 0:.6f}",
    ]
    msgs = task.get("messages", [])
    for m in msgs[:2]:
        lines.append(f"\n[{m['role']}]\n{textwrap.indent(_clip(m['content'] or '', width), '  ')}")
    for t in task.get("turns", []):
        r = t["result"]
        u, tm = r.get("usage") or {}, r.get("timing") or {}
        lines.append(
            f"\n--- turn {t['index'] + 1}  t+{t['t_start']:.2f}s  call {tm.get('t_done', 0):.2f}s "
            f"(ttft {tm.get('t_first_content') or 0:.2f}s)  out {u.get('output_tokens')} tok "
            f"(reasoning {u.get('reasoning_tokens')})  finish={r.get('finish_reason')}"
            f"  retries={r.get('retries', 0)}"
        )
        if r.get("content"):
            lines.append(textwrap.indent(_clip(r["content"], width), "  text: "))
        for call, rec_t in zip(r.get("tool_calls", []), t["tools"], strict=False):
            lines.append(f"  -> {call['name']}({_clip(call['arguments'], width)})")
            tag = rec_t["validity"] if rec_t["validity"] != "valid" else rec_t["status"]
            lines.append(textwrap.indent(_clip(rec_t["text"], width), f"     [{tag}] "))
        if t.get("nudged"):
            lines.append("  (no tool call: nudged)")
    if task.get("answer_sql"):
        lines.append(f"\nanswer: {task['answer_sql']}")
    if rec.get("error"):
        lines.append(f"\nerror: {rec['error']}")
    return "\n".join(lines)


def replay(rec: dict[str, Any], wl: SqlAgentWorkload, db: tools.ReadOnlyDB) -> list[str]:
    """Mismatches between the stored trace and a re-execution; [] means it replays."""
    task = rec.get("task") or {}
    msgs = task.get("messages", [])
    problems = []
    offered = offered_tools(wl.answer_mode)
    for t in task.get("turns", []):
        n = t["n_messages"]
        if n > len(msgs) or (n < len(msgs) and msgs[n]["role"] != "assistant"):
            problems.append(f"turn {t['index']}: history does not line up at message {n}")
            continue
        calls = t["result"].get("tool_calls", [])
        if n < len(msgs) and [c["id"] for c in msgs[n].get("tool_calls", [])] != [
            c["id"] for c in calls
        ]:
            problems.append(f"turn {t['index']}: assistant message differs from the result")
        for call, tr in zip(calls, t["tools"], strict=True):
            checked = tools.check_call(ToolCall.model_validate(call), offered)
            if checked.validity != tr["validity"]:
                problems.append(f"turn {t['index']} {call['id']}: validity differs")
                continue
            if not checked.ok or tr["status"] in (None, "answer", "timeout"):
                continue  # nothing executed, or timing-dependent
            assert checked.args is not None
            out = tools.execute(
                call["name"], checked.args, db, timeout_s=wl.sql_timeout_s, row_cap=wl.row_cap
            )
            if out.text != tr["text"]:
                problems.append(f"turn {t['index']} {call['id']}: tool result differs")
    return problems
