"""Experiment configs (F3.1): which arms run on which items, how often, under what budget."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dllm_eval.config import ModelRegistry
from dllm_eval.schemas import Message


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Arm(_Strict):
    """One model at one fixed setting. Arms never change mid-study (CLAUDE.md rule 6)."""

    name: str
    model: str  # key into models.yaml
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_completion_tokens: int = Field(gt=0)
    provider_extras: dict[str, Any] = Field(default_factory=dict)
    # Typical output tokens, used only for dry-run cost estimates (default: worst case).
    expected_output_tokens: int | None = Field(default=None, gt=0)

    @property
    def id(self) -> str:
        return f"{self.model}:{self.name}"


class PromptItem(_Strict):
    id: str
    messages: list[Message]


class PromptWorkload(_Strict):
    """Plain prompt list (smoke tests, the UC1 length sweep)."""

    kind: Literal["prompts"] = "prompts"
    items: list[PromptItem]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("workload item ids must be unique")
        return self


class CodegenSource(_Strict):
    dataset: Literal["humaneval+", "mbpp+", "lcb-v6"]
    sample: int | None = Field(default=None, gt=0)  # seeded random subset (experiment seed)
    ids: list[str] | None = None  # explicit native ids, e.g. ["HumanEval/0"]

    @model_validator(mode="after")
    def _one_selector(self) -> Self:
        if self.sample is not None and self.ids is not None:
            raise ValueError("use either sample or ids, not both")
        return self


class CodegenWorkload(_Strict):
    """UC1 problems from configs/datasets.yaml, formatted by usecases/codegen/prompts.py."""

    kind: Literal["codegen"]
    sources: list[CodegenSource]
    prompt_version: str  # must equal prompts.PROMPT_VERSION (prompts are frozen)


class SqlAgentWorkload(_Strict):
    """UC2 BIRD questions run through the agent loop (usecases/sql_agent). Each job is a
    whole task (up to max_turns model calls), logged as one record with its full trace."""

    kind: Literal["sql_agent"]
    prompt_version: str  # must equal sql_agent.prompts.PROMPT_VERSION
    ids_file: Path  # committed question list (`dllm-eval sample-bird`), one id per line
    answer_mode: Literal["tool", "response_format"] = "tool"  # response_format = F5.6
    max_turns: int = Field(default=8, gt=0)
    sql_timeout_s: float = Field(default=5.0, gt=0)  # run_sql statement timeout
    row_cap: int = Field(default=50, gt=0)  # rows shown per run_sql result
    # Dry-run estimate only: turns per task, and history growth per turn in tokens.
    expected_turns: float = Field(default=5.0, gt=0)
    expected_turn_growth_tokens: int = Field(default=600, ge=0)


class Experiment(_Strict):
    name: str
    description: str = ""
    seed: int = 0
    repeats: int = Field(default=1, gt=0)
    budget_usd: float = Field(gt=0)
    max_concurrency_per_provider: int = Field(default=4, gt=0)
    fail_on_warning: bool = True  # PRD R2: any `warning` means an arm is misconfigured
    warmup_calls_per_arm: int = Field(default=0, ge=0)  # marked, then excluded from latency stats
    record_raw: bool = False  # keep every raw chunk/event in calls.jsonl (large)
    # Network baseline (F2.5, PRD R8): probe each provider before the first job, and
    # optionally every N seconds while jobs run. Probes are free (GET .../models).
    network_probe: bool = True
    network_probe_samples: int = Field(default=3, gt=0)
    network_probe_interval_s: float | None = Field(default=None, gt=0)
    stream: bool = True
    arms: list[Arm]
    workload: PromptWorkload | CodegenWorkload | SqlAgentWorkload

    @model_validator(mode="after")
    def _unique_arms(self) -> Self:
        ids = [a.id for a in self.arms]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate arm ids: {ids}")
        return self

    def check_against(self, registry: ModelRegistry) -> None:
        """Arms must name enabled models and use only settings the model supports."""
        for arm in self.arms:
            model = registry.models.get(arm.model)
            if model is None or not model.enabled:
                raise ValueError(f"arm {arm.id}: model {arm.model!r} is unknown or disabled")
            if (
                arm.reasoning_effort is not None
                and arm.reasoning_effort not in model.reasoning_efforts
            ):
                raise ValueError(
                    f"arm {arm.id}: reasoning_effort {arm.reasoning_effort!r} not in "
                    f"{model.reasoning_efforts}"
                )
            if arm.temperature is not None and model.temperature_range is not None:
                lo, hi = model.temperature_range
                if not lo <= arm.temperature <= hi:
                    raise ValueError(
                        f"arm {arm.id}: temperature {arm.temperature} outside [{lo}, {hi}]"
                    )
            allowed = model.supports.tools_reasoning_efforts
            if (
                isinstance(self.workload, SqlAgentWorkload)
                and allowed is not None
                and arm.reasoning_effort not in allowed
            ):
                raise ValueError(
                    f"arm {arm.id}: {arm.model} supports tools only at reasoning_effort {allowed}"
                )
            if (
                isinstance(self.workload, SqlAgentWorkload)
                and self.workload.answer_mode == "response_format"
                and not model.supports.tools_with_structured_outputs
            ):
                raise ValueError(
                    f"arm {arm.id}: {arm.model} can't combine tools with response_format "
                    "(PRD OQ5), so it can't run the strict-schema sub-run"
                )
            if model.max_output_tokens and arm.max_completion_tokens > model.max_output_tokens:
                raise ValueError(
                    f"arm {arm.id}: max_completion_tokens {arm.max_completion_tokens} > "
                    f"model limit {model.max_output_tokens}"
                )


def load_experiment(path: Path) -> Experiment:
    with path.open() as f:
        return Experiment.model_validate(yaml.safe_load(f))
