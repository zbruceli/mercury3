"""BIRD Mini-Dev data check (marker `bird`, `make test-bird`): needs the extracted dataset
(`dllm-eval fetch-data`), makes no API calls. The UC2 analogue of the sandbox check: every
sampled gold query must run on its read-only database, return rows, and score as correct
against itself, or the scorer (or the sample) is wrong."""

from pathlib import Path

import pytest

from dllm_eval.experiment import SqlAgentWorkload, load_experiment
from dllm_eval.usecases.codegen.datasets import load_dataset_registry
from dllm_eval.usecases.sql_agent.compare import ex_bird, ex_match, has_top_level_order_by
from dllm_eval.usecases.sql_agent.datasets import DATASET, GOLD_EXCLUDED, load_questions
from dllm_eval.usecases.sql_agent.scoring import GOLD_TIMEOUT_S, execute
from dllm_eval.usecases.sql_agent.tools import ReadOnlyDB
from dllm_eval.usecases.sql_agent.workload import db_paths, select_questions
from tests.conftest import REPO

pytestmark = pytest.mark.bird


def test_release_has_498_unique_questions() -> None:
    qs = load_questions(
        load_dataset_registry(REPO / "configs/datasets.yaml").datasets[DATASET], REPO
    )
    assert len(qs) == 498
    assert {q.id for q in qs} >= GOLD_EXCLUDED


@pytest.mark.parametrize("config", ["r4_uc2_main", "r1_pilot_uc2"])
def test_sampled_gold_queries_score_against_themselves(config: str) -> None:
    exp = load_experiment(REPO / f"configs/experiments/{config}.yaml")
    assert isinstance(exp.workload, SqlAgentWorkload)
    qs = select_questions(exp.workload, root=REPO)
    paths = db_paths(qs, root=REPO)
    for q in qs:
        db = ReadOnlyDB(Path(paths[q.id]))
        a = execute(db, q.gold_sql, GOLD_TIMEOUT_S)
        b = execute(db, q.gold_sql, GOLD_TIMEOUT_S)
        assert a.status == "ok" and a.rows, q.id
        assert ex_match(a.rows, b.rows, ordered=has_top_level_order_by(q.gold_sql)), q.id
        assert ex_bird(a.rows, b.rows), q.id
