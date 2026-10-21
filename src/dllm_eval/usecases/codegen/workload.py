"""Turn a codegen workload into runner items, and map run items back to problems."""

from __future__ import annotations

import random
from pathlib import Path

from dllm_eval.experiment import CodegenWorkload, PromptItem
from dllm_eval.usecases.codegen import prompts
from dllm_eval.usecases.codegen.datasets import (
    DEFAULT_REGISTRY,
    CodeProblem,
    load_dataset_registry,
    load_problems,
)


def select_problems(
    workload: CodegenWorkload,
    seed: int,
    *,
    root: Path = Path("."),
    registry_path: Path = DEFAULT_REGISTRY,
) -> list[CodeProblem]:
    if workload.prompt_version != prompts.PROMPT_VERSION:
        raise ValueError(
            f"workload prompt_version {workload.prompt_version!r} != frozen "
            f"{prompts.PROMPT_VERSION!r}; prompts are versioned, never edited in place"
        )
    registry = load_dataset_registry(root / registry_path)
    chosen: list[CodeProblem] = []
    for src in workload.sources:
        problems = sorted(load_problems(src.dataset, registry, root), key=lambda p: p.id)
        if src.ids is not None:
            by_native = {p.id.split("/", 1)[1]: p for p in problems}
            missing = [i for i in src.ids if i not in by_native]
            if missing:
                raise ValueError(f"{src.dataset}: unknown ids {missing}")
            chosen += [by_native[i] for i in src.ids]
        elif src.sample is not None:
            if src.sample > len(problems):
                raise ValueError(f"{src.dataset}: sample {src.sample} > {len(problems)} problems")
            chosen += random.Random(f"{seed}:{src.dataset}").sample(problems, src.sample)
        else:
            chosen += problems
    ids = [p.id for p in chosen]
    if len(ids) != len(set(ids)):
        raise ValueError("a problem was selected twice")
    return chosen


def build_items(problems: list[CodeProblem]) -> list[PromptItem]:
    return [PromptItem(id=p.id, messages=prompts.messages(p)) for p in problems]
