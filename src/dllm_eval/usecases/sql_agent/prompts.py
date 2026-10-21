"""UC2 agent prompt (F5.3). Frozen and versioned like UC1's: changing any text below, or a
tool definition in tools.py, means a new PROMPT_VERSION, never an edit in place. Every
model gets the same system prompt, user message, and tool schemas (PRD UC2, R1)."""

from __future__ import annotations

from typing import Literal

from dllm_eval.schemas import Message
from dllm_eval.usecases.sql_agent.datasets import BirdQuestion

PROMPT_VERSION = "uc2-v1"

AnswerMode = Literal["tool", "response_format"]

SYSTEM = (
    "You are a data analyst answering questions about a SQLite database. Explore the "
    "database with the tools: list_tables, describe_table, and run_sql (read-only). Check "
    "your query's result with run_sql before you answer. {finish} Be efficient: you have "
    "at most {max_turns} turns."
)
FINISH_TOOL = (
    "When you are confident, call submit_answer with the single SQLite query whose result "
    "answers the question."
)
FINISH_RESPONSE_FORMAT = (
    "When you are confident, stop calling tools and reply with a JSON object whose `sql` "
    "field is the single SQLite query whose result answers the question."
)

USER = "Database: {db_id}\n\nQuestion: {question}\n\nHint: {evidence}"
USER_NO_HINT = "Database: {db_id}\n\nQuestion: {question}"

# Sent after a reply with neither a tool call nor (in response_format mode) an answer.
NUDGE_TOOL = "Please continue by calling one of the tools. Call submit_answer to finish."


def system_text(mode: AnswerMode, max_turns: int) -> str:
    finish = FINISH_TOOL if mode == "tool" else FINISH_RESPONSE_FORMAT
    return SYSTEM.format(finish=finish, max_turns=max_turns)


def user_text(q: BirdQuestion) -> str:
    if q.evidence.strip():
        return USER.format(db_id=q.db_id, question=q.question.strip(), evidence=q.evidence.strip())
    return USER_NO_HINT.format(db_id=q.db_id, question=q.question.strip())


def messages(q: BirdQuestion, mode: AnswerMode, max_turns: int) -> list[Message]:
    return [
        Message(role="system", content=system_text(mode, max_turns)),
        Message(role="user", content=user_text(q)),
    ]
