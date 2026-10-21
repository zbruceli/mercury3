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
from dllm_eval.experiment import CodegenWorkload, Experiment, load_experiment
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
    """For UC1 runs: the exact dataset bytes and prompt version (PRD R10/R11)."""
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
    """Download any missing UC1 dataset and verify every file against its pinned sha256."""
    from dllm_eval.usecases.codegen.datasets import fetch, load_dataset_registry

    reg = load_dataset_registry(datasets)
    for name, spec in reg.datasets.items():
        try:
            path = fetch(spec)
        except (OSError, ValueError) as e:
            typer.echo(f"{name}: FAILED: {e}", err=True)
            raise typer.Exit(1) from e
        typer.echo(f"{name} {spec.version}: OK ({path}, {spec.bytes} bytes, sha256 verified)")


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
    if not isinstance(exp.workload, CodegenWorkload):
        typer.echo("not a UC1 (codegen) run", err=True)
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
    df = load_calls(store)
    rows = arm_summary(df, seed=store.read_manifest()["experiment"].get("seed", 0))
    typer.echo(to_markdown(rows))
    probes = list(store.iter_probes())
    if probes:
        typer.echo("\nNetwork baseline (median of samples, ms):\n" + probe_markdown(probes))
    dest = out_dir / run_id
    dest.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest / "calls.parquet", index=False)
    (dest / "arm_summary.json").write_text(json.dumps(rows, indent=2))
    typer.echo(f"\nwrote {dest}/calls.parquet and arm_summary.json")


@app.command()
def charts(
    run_id: Annotated[str, typer.Argument(help="Scored UC1 run id")],
    runs_dir: Annotated[Path, typer.Option()] = DEFAULT_RUNS_DIR,
    out_dir: Annotated[Path, typer.Option()] = DEFAULT_ANALYSIS_DIR,
) -> None:
    """UC1 speed-quality Pareto chart (F6.4) from logs + scores.jsonl. Offline only."""
    from dllm_eval.analysis.charts import pareto_points, plot_pareto, write_points_csv
    from dllm_eval.analysis.load import load_calls

    store = RunStore(runs_dir / run_id)
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
