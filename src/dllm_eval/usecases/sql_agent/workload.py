"""Turn a sql_agent workload into runner items, and map run items back to questions."""

from __future__ import annotations

from pathlib import Path

from dllm_eval.experiment import PromptItem, SqlAgentWorkload
from dllm_eval.usecases.codegen.datasets import DEFAULT_REGISTRY, load_dataset_registry
from dllm_eval.usecases.sql_agent import prompts
from dllm_eval.usecases.sql_agent.datasets import (
    DATASET,
    GOLD_EXCLUDED,
    BirdQuestion,
    db_path,
    load_questions,
)


def read_ids(path: Path) -> list[str]:
    ids = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate ids")
    return ids


def write_ids(path: Path, questions: list[BirdQuestion], header: str) -> None:
    lines = [f"# {h}" for h in header.splitlines()] + [q.id for q in questions]
    path.write_text("\n".join(lines) + "\n")


def select_questions(
    wl: SqlAgentWorkload, *, root: Path = Path("."), registry_path: Path = DEFAULT_REGISTRY
) -> list[BirdQuestion]:
    if wl.prompt_version != prompts.PROMPT_VERSION:
        raise ValueError(
            f"workload prompt_version {wl.prompt_version!r} != frozen "
            f"{prompts.PROMPT_VERSION!r}; prompts are versioned, never edited in place"
        )
    spec = load_dataset_registry(root / registry_path).datasets[DATASET]
    by_id = {q.id: q for q in load_questions(spec, root)}
    ids = read_ids(root / wl.ids_file)
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError(f"{wl.ids_file}: unknown ids {missing}")
    bad = sorted(set(ids) & GOLD_EXCLUDED)
    if bad:
        raise ValueError(f"{wl.ids_file}: {bad} have gold SQL that can't be scored")
    return [by_id[i] for i in ids]


def build_items(questions: list[BirdQuestion], wl: SqlAgentWorkload) -> list[PromptItem]:
    return [
        PromptItem(id=q.id, messages=prompts.messages(q, wl.answer_mode, wl.max_turns))
        for q in questions
    ]


def db_paths(
    questions: list[BirdQuestion],
    *,
    root: Path = Path("."),
    registry_path: Path = DEFAULT_REGISTRY,
) -> dict[str, Path]:
    """item id -> its database file."""
    spec = load_dataset_registry(root / registry_path).datasets[DATASET]
    return {q.id: db_path(spec, q.db_id, root) for q in questions}
