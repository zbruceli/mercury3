"""UC2 agent tools over a read-only SQLite database (F5.2) and the argument checker (F5.5).

Model-written SQL runs on the host, but only through `ReadOnlyDB`, which stacks:
- a `mode=ro` URI connection plus `PRAGMA query_only` (no writes at the SQLite level),
- an authorizer that refuses ATTACH/DETACH, every write action, and all but a few
  read-only PRAGMAs (so the model can't turn query_only back off),
- no attached databases at all (SQLITE_LIMIT_ATTACHED = 0),
- a wall-clock deadline enforced through a progress handler, and a row cap.
Python's sqlite3 also refuses multiple statements per execute() and keeps extension
loading disabled.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dllm_eval.schemas import ToolCall, ToolSpec

READ_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list"}
)
_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)
CELL_CHARS = 100  # long text cells are cut in tool output (not in scoring)
SAMPLE_ROWS = 3  # example rows shown by describe_table


class SqlTimeout(Exception):
    pass


def _authorizer(action: int, arg1: str | None, *_: Any) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in READ_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class ReadOnlyDB:
    """One database file. Every query opens its own connection, so calls are thread-safe
    (the agent runs them in worker threads to keep the event loop free for timing)."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        self.uri = path.resolve().as_uri() + "?mode=ro"

    @contextmanager
    def connect(self, timeout_s: float) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.uri, uri=True, check_same_thread=False)
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            deadline = time.monotonic() + timeout_s
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.set_authorizer(_authorizer)
            yield conn
        finally:
            conn.close()

    def query(
        self, sql: str, *, timeout_s: float, max_rows: int
    ) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        """(column names, up to max_rows rows, truncated?). Raises sqlite3.Error or SqlTimeout."""
        with self.connect(timeout_s) as conn:
            try:
                cur = conn.execute(sql)
                rows = cur.fetchmany(max_rows + 1)
            except sqlite3.OperationalError as e:
                if str(e) == "interrupted":
                    raise SqlTimeout(f"query exceeded {timeout_s:g} s") from e
                raise
            cols = [d[0] for d in cur.description or []]
        return cols, rows[:max_rows], len(rows) > max_rows

    def table_names(self) -> list[str]:
        _, rows, _ = self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name",
            timeout_s=5,
            max_rows=10_000,
        )
        return [r[0] for r in rows]

    def create_sql(self, table: str) -> str:
        _, rows, _ = self.query(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = "
            + _quote_literal(table),
            timeout_s=5,
            max_rows=1,
        )
        return str(rows[0][0]) if rows else ""


def _quote_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _quote_ident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def render_cell(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bytes):
        return f"<blob {len(v)} bytes>"
    s = str(v).replace("\n", " ").replace("\t", " ")
    return s if len(s) <= CELL_CHARS else s[:CELL_CHARS] + "..."


def render_rows(cols: list[str], rows: list[tuple[Any, ...]], truncated: bool) -> str:
    lines = ["\t".join(cols)] + ["\t".join(render_cell(v) for v in r) for r in rows]
    if truncated:
        lines.append(f"(only the first {len(rows)} rows are shown; the query returned more)")
    elif not rows:
        lines.append("(0 rows)")
    return "\n".join(lines)


# --- tool specs (frozen with the prompt version in prompts.py) ------------------------

_SQL_ARG = {"type": "string", "description": "One SQLite SELECT statement."}

LIST_TABLES = ToolSpec(
    name="list_tables",
    description="List the tables in the database.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)
DESCRIBE_TABLE = ToolSpec(
    name="describe_table",
    description=(
        "Show a table's CREATE TABLE statement and a few example rows. Use it before "
        "writing queries against the table."
    ),
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Exact table name."}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
RUN_SQL = ToolSpec(
    name="run_sql",
    description="Run a read-only SQLite query and see its result (row-capped, time-limited).",
    parameters={
        "type": "object",
        "properties": {"query": _SQL_ARG},
        "required": ["query"],
        "additionalProperties": False,
    },
)
SUBMIT_ANSWER = ToolSpec(
    name="submit_answer",
    description=(
        "Submit your final answer: the single SQLite query whose result answers the "
        "question. This ends the task."
    ),
    parameters={
        "type": "object",
        "properties": {"sql": _SQL_ARG},
        "required": ["sql"],
        "additionalProperties": False,
    },
)
EXPLORE_TOOLS = (LIST_TABLES, DESCRIBE_TABLE, RUN_SQL)
ALL_TOOLS = (*EXPLORE_TOOLS, SUBMIT_ANSWER)

# Strict-schema sub-run (F5.6): the final answer is the reply text itself, constrained by
# response_format, instead of a submit_answer tool call.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sql": _SQL_ARG},
    "required": ["sql"],
    "additionalProperties": False,
}


# --- argument validation (F5.5) -------------------------------------------------------

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
}


def schema_errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """The JSON Schema subset our tool schemas use: type, properties, required,
    additionalProperties: false. Unknown keywords are ignored (none are used)."""
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        py = _JSON_TYPES[t]
        if not isinstance(value, py) or (t in ("integer", "number") and isinstance(value, bool)):
            return [f"{path}: expected {t}"]
    if isinstance(value, dict):
        props: dict[str, Any] = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}: missing required property {req!r}")
        if schema.get("additionalProperties") is False:
            errs += [f"{path}: unexpected property {k!r}" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                errs += schema_errors(value[k], sub, f"{path}.{k}")
    return errs


Validity = Literal["valid", "unknown_tool", "invalid_json", "schema_violation"]


@dataclass(frozen=True)
class CheckedCall:
    validity: Validity
    args: dict[str, Any] | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.validity == "valid"


def check_call(call: ToolCall, offered: tuple[ToolSpec, ...]) -> CheckedCall:
    spec = next((t for t in offered if t.name == call.name), None)
    if spec is None:
        return CheckedCall("unknown_tool", None, f"no tool named {call.name!r}")
    raw = call.arguments.strip() or "{}"  # some APIs send "" for a no-argument call
    try:
        args = json.loads(raw)
    except ValueError as e:
        return CheckedCall("invalid_json", None, f"arguments are not valid JSON: {e}")
    errs = schema_errors(args, spec.parameters)
    if errs:
        return CheckedCall("schema_violation", None, "; ".join(errs))
    return CheckedCall("valid", args)


# --- execution ------------------------------------------------------------------------

ToolStatus = Literal["ok", "sql_error", "timeout", "bad_table", "invalid_call"]


@dataclass(frozen=True)
class ToolOutcome:
    text: str  # what the model sees as the tool result
    status: ToolStatus


def execute(
    name: str, args: dict[str, Any], db: ReadOnlyDB, *, timeout_s: float, row_cap: int
) -> ToolOutcome:
    """Run one validated exploration tool. SQL problems become tool results, never
    exceptions: the model sees the error and can recover (that's part of the task)."""
    try:
        if name == "list_tables":
            return ToolOutcome("\n".join(db.table_names()), "ok")
        if name == "describe_table":
            table = args["name"]
            if table not in db.table_names():
                return ToolOutcome(f"Error: no table named {table!r}", "bad_table")
            cols, rows, _ = db.query(
                f"SELECT * FROM {_quote_ident(table)} LIMIT {SAMPLE_ROWS}",
                timeout_s=timeout_s,
                max_rows=SAMPLE_ROWS,
            )
            return ToolOutcome(
                db.create_sql(table) + "\n\nExample rows:\n" + render_rows(cols, rows, False),
                "ok",
            )
        if name == "run_sql":
            cols, rows, truncated = db.query(args["query"], timeout_s=timeout_s, max_rows=row_cap)
            return ToolOutcome(render_rows(cols, rows, truncated), "ok")
    except SqlTimeout as e:
        return ToolOutcome(f"Error: {e}", "timeout")
    except (sqlite3.Error, ValueError, OverflowError) as e:
        # ValueError: e.g. embedded NUL; OverflowError: integer out of range
        return ToolOutcome(f"Error: {e}", "sql_error")
    raise ValueError(f"not an exploration tool: {name!r}")
