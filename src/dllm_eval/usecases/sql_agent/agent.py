"""The UC2 agent loop (F5.3): one fixed loop for every model, on BaseProvider.chat() only.

Each turn is one model call. The assistant turn goes back into the history through
`ChatResult.as_message(provider_key)`, which carries the provider-native blocks that must
be echoed (Anthropic thinking, Gemini thought signatures). Tool calls are checked against
their schemas (F5.5) and executed in a worker thread, so a slow query never blocks the
event loop that times other tasks' streams. A reply may carry text next to tool calls
(Mercury 3 often writes a short preamble); the text is kept and the calls still run.

Outcomes:
  submitted       - a valid submit_answer call (or, in response_format mode, a valid
                    JSON answer). Correctness is decided offline by scoring.py
  invalid_answer  - response_format mode: the final reply didn't match the answer schema
  turn_cap        - max_turns model calls without an answer
  budget_out      - a turn stopped at max_completion_tokens (PRD R1: a failure)
  empty_response  - a turn with no text and no tool calls
  error           - a provider error ended the task (the partial trace is kept)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from dllm_eval.experiment import Arm, PromptItem, SqlAgentWorkload
from dllm_eval.providers import BaseProvider, ProviderError
from dllm_eval.schemas import ChatRequest, ChatResult, JsonSchemaFormat, Message, ToolSpec
from dllm_eval.timing import Clock
from dllm_eval.usecases.sql_agent import prompts, tools

Outcome = Literal[
    "submitted", "invalid_answer", "turn_cap", "budget_out", "empty_response", "error"
]
LENGTH_STOPS = frozenset({"length", "max_tokens"})  # OpenAI-style / Anthropic


@dataclass
class ToolRecord:
    tool_call_id: str
    name: str
    validity: tools.Validity
    detail: str = ""
    status: tools.ToolStatus | Literal["answer"] | None = None  # None = not executed
    text: str = ""
    exec_s: float = 0.0


@dataclass
class Turn:
    index: int
    n_messages: int  # history length sent with this turn's request
    t_start: float  # seconds since the task started
    result: ChatResult
    tools: list[ToolRecord] = field(default_factory=list)
    nudged: bool = False


@dataclass
class TaskResult:
    outcome: Outcome | None = None
    answer_sql: str | None = None
    turns: list[Turn] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    error: ProviderError | None = None
    wall_s: float = 0.0

    def to_record(self, *, record_raw: bool = False) -> dict[str, Any]:
        exclude = set() if record_raw else {"raw_response"}
        return {
            "outcome": self.outcome,
            "answer_sql": self.answer_sql,
            "wall_s": self.wall_s,
            "model_s": sum(t.result.timing.t_done for t in self.turns),
            "tool_s": sum(r.exec_s for t in self.turns for r in t.tools),
            "turns": [
                {
                    "index": t.index,
                    "n_messages": t.n_messages,
                    "t_start": t.t_start,
                    "nudged": t.nudged,
                    # raw_assistant is already in `messages`; don't store it twice
                    "result": t.result.model_dump(mode="json", exclude=exclude | {"raw_assistant"}),
                    "tools": [r.__dict__ for r in t.tools],
                }
                for t in self.turns
            ],
            "messages": [m.model_dump(mode="json") for m in self.messages],
        }


def offered_tools(mode: prompts.AnswerMode) -> tuple[ToolSpec, ...]:
    return tools.ALL_TOOLS if mode == "tool" else tools.EXPLORE_TOOLS


def build_request(
    arm: Arm, wl: SqlAgentWorkload, history: list[Message], *, stream: bool
) -> ChatRequest:
    return ChatRequest(
        model_key=arm.model,
        messages=list(history),
        max_completion_tokens=arm.max_completion_tokens,
        temperature=arm.temperature,
        reasoning_effort=arm.reasoning_effort,
        tools=list(offered_tools(wl.answer_mode)),
        tool_choice="auto",
        response_format=(
            JsonSchemaFormat(name="answer", schema_=tools.ANSWER_SCHEMA, strict=True)
            if wl.answer_mode == "response_format"
            else None
        ),
        stream=stream,
        provider_extras=dict(arm.provider_extras),
    )


def parse_answer(content: str) -> str | None:
    """response_format mode: the SQL from a schema-valid reply, else None."""
    try:
        value = json.loads(content)
    except ValueError:
        return None
    if tools.schema_errors(value, tools.ANSWER_SCHEMA):
        return None
    sql = value["sql"]
    return sql if isinstance(sql, str) and sql.strip() else None


async def run_task(
    provider: BaseProvider,
    arm: Arm,
    item: PromptItem,
    db: tools.ReadOnlyDB,
    wl: SqlAgentWorkload,
    *,
    stream: bool = True,
    clock: Clock = time.perf_counter,
) -> TaskResult:
    """Run one question to an outcome. Provider errors end the task with a partial trace
    (outcome "error"); anything else that raises is a harness bug and propagates."""
    offered = offered_tools(wl.answer_mode)
    history = list(item.messages)
    task = TaskResult(messages=history)
    t0 = clock()
    try:
        for index in range(wl.max_turns):
            req = build_request(arm, wl, history, stream=stream)
            t_start = clock() - t0
            try:
                result = await provider.chat(req)
            except ProviderError as e:
                task.error = e
                task.outcome = "error"
                return task
            turn = Turn(index, len(history), t_start, result)
            task.turns.append(turn)
            history.append(result.as_message(provider.provider_key))

            if result.finish_reason in LENGTH_STOPS:
                task.outcome = "budget_out"
                return task
            if not result.tool_calls:
                if wl.answer_mode == "response_format":
                    task.answer_sql = parse_answer(result.content)
                    task.outcome = "submitted" if task.answer_sql else "invalid_answer"
                    return task
                if not result.content.strip():
                    task.outcome = "empty_response"
                    return task
                history.append(Message(role="user", content=prompts.NUDGE_TOOL))
                turn.nudged = True
                continue

            for call in result.tool_calls:
                checked = tools.check_call(call, offered)
                rec = ToolRecord(call.id, call.name, checked.validity, checked.detail)
                turn.tools.append(rec)
                if not checked.ok:
                    rec.text = f"Error: invalid tool call: {checked.detail}"
                elif call.name == "submit_answer":
                    assert checked.args is not None
                    rec.status = "answer"
                    if task.answer_sql is None:
                        task.answer_sql = checked.args["sql"]
                    continue  # the task ends after this turn; no result is sent back
                else:
                    assert checked.args is not None
                    t = clock()
                    out = await asyncio.to_thread(
                        tools.execute,
                        call.name,
                        checked.args,
                        db,
                        timeout_s=wl.sql_timeout_s,
                        row_cap=wl.row_cap,
                    )
                    rec.exec_s = clock() - t
                    rec.status, rec.text = out.status, out.text
                history.append(Message(role="tool", tool_call_id=call.id, content=rec.text))
            if task.answer_sql is not None:
                task.outcome = "submitted"
                return task
        task.outcome = "turn_cap"
        return task
    finally:
        task.wall_s = clock() - t0
