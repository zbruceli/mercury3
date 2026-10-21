"""dllm-eval command line: validate-config, run (dry-run / --live), analyze (offline)."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from dllm_eval.config import DEFAULT_MODELS_PATH, ProviderKind, load_registry
from dllm_eval.experiment import CodegenWorkload, Experiment, SqlAgentWorkload, load_experiment
from dllm_eval.logging_store import RunStore, build_manifest, git_state, secret_values
from dllm_eval.probe import probe_all
from dllm_eval.runner import dry_run, resolve_items, run_experiment

app = typer.Typer(no_args_is_help=True, add_completion=False)

# TEST_PLAN B1: model ids and prices must be re-verified within 7 days of a real run.
MAX_VERIFY_AGE_DAYS = 7
DEFAULT_RUNS_DIR = Path("results/runs")
DEFAULT_ANALYSIS_DIR = Path("results/analysis")
REPO_ROOT = Path(__file__).resolve().parents[2]


@app.callback()
def main() -> None:
    """Benchmark harness for Mercury 3 (diffusion) vs. autoregressive LLMs."""


@app.command("validate-config")
def validate_config(
    path: Annotated[Path, typer.Option(help="Path to models.yaml")] = DEFAULT_MODELS_PATH,
    strict: Annotated[
        bool, typer.Option(help="Fail (exit 1) if enabled entries are stale")
    ] = False,
) -> None:
    """Validate configs/models.yaml and report stale or disabled entries."""
    try:
        registry = load_registry(path)
    except (ValidationError, OSError) as e:
        typer.echo(f"invalid {path}:\n{e}", err=True)
        raise typer.Exit(1) from e

    enabled = registry.enabled_models()
    disabled = sorted(set(registry.models) - set(enabled))
    typer.echo(f"{path}: OK ({len(enabled)} enabled, {len(disabled)} disabled)")
    if disabled:
        typer.echo(f"  disabled (pending M0 verification): {', '.join(disabled)}")

    stale = registry.stale_entries(dt.date.today(), MAX_VERIFY_AGE_DAYS)
    if stale:
        typer.echo(f"  stale (> {MAX_VERIFY_AGE_DAYS} days since verified_on): {', '.join(stale)}")
        if strict:
            raise typer.Exit(1)


def workload_provenance(exp: Experiment) -> dict[str, Any]:
    """For UC1/UC2 runs: the exact dataset bytes and prompt version (PRD R10/R11)."""
    if isinstance(exp.workload, SqlAgentWorkload):
        import sqlite3

        from dllm_eval.usecases.codegen.datasets import load_dataset_registry as load_reg
        from dllm_eval.usecases.sql_agent import prompts as uc2_prompts
        from dllm_eval.usecases.sql_agent.datasets import DATASET, GOLD_EXCLUDED

        return {
            "prompt_version": uc2_prompts.PROMPT_VERSION,
            "datasets": {DATASET: load_reg().datasets[DATASET].model_dump(mode="json")},
            "sqlite_version": sqlite3.sqlite_version,
            "gold_excluded": sorted(GOLD_EXCLUDED),
        }
    if not isinstance(exp.workload, CodegenWorkload):
        return {}
    from dllm_eval.usecases.codegen import prompts
    from dllm_eval.usecases.codegen.datasets import load_dataset_registry

    reg = load_dataset_registry()
    used = sorted({s.dataset for s in exp.workload.sources})
    return {
        "prompt_version": prompts.PROMPT_VERSION,
        "datasets": {n: reg.datasets[n].model_dump(mode="json") for n in used},
    }


@app.command("fetch-data")
def fetch_data(
    datasets: Annotated[Path, typer.Option(help="Dataset registry")] = Path(
        "configs/datasets.yaml"
    ),
) -> None:
    """Download any missing dataset and verify every file against its pinned sha256.
    BIRD Mini-Dev is then extracted (questions + SQLite files only) to data/bird-minidev/."""
    from dllm_eval.usecases.codegen.datasets import fetch, load_dataset_registry
    from dllm_eval.usecases.sql_agent.datasets import DATASET, extract

    reg = load_dataset_registry(datasets)
    for name, spec in reg.datasets.items():
        try:
            path = fetch(spec)
            if name == DATASET:
                extract(spec)
        except (OSError, ValueError) as e:
            typer.echo(f"{name}: FAILED: {e}", err=True)
            raise typer.Exit(1) from e
        typer.echo(f"{name} {spec.version}: OK ({path}, {spec.bytes} bytes, sha256 verified)")


@app.command("sample-bird")
def sample_bird(
    n: Annotated[int, typer.Option(help="Questions to draw")],
    seed: Annotated[int, typer.Option(help="Sampler seed")],
    out: Annotated[Path, typer.Option(help="Id list to write (commit it)")],
    within: Annotated[
        Path | None, typer.Option(help="Draw from this id list instead of all questions")
    ] = None,
) -> None:
    """Write a difficulty-stratified BIRD Mini-Dev id list (F5.1). Deterministic."""
    from collections import Counter

    from dllm_eval.usecases.codegen.datasets import load_dataset_registry
    from dllm_eval.usecases.sql_agent.datasets import DATASET, load_questions, stratified_sample
    from dllm_eval.usecases.sql_agent.workload import read_ids, write_ids

    questions = load_questions(load_dataset_registry().datasets[DATASET])
    if within is not None:
        keep = set(read_ids(within))
        questions = [q for q in questions if q.id in keep]
    chosen = stratified_sample(questions, n, seed)
    mix = Counter(q.difficulty for q in chosen)
    header = (
        f"BIRD Mini-Dev UC2 sample: {n} questions, seed {seed}, stratified by difficulty\n"
        f"({', '.join(f'{k} {v}' for k, v in sorted(mix.items()))})"
        + (f", drawn from {within}" if within else "")
        + "\nRegenerate: uv run dllm-eval sample-bird "
        + f"--n {n} --seed {seed} --out {out}"
        + (f" --within {within}" if within else "")
    )
    write_ids(out, chosen, header)
    typer.echo(f"wrote {out}: {n} ids ({dict(mix)})")


@app.command()
def score(
    run_id: Annotated[str, typer.Argument(help="UC1 run id to grade")],
    runs_dir: Annotated[Path, typer.Option()] = DEFAULT_RUNS_DIR,
    out_dir: Annotated[Path, typer.Option()] = DEFAULT_ANALYSIS_DIR,
    sandbox: Annotated[Path, typer.Option(help="Sandbox config")] = Path("configs/sandbox.yaml"),
) -> None:
    """Grade a UC1 run's code in the Docker sandbox and print pass@k. Never calls a model API."""
    from dllm_eval.sandbox.executor import DockerExecutor, docker_available, load_sandbox_config
    from dllm_eval.usecases.codegen.scoring import score_run, summarize
    from dllm_eval.usecases.codegen.scoring import to_markdown as scores_markdown
    from dllm_eval.usecases.codegen.workload import select_problems

    store = RunStore(runs_dir / run_id)
    if not store.exists():
        typer.echo(f"no run {run_id} under {runs_dir}", err=True)
        raise typer.Exit(1)
    exp = Experiment.model_validate(store.read_manifest()["experiment"])
    if isinstance(exp.workload, SqlAgentWorkload):
        _score_uc2(store, exp, exp.workload, out_dir / run_id)
        return
    if not isinstance(exp.workload, CodegenWorkload):
        typer.echo("not a UC1 (codegen) or UC2 (sql_agent) run", err=True)
        raise typer.Exit(1)
    try:
        cfg = load_sandbox_config(sandbox)
    except (OSError, ValueError, ValidationError) as e:
        typer.echo(f"sandbox not ready: {e}", err=True)
        raise typer.Exit(1) from e
    if not docker_available():
        typer.echo("Docker daemon is not running; start Docker Desktop", err=True)
        raise typer.Exit(1)
    problems = {p.id: p for p in select_problems(exp.workload, exp.seed)}
    out = out_dir / run_id / "scores.jsonl"
    rows = score_run(store, problems, DockerExecutor(cfg), out, log=typer.echo)
    api_errors: dict[str, int] = {}
    finish: dict[str, str | None] = {}
    for rec in store.latest_by_job().values():
        if rec["status"] != "ok":
            api_errors[rec["arm"]] = api_errors.get(rec["arm"], 0) + 1
        else:
            finish[rec["job_id"]] = rec["result"].get("finish_reason")
    typer.echo(scores_markdown(summarize(rows, api_errors, finish)))


def _score_uc2(store: RunStore, exp: Experiment, wl: SqlAgentWorkload, dest: Path) -> None:
    from dllm_eval.analysis import uc2
    from dllm_eval.usecases.sql_agent.scoring import score_run
    from dllm_eval.usecases.sql_agent.workload import db_paths, select_questions

    questions = select_questions(wl)
    rows = score_run(
        store, {q.id: q for q in questions}, db_paths(questions), dest / "uc2_scores.jsonl",
        log=typer.echo,
    )  # fmt: skip
    tasks, turns = uc2.task_rows(store)
    table = uc2.arm_table(pd_frame(tasks), pd_frame(turns), rows, seed=exp.seed)
    typer.echo("\n" + uc2.accuracy_markdown(table))
    typer.echo("\nReliability:\n" + uc2.reliability_markdown(table))
    (dest / "uc2_arm_table.json").write_text(json.dumps(table, indent=2, default=str))
    typer.echo(f"\nwrote {dest}/uc2_scores.jsonl and uc2_arm_table.json")


def pd_frame(rows: list[dict[str, Any]]) -> Any:
    import pandas as pd

    return pd.DataFrame(rows)


@app.command()
def trace(
    run_id: Annotated[str, typer.Argument(help="UC2 run id")],
    job_id: Annotated[str | None, typer.Argument(help="Job id (default: list jobs)")] = None,
    runs_dir: Annotated[Path, typer.Option()] = DEFAULT_RUNS_DIR,
    verify: Annotated[
        bool, typer.Option(help="Re-run the recorded tool calls and check the results match")
    ] = False,
    all_jobs: Annotated[
        bool, typer.Option("--all", help="With --verify: check every task")
    ] = False,
) -> None:
    """Pretty-print a UC2 task trace (F5.7), or check that traces replay. Offline only."""
    from dllm_eval.usecases.sql_agent.tools import ReadOnlyDB
    from dllm_eval.usecases.sql_agent.trace import format_trace, replay
    from dllm_eval.usecases.sql_agent.workload import db_paths, select_questions

    store = RunStore(runs_dir / run_id)
    if not store.exists():
        typer.echo(f"no run {run_id} under {runs_dir}", err=True)
        raise typer.Exit(1)
    exp = Experiment.model_validate(store.read_manifest()["experiment"])
    if not isinstance(exp.workload, SqlAgentWorkload):
        typer.echo("not a UC2 (sql_agent) run", err=True)
        raise typer.Exit(1)
    recs = store.latest_by_job()
    if verify:
        paths = db_paths(select_questions(exp.workload))
        dbs = {i: ReadOnlyDB(p) for i, p in paths.items()}
        targets = list(recs.values()) if all_jobs else [recs[job_id]] if job_id else []
        if not targets:
            typer.echo("give a job id or --all", err=True)
            raise typer.Exit(1)
        bad = 0
        for rec in targets:
            problems = replay(rec, exp.workload, dbs[rec["item_id"]])
            if problems:
                bad += 1
                typer.echo(f"{rec['job_id']}: " + "; ".join(problems))
        typer.echo(f"replayed {len(targets)} traces: {len(targets) - bad} match, {bad} differ")
        if bad:
            raise typer.Exit(1)
        return
    if job_id is None:
        for j, rec in sorted(recs.items()):
            typer.echo(f"{j}\t{rec['status']}\t{(rec.get('task') or {}).get('outcome')}")
        return
    if job_id not in recs:
        typer.echo(f"no job {job_id!r} in {run_id}", err=True)
        raise typer.Exit(1)
    typer.echo(format_trace(recs[job_id]))


@app.command()
def run(
    config: Annotated[Path, typer.Argument(help="Experiment YAML")],
    dry_run_only: Annotated[
        bool, typer.Option("--dry-run", help="Estimate only, no calls")
    ] = False,
    live: Annotated[
        bool, typer.Option("--live", help="Actually call APIs (spends tokens)")
    ] = False,
    models: Annotated[Path, typer.Option(help="Path to models.yaml")] = DEFAULT_MODELS_PATH,
    runs_dir: Annotated[Path, typer.Option(help="Where run directories go")] = DEFAULT_RUNS_DIR,
    resume: Annotated[str | None, typer.Option(help="Resume this run id")] = None,
    allow_dirty: Annotated[
        bool,
        typer.Option(help="Run --live with uncommitted changes (recorded in the manifest)"),
    ] = False,
) -> None:
    """Run an experiment. Always prints the dry-run estimate; spends tokens only with --live."""
    try:
        registry = load_registry(models)
        exp = load_experiment(config)
        items = resolve_items(exp)
        rows = dry_run(exp, registry, items)
    except (ValidationError, OSError, ValueError) as e:
        typer.echo(f"invalid config: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(
        f"experiment {exp.name}: {sum(r.jobs for r in rows)} jobs, budget ${exp.budget_usd:.2f}"
    )
    typer.echo("| Arm | Jobs | Input tok | Output tok (exp / worst) | Cost $ (exp / worst) |")
    typer.echo("|---|---:|---:|---:|---:|")
    for r in rows:
        typer.echo(
            f"| `{r.arm_id}` | {r.jobs} | {r.input_tokens} | {r.output_tokens_expected} / "
            f"{r.output_tokens_worst} | {r.cost_expected:.4f} / {r.cost_worst:.4f} |"
        )
    worst = sum(r.cost_worst for r in rows)
    expected = sum(r.cost_expected for r in rows)
    typer.echo(f"total expected cost ${expected:.4f} (from expected_output_tokens)")
    typer.echo(f"total worst-case cost ${worst:.4f} (every call uses max_completion_tokens)")
    if dry_run_only:
        return
    if not live:
        typer.echo("not running: pass --live to call the APIs (spends real tokens)", err=True)
        raise typer.Exit(2)

    used = {registry.models[a.model].provider for a in exp.arms}
    missing = [
        registry.providers[p].api_key_env
        for p in sorted(used)
        if registry.providers[p].kind is not ProviderKind.FAKE
        and not os.environ.get(registry.providers[p].api_key_env)
    ]
    if missing:
        typer.echo(
            f"missing API keys: {', '.join(missing)} (use `uv run --env-file .env`)", err=True
        )
        raise typer.Exit(1)

    # TEST_PLAN B1: real runs need a clean tree so the manifest's git SHA is the code that ran.
    git = git_state(REPO_ROOT)
    if (git["sha"] is None or git["dirty"]) and not allow_dirty:
        reason = "not a git checkout" if git["sha"] is None else "uncommitted changes"
        typer.echo(
            f"refusing to run --live: {reason}. Commit or stash first, or pass --allow-dirty "
            "(the manifest records it).",
            err=True,
        )
        raise typer.Exit(1)
    git = {**git, "allow_dirty": allow_dirty}

    # F2.5 / PRD R8: network baseline before the first job (free GET .../models per provider).
    async def prober(label: str) -> list[dict[str, Any]]:
        found = await probe_all(registry, used, samples=exp.network_probe_samples, label=label)
        return [pr.model_dump(mode="json") for pr in found]

    start_probes: list[dict[str, Any]] = []
    if exp.network_probe:
        start_probes = asyncio.run(prober("resume" if resume else "start"))
        for pr in start_probes:
            m = pr["median"]
            rtt = "—" if m["connect_s"] is None else f"{m['connect_s'] * 1000:.0f} ms"
            ttfb = "—" if m["ttfb_s"] is None else f"{m['ttfb_s'] * 1000:.0f} ms"
            typer.echo(f"network probe {pr['provider']}: connect {rtt}, ttfb {ttfb}")

    exp_dump = exp.model_dump(mode="json", by_alias=True)
    run_id = resume or f"{exp.name}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
    store = RunStore(runs_dir / run_id, secrets=secret_values(registry))
    if resume:
        if not store.exists():
            typer.echo(f"no run {run_id} under {runs_dir}", err=True)
            raise typer.Exit(1)
        if store.read_manifest()["experiment"] != exp_dump:
            typer.echo(
                "experiment config changed since this run started; start a new run", err=True
            )
            raise typer.Exit(1)
    else:
        manifest = build_manifest(
            run_id,
            exp_dump,
            registry,
            [a.model for a in exp.arms],
            REPO_ROOT,
            git=git,
            network_probe=start_probes,
            extra=workload_provenance(exp),
        )
        store.create(manifest)
    for pr in start_probes:  # every probe, start/resume/periodic, also lands in probes.jsonl
        store.append_probe(pr)

    summary = asyncio.run(
        run_experiment(
            exp,
            registry,
            store,
            run_id,
            prober=prober if exp.network_probe else None,
            items=items,
            log=typer.echo,
        )
    )
    typer.echo(json.dumps(dataclasses.asdict(summary), indent=2))
    typer.echo(f"run dir: {store.dir}")
    if summary.aborted or summary.errors:
        raise typer.Exit(1)


@app.command()
def analyze(
    run_id: Annotated[str, typer.Argument(help="Run id (directory name under runs-dir)")],
    runs_dir: Annotated[Path, typer.Option()] = DEFAULT_RUNS_DIR,
    out_dir: Annotated[Path, typer.Option()] = DEFAULT_ANALYSIS_DIR,
) -> None:
    """Summarise a run from its logs only. Never calls an API."""
    from dllm_eval.analysis.load import load_calls
    from dllm_eval.analysis.tables import arm_summary, probe_markdown, to_markdown

    store = RunStore(runs_dir / run_id)
    if not store.exists():
        typer.echo(f"no run {run_id} under {runs_dir}", err=True)
        raise typer.Exit(1)
    exp_dict = store.read_manifest()["experiment"]
    if (exp_dict.get("workload") or {}).get("kind") == "sql_agent":
        _analyze_uc2(store, out_dir / run_id, exp_dict.get("seed", 0))
        return
    df = load_calls(store)
    rows = arm_summary(df, seed=exp_dict.get("seed", 0))
    typer.echo(to_markdown(rows))
    probes = list(store.iter_probes())
    if probes:
        typer.echo("\nNetwork baseline (median of samples, ms):\n" + probe_markdown(probes))
    dest = out_dir / run_id
    dest.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest / "calls.parquet", index=False)
    (dest / "arm_summary.json").write_text(json.dumps(rows, indent=2))
    typer.echo(f"\nwrote {dest}/calls.parquet and arm_summary.json")


def _analyze_uc2(store: RunStore, dest: Path, seed: int) -> None:
    """UC2: task and turn tables; accuracy columns appear once `score` has run."""
    from dllm_eval.analysis import uc2
    from dllm_eval.analysis.tables import probe_markdown

    tasks, turns = uc2.task_rows(store)
    scores_path = dest / "uc2_scores.jsonl"
    scores = (
        [json.loads(x) for x in scores_path.read_text().splitlines() if x.strip()]
        if scores_path.exists()
        else []
    )
    tdf, udf = pd_frame(tasks), pd_frame(turns)
    table = uc2.arm_table(tdf, udf, scores, seed=seed)
    if not scores:
        typer.echo("(not scored yet: run `dllm-eval score` for EX)")
    typer.echo(uc2.accuracy_markdown(table))
    typer.echo("\nReliability:\n" + uc2.reliability_markdown(table))
    probes = list(store.iter_probes())
    if probes:
        typer.echo("\nNetwork baseline (median of samples, ms):\n" + probe_markdown(probes))
    dest.mkdir(parents=True, exist_ok=True)
    tdf.to_parquet(dest / "tasks.parquet", index=False)
    udf.to_parquet(dest / "turns.parquet", index=False)
    typer.echo(f"\nwrote {dest}/tasks.parquet and turns.parquet")


@app.command()
def charts(
    run_id: Annotated[str, typer.Argument(help="Scored UC1 run id, or a UC2 run id")],
    runs_dir: Annotated[Path, typer.Option()] = DEFAULT_RUNS_DIR,
    out_dir: Annotated[Path, typer.Option()] = DEFAULT_ANALYSIS_DIR,
    reference: Annotated[str, typer.Option(help="UC2: arm the ratios are relative to")] = (
        "mercury-3:low"
    ),
) -> None:
    """UC1 Pareto chart, or the UC2 task-vs-call speed-up chart (F6.4). Offline only."""
    from dllm_eval.analysis.charts import pareto_points, plot_pareto, write_points_csv
    from dllm_eval.analysis.load import load_calls

    store = RunStore(runs_dir / run_id)
    if store.exists() and store.read_manifest()["experiment"]["workload"].get("kind") == (
        "sql_agent"
    ):
        _charts_uc2(store, out_dir / run_id, reference)
        return
    scores_path = out_dir / run_id / "scores.jsonl"
    if not store.exists() or not scores_path.exists():
        typer.echo(
            f"need {runs_dir / run_id} and {scores_path} (run `dllm-eval score` first)", err=True
        )
        raise typer.Exit(1)
    scores = [json.loads(line) for line in scores_path.read_text().splitlines() if line.strip()]
    manifest = store.read_manifest()
    points = pareto_points(load_calls(store), scores, seed=manifest["experiment"].get("seed", 0))
    n_problems = max((p.problems for p in points), default=0)
    date = manifest.get("created_at", "")[:10]
    png = plot_pareto(
        points,
        out_dir / run_id / "fig1_pareto_uc1.png",
        title="Speed vs. correctness on code generation",
        subtitle=(
            f"{n_problems} problems (HumanEval+, MBPP+, LiveCodeBench v6 pre-cutoff); one point "
            f"per model and reasoning setting; bars = 95% CI · run {run_id} · as of {date}"
        ),
    )
    csv = write_points_csv(points, out_dir / run_id / "fig1_pareto_uc1.csv")
    typer.echo(f"wrote {png} and {csv}")


def _charts_uc2(store: RunStore, dest: Path, reference: str) -> None:
    from dllm_eval.analysis import uc2

    manifest = store.read_manifest()
    tasks, turns = uc2.task_rows(store)
    tdf = pd_frame(tasks)
    points = uc2.speedup_points(
        tdf, pd_frame(turns), reference=reference, seed=manifest["experiment"].get("seed", 0)
    )
    n_q = tdf["item_id"].nunique()
    date = manifest.get("created_at", "")[:10]
    png = uc2.plot_speedup(
        points,
        dest / "fig_uc2_speedup.png",
        title="Does per-call speed carry through to whole agent tasks?",
        subtitle=(
            f"SQL agent on {n_q} BIRD Mini-Dev questions (max 8 turns). Median latency per model "
            f"call and per task, relative to {reference}; bars = 95% bootstrap CI · "
            f"run {store.dir.name} · as of {date}"
        ),
    )
    csv = uc2.write_speedup_csv(points, dest / "fig_uc2_speedup.csv")
    typer.echo(f"wrote {png} and {csv}")
