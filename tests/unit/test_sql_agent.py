"""UC2 SQL agent (F5.1-F5.7, F6.4 UC2): dataset, tools, agent loop, scoring, analysis.

Everything runs offline: a tiny SQLite database built here, a crafted BIRD-shaped archive,
and the fake provider's scripted turns. No real BIRD data is needed.
"""

import asyncio
import copy
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from dllm_eval.analysis import uc2
from dllm_eval.cli import app
from dllm_eval.config import ModelRegistry
from dllm_eval.experiment import Arm, Experiment, PromptItem, SqlAgentWorkload
from dllm_eval.logging_store import RunStore
from dllm_eval.providers import build_provider
from dllm_eval.runner import dry_run, run_experiment
from dllm_eval.schemas import ToolCall
from dllm_eval.usecases.codegen.datasets import DatasetSpec, sha256_of
from dllm_eval.usecases.sql_agent import agent, compare, prompts, tools
from dllm_eval.usecases.sql_agent import datasets as bird
from dllm_eval.usecases.sql_agent.scoring import score_run
from dllm_eval.usecases.sql_agent.trace import format_trace, replay
from tests.conftest import REPO

# --- fixtures -------------------------------------------------------------------------


def make_db(path: Path) -> Path:
    if path.exists():
        return path
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE team (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        CREATE TABLE player (id INTEGER PRIMARY KEY, team_id INTEGER, name TEXT, score REAL);
        INSERT INTO team VALUES (1, 'Ants', 'Austin'), (2, 'Bees', 'Boston'), (3, 'Cats', NULL);
        INSERT INTO player VALUES (1, 1, 'ann', 10.5), (2, 1, 'bob', 7.0), (3, 2, 'cy', 12.25),
            (4, 2, 'di', NULL), (5, 3, 'ed', 3.0);
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(tmp_path: Path) -> tools.ReadOnlyDB:
    return tools.ReadOnlyDB(make_db(tmp_path / "league.sqlite"))


def q(
    qid: int, difficulty: str = "simple", sql: str = "SELECT name FROM team"
) -> bird.BirdQuestion:
    return bird.BirdQuestion(
        id=f"bird/{qid}", question_id=qid, db_id="league", question=f"question {qid}?",
        evidence="team.city is the home city", gold_sql=sql, difficulty=difficulty,  # type: ignore[arg-type]
    )  # fmt: skip


# --- F5.2 tools -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO team VALUES (9, 'x', 'y')",
        "UPDATE team SET name = 'z'",
        "DELETE FROM player",
        "CREATE TABLE t (a)",
        "DROP TABLE team",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA query_only = OFF",
        "PRAGMA journal_mode = WAL",
    ],
)
def test_writes_and_escapes_are_rejected(db: tools.ReadOnlyDB, sql: str) -> None:
    out = tools.execute("run_sql", {"query": sql}, db, timeout_s=2, row_cap=10)
    assert out.status == "sql_error"
    _, rows, _ = db.query("SELECT count(*) FROM team", timeout_s=2, max_rows=1)
    assert rows == [(3,)]


def test_read_pragmas_and_selects_work(db: tools.ReadOnlyDB) -> None:
    out = tools.execute(
        "run_sql", {"query": "PRAGMA table_info(team)"}, db, timeout_s=2, row_cap=10
    )
    assert out.status == "ok" and "city" in out.text
    out = tools.execute(
        "run_sql",
        {"query": "SELECT name, score FROM player ORDER BY id"},
        db,
        timeout_s=2,
        row_cap=10,
    )
    assert out.text.splitlines() == [
        "name\tscore",
        "ann\t10.5",
        "bob\t7.0",
        "cy\t12.25",
        "di\tNULL",
        "ed\t3.0",
    ]


def test_timeout_is_enforced(db: tools.ReadOnlyDB) -> None:
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    out = tools.execute("run_sql", {"query": sql}, db, timeout_s=0.2, row_cap=10)
    assert out.status == "timeout" and "0.2 s" in out.text


def test_row_cap_and_multi_statement(db: tools.ReadOnlyDB) -> None:
    out = tools.execute("run_sql", {"query": "SELECT * FROM player"}, db, timeout_s=2, row_cap=2)
    assert out.status == "ok" and len(out.text.splitlines()) == 4 and "first 2 rows" in out.text
    out = tools.execute("run_sql", {"query": "SELECT 1; SELECT 2"}, db, timeout_s=2, row_cap=2)
    assert out.status == "sql_error"
    out = tools.execute("run_sql", {"query": "SELECT * FROM nope"}, db, timeout_s=2, row_cap=2)
    assert out.status == "sql_error" and "no such table" in out.text


def test_list_and_describe(db: tools.ReadOnlyDB) -> None:
    assert tools.execute("list_tables", {}, db, timeout_s=2, row_cap=5).text == "player\nteam"
    d = tools.execute("describe_table", {"name": "team"}, db, timeout_s=2, row_cap=5)
    assert d.status == "ok" and d.text.startswith("CREATE TABLE team") and "Ants\tAustin" in d.text
    bad = tools.execute("describe_table", {"name": 'team" --'}, db, timeout_s=2, row_cap=5)
    assert bad.status == "bad_table"


def test_cells_are_rendered_compactly() -> None:
    assert tools.render_cell(None) == "NULL"
    assert tools.render_cell(b"\x00\x01") == "<blob 2 bytes>"
    assert tools.render_cell("a\tb\nc") == "a b c"
    assert tools.render_cell("x" * 500).endswith("...") and len(tools.render_cell("x" * 500)) == 103


def test_db_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tools.ReadOnlyDB(tmp_path / "missing.sqlite")


# --- F5.5 tool-call validity ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "args", "validity"),
    [
        ("run_sql", '{"query": "SELECT 1"}', "valid"),
        ("list_tables", "", "valid"),
        ("list_tables", "{}", "valid"),
        ("drop_db", "{}", "unknown_tool"),
        ("run_sql", '{"query": "SELECT 1"', "invalid_json"),
        ("run_sql", "[1]", "schema_violation"),
        ("run_sql", "{}", "schema_violation"),
        ("run_sql", '{"query": 3}', "schema_violation"),
        ("run_sql", '{"query": "x", "limit": 3}', "schema_violation"),
        ("describe_table", '{"name": true}', "schema_violation"),
    ],
)
def test_check_call(name: str, args: str, validity: str) -> None:
    got = tools.check_call(ToolCall(id="c", name=name, arguments=args), tools.ALL_TOOLS)
    assert got.validity == validity
    assert got.ok == (validity == "valid")


def test_schema_errors_types() -> None:
    assert tools.schema_errors(True, {"type": "integer"}) == ["$: expected integer"]
    assert tools.schema_errors(3, {"type": "number"}) == []
    assert (
        tools.schema_errors({"a": {}}, {"type": "object", "properties": {"a": {"type": "object"}}})
        == []
    )


# --- F5.4 execution accuracy ----------------------------------------------------------


def test_ex_is_order_insensitive_without_order_by() -> None:
    assert compare.ex_match([(1,), (2,)], [(2,), (1,)], ordered=False)
    assert not compare.ex_match([(1,), (2,)], [(2,), (1,)], ordered=True)
    assert compare.ex_match([(1,), (2,)], [(1,), (2,)], ordered=True)


def test_ex_duplicates_and_column_order() -> None:
    assert not compare.ex_match([(1,), (1,)], [(1,)], ordered=False)
    assert compare.ex_bird([(1,), (1,)], [(1,)])  # BIRD's set rule ignores duplicates
    assert not compare.ex_match([(1, "a")], [("a", 1)], ordered=False)


def test_ex_nulls_and_floats() -> None:
    assert compare.ex_match([(None, 0.1 + 0.2)], [(None, 0.3)], ordered=False)
    assert not compare.ex_bird([(0.1 + 0.2,)], [(0.3,)])  # raw values, as BIRD does
    assert compare.ex_match([(2.0,)], [(2,)], ordered=False)
    assert compare.ex_match([(-0.0,)], [(0.0,)], ordered=False)
    assert not compare.ex_match([(0.3,)], [(0.31,)], ordered=False)
    assert compare.ex_match([(float("nan"),)], [(float("nan"),)], ordered=False)
    assert compare.ex_match([(True,)], [(1,)], ordered=False)


@pytest.mark.parametrize(
    ("sql", "ordered"),
    [
        ("SELECT a FROM t ORDER BY a", True),
        ("select a from t order  by a desc limit 1", True),
        ("SELECT a FROM t WHERE b = (SELECT b FROM u ORDER BY c LIMIT 1)", False),
        ("SELECT 'order by' FROM t", False),
        ('SELECT "ORDER BY" FROM t', False),
        ("SELECT a FROM t -- ORDER BY a", False),
        ("SELECT a FROM (SELECT a FROM t ORDER BY a) ORDER BY a", True),
        ("SELECT a FROM t", False),
    ],
)
def test_top_level_order_by(sql: str, ordered: bool) -> None:
    assert compare.has_top_level_order_by(sql) is ordered


# --- F5.1 dataset ---------------------------------------------------------------------


def test_allocate_is_proportional() -> None:
    assert bird.allocate({"simple": 148, "moderate": 247, "challenging": 101}, 150) == {
        "simple": 45, "moderate": 75, "challenging": 30,
    }  # fmt: skip
    assert sum(bird.allocate({"a": 1, "b": 1, "c": 1}, 2).values()) == 2
    with pytest.raises(ValueError, match="cannot draw"):
        bird.allocate({"a": 1}, 2)


def test_stratified_sample_is_deterministic_and_excludes_unscorable() -> None:
    pool = [q(i, ("simple", "moderate", "challenging")[i % 3]) for i in range(1, 61)]
    pool.append(q(518, "moderate"))
    a = bird.stratified_sample(pool, 12, seed=3)
    b = bird.stratified_sample(list(reversed(pool)), 12, seed=3)
    assert [x.id for x in a] == [x.id for x in b]
    assert [x.question_id for x in a] == sorted(x.question_id for x in a)
    assert {x.difficulty for x in a} == {"simple", "moderate", "challenging"}
    assert all(x.id not in bird.GOLD_EXCLUDED for x in a)
    assert bird.stratified_sample(pool, 12, seed=4) != a


def test_gold_exclusions_are_pinned() -> None:
    assert frozenset({"bird/518", "bird/701"}) == bird.GOLD_EXCLUDED


def _archive(tmp_path: Path) -> tuple[DatasetSpec, Path]:
    db = make_db(tmp_path / "src.sqlite")
    rows = [
        {"question_id": 1, "db_id": "league", "question": "How many teams?", "evidence": "",
         "SQL": "SELECT count(*) FROM team", "difficulty": "simple"},
        {"question_id": 2, "db_id": "league", "question": "Top scorer?", "evidence": "score is points",
         "SQL": "SELECT name FROM player ORDER BY score DESC LIMIT 1", "difficulty": "moderate"},
    ]  # fmt: skip
    rows.append(dict(rows[1]))  # the real release repeats two questions verbatim
    zpath = tmp_path / "data/raw/bird-minidev/minidev.zip"
    zpath.parent.mkdir(parents=True)
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(bird.QUESTIONS_MEMBER, json.dumps(rows))
        z.write(db, "minidev/MINIDEV/dev_databases/league/league.sqlite")
        z.writestr("minidev/MINIDEV/dev_databases/../../evil/evil.sqlite", "x")
        z.writestr("minidev/MINIDEV_mysql/BIRD_dev.sql", "-- not extracted")
    spec = DatasetSpec(
        version="test", url="https://example.invalid/minidev.zip",
        path=Path("data/raw/bird-minidev/minidev.zip"), bytes=zpath.stat().st_size,
        sha256=sha256_of(zpath), problems=3,
    )  # fmt: skip
    return spec, tmp_path


def test_extract_and_load(tmp_path: Path) -> None:
    spec, root = _archive(tmp_path)
    dest = bird.extract(spec, root)
    assert sorted(p.name for p in (dest / "databases").iterdir()) == ["league.sqlite"]
    assert not (root / "evil").exists() and not (root / "data/evil").exists()
    qs = bird.load_questions(spec, root)
    assert [x.id for x in qs] == ["bird/1", "bird/2"]  # duplicate dropped
    assert qs[1].gold_sql.endswith("LIMIT 1") and qs[1].evidence == "score is points"
    assert bird.db_path(spec, "league", root).is_file()
    assert bird.extract(spec, root) == dest  # idempotent
    with pytest.raises(ValueError, match="bad db_id"):
        bird.db_path(spec, "../x", root)
    (dest / "databases/league.sqlite").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        bird.load_questions(spec, root)


def test_conflicting_duplicates_are_refused(tmp_path: Path) -> None:
    spec, root = _archive(tmp_path)
    dest = bird.extract(spec, root)
    rows = json.loads((dest / "questions.json").read_text())
    rows[2]["SQL"] = "SELECT 1"
    (dest / "questions.json").write_text(json.dumps(rows))
    marker = json.loads((dest / bird.MARKER).read_text())
    marker["files"]["questions.json"] = (dest / "questions.json").stat().st_size
    (dest / bird.MARKER).write_text(json.dumps(marker))
    with pytest.raises(ValueError, match="different content"):
        bird.load_questions(spec, root)


def test_extract_needs_the_archive(tmp_path: Path) -> None:
    spec = DatasetSpec(version="t", url="u", path=Path("nope.zip"), bytes=1, sha256="0", problems=1)
    with pytest.raises(FileNotFoundError):
        bird.extracted_dir(spec, tmp_path)
    with pytest.raises(FileNotFoundError):
        bird.extract(spec, tmp_path)


def test_committed_id_lists_are_stratified_samples() -> None:
    from dllm_eval.usecases.sql_agent.workload import read_ids

    r4 = read_ids(REPO / "configs/uc2/bird_r4_150.txt")
    pilot = read_ids(REPO / "configs/uc2/bird_pilot_10.txt")
    assert len(r4) == 150 and len(pilot) == 10 and set(pilot) <= set(r4)
    assert not set(r4) & bird.GOLD_EXCLUDED


# --- prompts --------------------------------------------------------------------------

PROMPT_DIGESTS = {"uc2-v1": "91b34b3225f809f83674eb4e54f2581d0a1c4c2d3972479099e31f64cb43e9b1"}


def _prompt_digest() -> str:
    parts = [prompts.SYSTEM, prompts.FINISH_TOOL, prompts.FINISH_RESPONSE_FORMAT, prompts.USER,
             prompts.USER_NO_HINT, prompts.NUDGE_TOOL,
             json.dumps([t.model_dump() for t in tools.ALL_TOOLS], sort_keys=True),
             json.dumps(tools.ANSWER_SCHEMA, sort_keys=True)]  # fmt: skip
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def test_prompt_and_tools_are_frozen() -> None:
    assert _prompt_digest() == PROMPT_DIGESTS[prompts.PROMPT_VERSION]


def test_messages() -> None:
    m = prompts.messages(q(1), "tool", 8)
    assert [x.role for x in m] == ["system", "user"]
    assert "submit_answer" in (m[0].content or "") and "at most 8 turns" in (m[0].content or "")
    assert "Hint: team.city" in (m[1].content or "")
    no_hint = q(2).model_copy(update={"evidence": " "})
    assert "Hint" not in prompts.user_text(no_hint)
    assert "JSON object" in (prompts.messages(q(1), "response_format", 8)[0].content or "")


# --- F5.3 agent loop ------------------------------------------------------------------


def wl(**kw: Any) -> SqlAgentWorkload:
    base = {"kind": "sql_agent", "prompt_version": "uc2-v1", "ids_file": "ids.txt"}
    return SqlAgentWorkload.model_validate(base | kw)


GOOD_SCRIPT = [
    {"text": "Let me look.", "calls": [["list_tables", {}]]},
    {"calls": [["describe_table", {"name": "team"}], ["describe_table", {"name": "player"}]]},
    {"calls": [["run_sql", {"query": "SELECT name FROM team"}]]},
    {"calls": [["submit_answer", {"sql": "SELECT name FROM team"}]]},
]


def arm(script: list[dict[str, Any]], **extras: Any) -> Arm:
    return Arm(
        name="instant", model="fake-diffusion", reasoning_effort="instant", temperature=0.5,
        max_completion_tokens=100, provider_extras={"fake_script": script, **extras},
    )  # fmt: skip


def run_one(
    reg: ModelRegistry, db: tools.ReadOnlyDB, script: list[dict[str, Any]], **kw: Any
) -> agent.TaskResult:
    w = wl(**kw)
    item = PromptItem(id="bird/1", messages=prompts.messages(q(1), w.answer_mode, w.max_turns))
    provider = build_provider("fake-diffusion", reg)
    return asyncio.run(agent.run_task(provider, arm(script), item, db, w))


def test_agent_happy_path(fake_registry: ModelRegistry, db: tools.ReadOnlyDB) -> None:
    t = run_one(fake_registry, db, GOOD_SCRIPT)
    assert t.outcome == "submitted" and t.answer_sql == "SELECT name FROM team"
    assert len(t.turns) == 4 and t.error is None
    assert [len(x.tools) for x in t.turns] == [1, 2, 1, 1]
    assert t.turns[0].result.content == "Let me look."  # preamble kept next to the call
    assert t.turns[2].tools[0].text.startswith("name\nAnts")
    roles = [m.role for m in t.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "tool", "tool",
                     "assistant", "tool", "assistant"]  # fmt: skip
    assert t.messages[3].tool_call_id == t.messages[2].tool_calls[0].id
    assert [x.n_messages for x in t.turns] == [2, 4, 7, 9]
    assert t.wall_s >= sum(x.exec_s for turn in t.turns for x in turn.tools)
    rec = t.to_record()
    assert rec["outcome"] == "submitted" and len(rec["messages"]) == 10
    assert "raw_assistant" not in rec["turns"][0]["result"]


def test_agent_nudges_then_recovers(fake_registry: ModelRegistry, db: tools.ReadOnlyDB) -> None:
    script = [{"text": "I think the answer is Ants."}, *GOOD_SCRIPT]
    t = run_one(fake_registry, db, script)
    assert t.turns[0].nudged and t.messages[3].role == "user"
    assert t.outcome == "submitted"


def test_agent_reports_invalid_calls(fake_registry: ModelRegistry, db: tools.ReadOnlyDB) -> None:
    script = [
        {"calls": [["run_sql", '{"query": "SELECT 1"'], ["nope", {}]]},
        {"calls": [["submit_answer", {"sql": ""}], ["submit_answer", {"query": "x"}]]},
        {"calls": [["submit_answer", {"sql": "SELECT 1"}]]},
    ]
    t = run_one(fake_registry, db, script)
    assert [r.validity for r in t.turns[0].tools] == ["invalid_json", "unknown_tool"]
    assert t.turns[0].tools[0].text.startswith("Error: invalid tool call")
    assert t.turns[1].tools[1].validity == "schema_violation"
    # an empty sql string is schema-valid, so it is the (wrong) submitted answer
    assert t.outcome == "submitted" and t.answer_sql == ""


@pytest.mark.parametrize(
    ("script", "kw", "outcome"),
    [
        ([{"calls": [["list_tables", {}]]}], {"max_turns": 3}, "turn_cap"),
        ([{"text": "thinking...", "finish": "length"}], {}, "budget_out"),
        (
            [
                {
                    "calls": [
                        ["run_sql", '{"query": "SEL'],
                    ],
                    "finish": "length",
                }
            ],
            {},
            "budget_out",
        ),
        ([{"text": ""}], {}, "empty_response"),
        ([{"text": '{"sql": "SELECT 1"}'}], {"answer_mode": "response_format"}, "submitted"),
        ([{"text": "SELECT 1"}], {"answer_mode": "response_format"}, "invalid_answer"),
        ([{"text": '{"sql": "  "}'}], {"answer_mode": "response_format"}, "invalid_answer"),
        ([{"text": '{"sql": "x", "y": 1}'}], {"answer_mode": "response_format"}, "invalid_answer"),
    ],
)
def test_agent_outcomes(
    fake_registry: ModelRegistry, db: tools.ReadOnlyDB, script: Any, kw: Any, outcome: str
) -> None:
    t = run_one(fake_registry, db, script, **kw)
    assert t.outcome == outcome
    if outcome == "turn_cap":
        assert len(t.turns) == 3


def test_response_format_request(fake_registry: ModelRegistry) -> None:
    req = agent.build_request(arm([]), wl(answer_mode="response_format"), [], stream=True)
    assert [t.name for t in req.tools] == ["list_tables", "describe_table", "run_sql"]
    assert req.response_format is not None and req.response_format.strict
    tool_req = agent.build_request(arm([]), wl(), [], stream=True)
    assert tool_req.response_format is None and tool_req.tools[-1].name == "submit_answer"
    assert tool_req.tool_choice == "auto"


def test_provider_error_keeps_partial_trace(
    fake_registry: ModelRegistry, db: tools.ReadOnlyDB
) -> None:
    w = wl()
    item = PromptItem(id="bird/1", messages=prompts.messages(q(1), "tool", 8))
    a = arm(GOOD_SCRIPT, fake_fail=[400])
    t = asyncio.run(agent.run_task(build_provider("fake-diffusion", fake_registry), a, item, db, w))
    assert t.outcome == "error" and t.error is not None and t.error.status == 400
    assert t.turns == []


def test_cerebras_cannot_run_strict_subrun(repo_registry: ModelRegistry) -> None:
    exp = Experiment.model_validate(
        {
            "name": "x", "budget_usd": 1,
            "arms": [{"name": "low", "model": "gpt-oss-120b-cerebras", "reasoning_effort": "low",
                      "max_completion_tokens": 100}],
            "workload": {"kind": "sql_agent", "prompt_version": "uc2-v1", "ids_file": "x",
                         "answer_mode": "response_format"},
        }
    )  # fmt: skip
    with pytest.raises(ValueError, match="OQ5"):
        exp.check_against(repo_registry)
    p = build_provider("gpt-oss-120b-cerebras", repo_registry, client=object())
    req = agent.build_request(exp.arms[0], exp.workload, [], stream=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="response_format"):
        p.validate(req)


# --- runner, scoring, analysis, trace (end to end on the fake provider) ---------------

EXP: dict[str, Any] = {
    "name": "fake-uc2",
    "seed": 5,
    "repeats": 2,
    "budget_usd": 1.0,
    "max_concurrency_per_provider": 3,
    "warmup_calls_per_arm": 1,
    "arms": [
        {"name": "instant", "model": "fake-diffusion", "reasoning_effort": "instant", "temperature": 0.5,
         "max_completion_tokens": 50, "provider_extras": {"fake_script": GOOD_SCRIPT}},
        {"name": "none", "model": "fake-ar", "reasoning_effort": "none", "max_completion_tokens": 50,
         "expected_output_tokens": 20,
         "provider_extras": {"fake_script": [GOOD_SCRIPT[0], GOOD_SCRIPT[2],
                                             {"calls": [["submit_answer", {"sql": "SELECT city FROM team"}]]}]}},
    ],
    "workload": {"kind": "sql_agent", "prompt_version": "uc2-v1", "ids_file": "ids.txt"},
}  # fmt: skip

QUESTIONS = {
    "bird/1": q(1, "simple"),
    "bird/2": q(2, "moderate", "SELECT name FROM team ORDER BY id"),
}


def uc2_run(reg: ModelRegistry, tmp: Path, **overrides: Any) -> tuple[Any, RunStore, Experiment]:
    d = copy.deepcopy(EXP)
    d.update(overrides)
    e = Experiment.model_validate(d)
    assert isinstance(e.workload, SqlAgentWorkload)
    from dllm_eval.usecases.sql_agent.workload import build_items

    items = build_items(list(QUESTIONS.values()), e.workload)
    dbpath = make_db(tmp / "league.sqlite")
    store = RunStore(tmp / "run")
    if not store.exists():
        store.create({"run_id": "r", "experiment": e.model_dump(mode="json")})
    s = asyncio.run(
        run_experiment(
            e, reg, store, "r", items=items, task_dbs={i: dbpath for i in QUESTIONS},
            log=lambda _: None,
        )
    )  # fmt: skip
    return s, store, e


def test_end_to_end(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    s, store, e = uc2_run(fake_registry, tmp_path)
    assert (s.planned, s.ok, s.errors, s.aborted) == (8, 8, 0, None)
    recs = store.latest_by_job()
    rec = next(r for r in recs.values() if r["arm"] == "fake-diffusion:instant")
    assert rec["task"]["outcome"] == "submitted" and rec["cost_usd"] > 0
    assert rec["request"]["tools"][-1]["name"] == "submit_answer"
    assert sum(r["warmup"] for r in recs.values()) == 2

    # resume: nothing left to do
    s2, _, _ = uc2_run(fake_registry, tmp_path)
    assert (s2.skipped_done, s2.ok) == (8, 0)

    # scoring: the fake-ar arm answers with the wrong column on purpose
    rows = score_run(store, QUESTIONS, {i: tmp_path / "league.sqlite" for i in QUESTIONS},
                     tmp_path / "out/uc2_scores.jsonl", log=lambda _: None)  # fmt: skip
    by_arm: dict[str, list[bool]] = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r["ex"])
    assert all(by_arm["fake-diffusion:instant"]) and not any(by_arm["fake-ar:none"])
    ordered = [r for r in rows if r["item_id"] == "bird/2" and r["arm"] == "fake-diffusion:instant"]
    assert all(r["ex"] for r in ordered)  # same order as the gold ORDER BY id

    tasks, turns = uc2.task_rows(store)
    tdf, udf = pd.DataFrame(tasks), pd.DataFrame(turns)
    table = {r["arm"]: r for r in uc2.arm_table(tdf, udf, rows, seed=1)}
    d = table["fake-diffusion:instant"]
    assert d["ex"] == 1.0 and d["submitted"] == 1.0 and d["tool_call_validity"] == 1.0
    assert d["turns_mean"] == 4 and d["cost_per_correct"] is not None and d["latency_n"] == 3
    assert table["fake-ar:none"]["cost_per_correct"] is None
    md = uc2.accuracy_markdown(list(table.values())) + uc2.reliability_markdown(
        list(table.values())
    )
    assert "fake-ar:none" in md

    pts = uc2.speedup_points(tdf, udf, reference="fake-diffusion:instant")
    ref = next(p for p in pts if p.arm == "fake-diffusion:instant")
    assert ref.call_ratio == pytest.approx(1.0) and ref.task_ratio == pytest.approx(1.0)
    png = uc2.plot_speedup(pts, tmp_path / "out/speed.png", title="t", subtitle="s")
    assert png.stat().st_size > 1000
    assert uc2.write_speedup_csv(pts, tmp_path / "out/speed.csv").read_text().startswith("arm,")
    with pytest.raises(ValueError, match="reference"):
        uc2.speedup_points(tdf, udf, reference="nope:x")

    # traces replay against the database, and a tampered trace doesn't
    w = e.workload
    assert isinstance(w, SqlAgentWorkload)
    db = tools.ReadOnlyDB(tmp_path / "league.sqlite")
    assert all(replay(r, w, db) == [] for r in recs.values())
    bad = copy.deepcopy(rec)
    bad["task"]["turns"][2]["tools"][0]["text"] = "forged"
    assert replay(bad, w, db) == ["turn 2 call_2_0: tool result differs"]
    text = format_trace(rec)
    assert "turn 1" in text and "submit_answer" in text and "answer: SELECT" in text


def test_harness_errors_and_failed_tasks_score(
    fake_registry: ModelRegistry, tmp_path: Path
) -> None:
    _, store, _ = uc2_run(fake_registry, tmp_path)
    broken = {"bird/1": q(1, sql="SELECT * FROM missing_table"), "bird/2": QUESTIONS["bird/2"]}
    rows = score_run(store, broken, {i: tmp_path / "league.sqlite" for i in broken},
                     tmp_path / "s.jsonl", log=lambda _: None)  # fmt: skip
    assert {r["status"] for r in rows if r["item_id"] == "bird/1"} == {"harness_error"}


def test_provider_errors_are_recorded(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    d = copy.deepcopy(EXP)
    d["arms"][1]["provider_extras"]["fake_fail"] = [400]
    s, store, _ = uc2_run(fake_registry, tmp_path, arms=d["arms"])
    assert s.errors == 4 and s.ok == 4 and s.aborted is None
    errs = [r for r in store.latest_by_job().values() if r["status"] == "error"]
    assert errs[0]["error"]["status"] == 400 and errs[0]["task"]["outcome"] == "error"


def test_billing_error_aborts(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    d = copy.deepcopy(EXP)
    d["arms"][1]["provider_extras"]["fake_fail"] = ["quota"]
    s, _, _ = uc2_run(fake_registry, tmp_path, arms=d["arms"], max_concurrency_per_provider=1)
    assert s.aborted and "run-fatal" in s.aborted


def test_warning_aborts(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    d = copy.deepcopy(EXP)
    d["arms"][0]["provider_extras"]["fake_warning"] = "temperature reset"
    s, _, _ = uc2_run(fake_registry, tmp_path, arms=d["arms"], max_concurrency_per_provider=1)
    assert s.aborted and "warning" in s.aborted


def test_config_error_aborts(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    d = copy.deepcopy(EXP)
    d["arms"][0]["provider_extras"]["bogus"] = 1
    s, store, _ = uc2_run(fake_registry, tmp_path, arms=d["arms"], max_concurrency_per_provider=1)
    assert s.aborted and "config error" in s.aborted
    assert any(r["status"] == "config_error" for r in store.iter_calls())


def test_dry_run_counts_turns(fake_registry: ModelRegistry) -> None:
    e = Experiment.model_validate(EXP)
    assert isinstance(e.workload, SqlAgentWorkload)
    from dllm_eval.usecases.sql_agent.workload import build_items

    items = build_items(list(QUESTIONS.values()), e.workload)
    rows = {r.arm_id: r for r in dry_run(e, fake_registry, items)}
    r = rows["fake-ar:none"]
    assert r.jobs == 4
    assert r.output_tokens_expected == 20 * 5 * 4
    assert r.output_tokens_worst == 50 * 8 * 4
    assert r.cost_worst > r.cost_expected > 0


# --- CLI ------------------------------------------------------------------------------


def _cli_run_dir(fake_registry: ModelRegistry, tmp_path: Path) -> tuple[Path, str]:
    _, store, e = uc2_run(fake_registry, tmp_path)
    store.manifest_path.write_text(
        json.dumps(
            {
                "run_id": "run",
                "created_at": "2026-10-21T00:00:00+00:00",
                "experiment": e.model_dump(mode="json"),
            }
        )
    )
    return tmp_path, "run"


def test_cli_trace_and_analyze(
    fake_registry: ModelRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir, run_id = _cli_run_dir(fake_registry, tmp_path)
    from dllm_eval.usecases.sql_agent import workload as uc2_workload

    monkeypatch.setattr(uc2_workload, "select_questions", lambda _wl, **_: list(QUESTIONS.values()))
    monkeypatch.setattr(
        uc2_workload, "db_paths", lambda qs, **_: {x.id: tmp_path / "league.sqlite" for x in qs}
    )
    cli = CliRunner()
    out = cli.invoke(app, ["trace", run_id, "--runs-dir", str(runs_dir)])
    assert out.exit_code == 0 and "fake-ar:none/bird/1/0" in out.output
    job = out.output.split("\t")[0]
    out = cli.invoke(app, ["trace", run_id, job, "--runs-dir", str(runs_dir)])
    assert out.exit_code == 0 and "turn 1" in out.output
    out = cli.invoke(app, ["trace", run_id, "--verify", "--all", "--runs-dir", str(runs_dir)])
    assert out.exit_code == 0 and "8 match, 0 differ" in out.output
    out = cli.invoke(app, ["trace", run_id, "nope", "--runs-dir", str(runs_dir)])
    assert out.exit_code == 1

    out_dir = tmp_path / "analysis"
    out = cli.invoke(
        app, ["analyze", run_id, "--runs-dir", str(runs_dir), "--out-dir", str(out_dir)]
    )
    assert out.exit_code == 0 and "not scored yet" in out.output
    out = cli.invoke(app, ["score", run_id, "--runs-dir", str(runs_dir), "--out-dir", str(out_dir)])
    assert out.exit_code == 0 and "Reliability" in out.output, out.output
    assert (out_dir / run_id / "uc2_scores.jsonl").exists()
    out = cli.invoke(
        app, ["analyze", run_id, "--runs-dir", str(runs_dir), "--out-dir", str(out_dir)]
    )
    assert out.exit_code == 0 and "not scored yet" not in out.output
    out = cli.invoke(app, ["charts", run_id, "--runs-dir", str(runs_dir), "--out-dir", str(out_dir),
                           "--reference", "fake-diffusion:instant"])  # fmt: skip
    assert out.exit_code == 0, out.output
    assert (out_dir / run_id / "fig_uc2_speedup.png").exists()


def test_cli_sample_bird(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pool = [q(i, ("simple", "moderate", "challenging")[i % 3]) for i in range(1, 31)]
    monkeypatch.setattr(bird, "load_questions", lambda *_a, **_k: pool)
    out = tmp_path / "ids.txt"
    r = CliRunner().invoke(app, ["sample-bird", "--n", "6", "--seed", "1", "--out", str(out)])
    assert r.exit_code == 0, r.output
    from dllm_eval.usecases.sql_agent.workload import read_ids

    ids = read_ids(out)
    assert len(ids) == 6
    sub = tmp_path / "sub.txt"
    r = CliRunner().invoke(
        app, ["sample-bird", "--n", "3", "--seed", "1", "--out", str(sub), "--within", str(out)]
    )
    assert r.exit_code == 0 and set(read_ids(sub)) <= set(ids)
    assert "--within" in sub.read_text()


def test_read_ids_rejects_duplicates(tmp_path: Path) -> None:
    from dllm_eval.usecases.sql_agent.workload import read_ids

    p = tmp_path / "ids.txt"
    p.write_text("# header\nbird/1\nbird/1\n")
    with pytest.raises(ValueError, match="duplicate"):
        read_ids(p)


def test_repo_uc2_configs_validate(repo_registry: ModelRegistry) -> None:
    for name in ("r1_pilot_uc2", "r1_pilot_uc2_strict", "r4_uc2_main", "r4b_uc2_strict"):
        cfg = yaml.safe_load((REPO / f"configs/experiments/{name}.yaml").read_text())
        e = Experiment.model_validate(cfg)
        e.check_against(repo_registry)
        assert isinstance(e.workload, SqlAgentWorkload)
        assert (REPO / e.workload.ids_file).exists()
        assert {a.max_completion_tokens for a in e.arms} == {32768}  # PRD R1
        assert not any(a.model == "mercury-2.5" for a in e.arms)
