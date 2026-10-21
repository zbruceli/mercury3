"""Experiment runner (F3.2-F3.5): job matrix, interleaving, concurrency, resume, budget guard."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dllm_eval.config import ModelRegistry
from dllm_eval.cost import call_cost, estimate_tokens, tokens_cost
from dllm_eval.experiment import Arm, Experiment, PromptItem, PromptWorkload, SqlAgentWorkload
from dllm_eval.logging_store import RunStore
from dllm_eval.providers import BaseProvider, ErrorKind, ProviderError, RateLimiter, build_provider
from dllm_eval.schemas import ChatRequest

ProviderFactory = Callable[..., BaseProvider]
# Returns probe records (ProviderProbe dumps) for the given label; see probe.probe_all.
Prober = Callable[[str], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class Job:
    job_id: str
    arm: Arm
    item: PromptItem
    repeat: int
    warmup: bool = False


def resolve_items(exp: Experiment, root: Path = Path(".")) -> list[PromptItem]:
    """The prompts a run sends: inline prompt items, or UC1 problems formatted by the frozen
    prompt template (which loads and checksum-verifies the datasets)."""
    if isinstance(exp.workload, PromptWorkload):
        return list(exp.workload.items)
    if isinstance(exp.workload, SqlAgentWorkload):
        from dllm_eval.usecases.sql_agent import workload as uc2

        return uc2.build_items(uc2.select_questions(exp.workload, root=root), exp.workload)
    from dllm_eval.usecases.codegen.workload import build_items, select_problems

    return build_items(select_problems(exp.workload, exp.seed, root=root))


def expand_jobs(exp: Experiment, items: list[PromptItem] | None = None) -> list[Job]:
    """Every (arm, item, repeat), shuffled with the experiment seed so models interleave
    (never all of model A, then all of B; CLAUDE.md measurement practices)."""
    jobs = [
        Job(f"{arm.id}/{item.id}/{r}", arm, item, r)
        for arm in exp.arms
        for item in (items if items is not None else resolve_items(exp))
        for r in range(exp.repeats)
    ]
    random.Random(exp.seed).shuffle(jobs)
    if exp.warmup_calls_per_arm:
        seen: dict[str, int] = {}
        marked = []
        for j in jobs:
            n = seen.get(j.arm.id, 0)
            seen[j.arm.id] = n + 1
            marked.append(
                Job(j.job_id, j.arm, j.item, j.repeat, warmup=True)
                if n < exp.warmup_calls_per_arm
                else j
            )
        jobs = marked
    return jobs


def build_request(job: Job, exp: Experiment) -> ChatRequest:
    if isinstance(exp.workload, SqlAgentWorkload):  # the first turn's request
        from dllm_eval.usecases.sql_agent.agent import build_request as agent_request

        return agent_request(job.arm, exp.workload, list(job.item.messages), stream=exp.stream)
    a = job.arm
    return ChatRequest(
        model_key=a.model,
        messages=list(job.item.messages),
        max_completion_tokens=a.max_completion_tokens,
        temperature=a.temperature,
        reasoning_effort=a.reasoning_effort,
        stream=exp.stream,
        provider_extras=dict(a.provider_extras),
    )


def _task_input(first_turn: int, turns: float, growth: int) -> int:
    """Input tokens of one agent task: every turn resends the history, which grows by
    `growth` tokens per turn."""
    k = max(1, round(turns))
    return sum(first_turn + t * growth for t in range(k))


@dataclass
class DryRunRow:
    arm_id: str
    jobs: int
    input_tokens: int
    output_tokens_expected: int
    output_tokens_worst: int
    cost_expected: float
    cost_worst: float


def dry_run(
    exp: Experiment, registry: ModelRegistry, items: list[PromptItem] | None = None
) -> list[DryRunRow]:
    """Job counts and token/cost estimates per arm (F3.5). No API calls."""
    exp.check_against(registry)
    items = items if items is not None else resolve_items(exp)
    wl = exp.workload
    rows = []
    for arm in exp.arms:
        model = registry.models[arm.model]
        per_item_in = [
            sum(estimate_tokens(m.content or "") for m in item.messages) for item in items
        ]
        n = len(per_item_in) * exp.repeats
        inp = sum(per_item_in) * exp.repeats
        worst = arm.max_completion_tokens * n
        expected = (arm.expected_output_tokens or arm.max_completion_tokens) * n
        if isinstance(wl, SqlAgentWorkload):
            # A task resends its growing history every turn. expected_output_tokens is per
            # turn; the worst case is max_turns turns that each use the whole budget.
            # Prompt caching is ignored, so input cost is an upper bound.
            first = inp // max(n, 1)
            inp_expected = _task_input(first, wl.expected_turns, wl.expected_turn_growth_tokens) * n
            expected = round(
                (arm.expected_output_tokens or arm.max_completion_tokens) * wl.expected_turns * n
            )
            worst = arm.max_completion_tokens * wl.max_turns * n
            worst_growth = wl.expected_turn_growth_tokens + arm.max_completion_tokens
            inp_worst = _task_input(first, wl.max_turns, worst_growth) * n
            rows.append(
                DryRunRow(
                    arm_id=arm.id,
                    jobs=n,
                    input_tokens=inp_expected,
                    output_tokens_expected=expected,
                    output_tokens_worst=worst,
                    cost_expected=tokens_cost(model, inp_expected, 0, expected),
                    cost_worst=tokens_cost(model, inp_worst, 0, worst),
                )
            )
            continue
        rows.append(
            DryRunRow(
                arm_id=arm.id,
                jobs=n,
                input_tokens=inp,
                output_tokens_expected=expected,
                output_tokens_worst=worst,
                cost_expected=tokens_cost(model, inp, 0, expected),
                cost_worst=tokens_cost(model, inp, 0, worst),
            )
        )
    return rows


@dataclass
class RunSummary:
    run_id: str
    planned: int
    skipped_done: int
    ok: int = 0
    errors: int = 0
    not_started: int = 0
    spent_usd: float = 0.0
    aborted: str | None = None
    error_samples: list[dict[str, Any]] = field(default_factory=list)


async def run_experiment(
    exp: Experiment,
    registry: ModelRegistry,
    store: RunStore,
    run_id: str,
    *,
    provider_factory: ProviderFactory = build_provider,
    prober: Prober | None = None,
    items: list[PromptItem] | None = None,
    task_dbs: dict[str, Path] | None = None,
    log: Callable[[str], None] = print,
) -> RunSummary:
    """Run every job not yet done. For sql_agent workloads `task_dbs` maps item id to its
    database (default: resolved from the dataset registry)."""
    exp.check_against(registry)
    done = store.completed_job_ids()
    all_jobs = expand_jobs(exp, items)
    jobs = [j for j in all_jobs if j.job_id not in done]
    summary = RunSummary(
        run_id=run_id, planned=len(all_jobs), skipped_done=len(all_jobs) - len(jobs)
    )
    # Resumed runs count what was already spent against the budget.
    summary.spent_usd = sum(
        r.get("cost_usd") or 0.0 for r in store.latest_by_job().values() if r.get("status") == "ok"
    )

    limiters: dict[str, RateLimiter] = {}
    providers: dict[str, BaseProvider] = {}
    for model_key in sorted({a.model for a in exp.arms}):
        providers[model_key] = provider_factory(
            model_key, registry, limiters=limiters, record_raw=exp.record_raw
        )
    sems = {
        pk: asyncio.Semaphore(exp.max_concurrency_per_provider)
        for pk in {registry.models[a.model].provider for a in exp.arms}
    }
    abort = asyncio.Event()
    agent_dbs: dict[str, Any] = {}
    if isinstance(exp.workload, SqlAgentWorkload):
        from dllm_eval.usecases.sql_agent import workload as uc2
        from dllm_eval.usecases.sql_agent.tools import ReadOnlyDB

        if task_dbs is None:
            task_dbs = uc2.db_paths(uc2.select_questions(exp.workload))
        agent_dbs = {item_id: ReadOnlyDB(p) for item_id, p in task_dbs.items()}

    def stop(reason: str) -> None:
        if not abort.is_set():
            summary.aborted = reason
            abort.set()
            log(f"ABORT: {reason}")

    async def work(job: Job) -> None:
        model = registry.models[job.arm.model]
        async with sems[model.provider]:
            if abort.is_set():
                summary.not_started += 1
                return
            if summary.spent_usd >= exp.budget_usd:
                summary.not_started += 1
                stop(f"budget_usd {exp.budget_usd} reached (spent {summary.spent_usd:.4f})")
                return
            req = build_request(job, exp)
            base: dict[str, Any] = {
                "job_id": job.job_id,
                "arm": job.arm.id,
                "model": job.arm.model,
                "item_id": job.item.id,
                "repeat": job.repeat,
                "warmup": job.warmup,
                "started_at": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
                "request": req.model_dump(mode="json", by_alias=True),
            }
            if isinstance(exp.workload, SqlAgentWorkload):
                await agent_job(job, exp.workload, base)
                return
            t0 = time.perf_counter()
            try:
                result = await providers[job.arm.model].chat(req)
            except ProviderError as e:
                summary.errors += 1
                rec = {
                    **base,
                    "status": "error",
                    "error": e.to_record(),
                    "wall_s": time.perf_counter() - t0,
                }
                store.append_call(rec)
                if len(summary.error_samples) < 5:
                    summary.error_samples.append(rec["error"])
                if e.kind is ErrorKind.RUN_FATAL:
                    stop(f"run-fatal error on {job.arm.id}: {e}")
                return
            except ValueError as e:  # request validation: a config bug, not a model failure
                summary.errors += 1
                store.append_call({**base, "status": "config_error", "error": {"message": str(e)}})
                stop(f"config error on {job.arm.id}: {e}")
                return
            except Exception as e:  # harness bug: stop rather than burn budget on it
                summary.errors += 1
                store.append_call(
                    {
                        **base,
                        "status": "internal_error",
                        "error": {"message": f"{type(e).__name__}: {e}"},
                    }
                )
                stop(f"internal error on {job.arm.id}: {type(e).__name__}: {e}")
                return

            cost = call_cost(result.usage, model)
            summary.spent_usd += cost or 0.0
            status = "ok"
            if result.warnings and exp.fail_on_warning:
                status = "warning"
                stop(f"provider warning on {job.arm.id}: {result.warnings}")
            else:
                summary.ok += 1
            dumped = result.model_dump(
                mode="json", exclude={"raw_response"} if not exp.record_raw else set()
            )
            store.append_call(
                {
                    **base,
                    "status": status,
                    "result": dumped,
                    "wall_s": time.perf_counter() - t0,
                    "cost_usd": cost,
                    "cost_usd_regular": call_cost(result.usage, model, regular=True),
                }
            )

    async def agent_job(job: Job, wl: SqlAgentWorkload, base: dict[str, Any]) -> None:
        """One UC2 task: the whole agent loop, logged as a single record with its trace."""
        from dllm_eval.usecases.sql_agent.agent import run_task

        model = registry.models[job.arm.model]
        t0 = time.perf_counter()
        try:
            task = await run_task(
                providers[job.arm.model], job.arm, job.item, agent_dbs[job.item.id], wl,
                stream=exp.stream,
            )  # fmt: skip
        except ValueError as e:  # request validation: a config bug, not a model failure
            summary.errors += 1
            store.append_call({**base, "status": "config_error", "error": {"message": str(e)}})
            stop(f"config error on {job.arm.id}: {e}")
            return
        except Exception as e:  # harness bug: stop rather than burn budget on it
            summary.errors += 1
            store.append_call(
                {
                    **base,
                    "status": "internal_error",
                    "error": {"message": f"{type(e).__name__}: {e}"},
                }
            )
            stop(f"internal error on {job.arm.id}: {type(e).__name__}: {e}")
            return

        costs = [call_cost(t.result.usage, model) for t in task.turns]
        regular = [call_cost(t.result.usage, model, regular=True) for t in task.turns]
        cost = sum(c or 0.0 for c in costs)
        summary.spent_usd += cost
        warnings = [w for t in task.turns for w in t.result.warnings]
        status = "ok"
        if task.error is not None:
            status = "error"
            summary.errors += 1
            if len(summary.error_samples) < 5:
                summary.error_samples.append(task.error.to_record())
            if task.error.kind is ErrorKind.RUN_FATAL:
                stop(f"run-fatal error on {job.arm.id}: {task.error}")
        elif warnings and exp.fail_on_warning:
            status = "warning"
            stop(f"provider warning on {job.arm.id}: {warnings}")
        else:
            summary.ok += 1
        store.append_call(
            {
                **base,
                "status": status,
                "task": task.to_record(record_raw=exp.record_raw),
                "error": task.error.to_record() if task.error else None,
                "wall_s": time.perf_counter() - t0,
                "cost_usd": None if any(c is None for c in costs) else cost,
                "cost_usd_regular": (
                    None if any(c is None for c in regular) else sum(c or 0.0 for c in regular)
                ),
            }
        )

    async def reprobe(interval: float) -> None:
        assert prober is not None
        while True:
            await asyncio.sleep(interval)
            try:
                for rec in await prober("periodic"):
                    store.append_probe(rec)
            except Exception as e:  # a failed probe must never stop the run
                log(f"network probe failed: {type(e).__name__}: {e}")

    probe_task = None
    if prober is not None and exp.network_probe and exp.network_probe_interval_s:
        probe_task = asyncio.create_task(reprobe(exp.network_probe_interval_s))
    try:
        await asyncio.gather(*(work(j) for j in jobs))
    finally:
        if probe_task is not None:
            probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe_task
    return summary
