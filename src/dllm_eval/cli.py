"""dllm-eval command line: validate-config, run (dry-run / --live), analyze (offline)."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from dllm_eval.config import DEFAULT_MODELS_PATH, ProviderKind, load_registry
from dllm_eval.experiment import load_experiment
from dllm_eval.logging_store import RunStore, build_manifest, secret_values
from dllm_eval.runner import dry_run, run_experiment

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
) -> None:
    """Run an experiment. Always prints the dry-run estimate; spends tokens only with --live."""
    try:
        registry = load_registry(models)
        exp = load_experiment(config)
        rows = dry_run(exp, registry)
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
    typer.echo(f"total worst-case cost ${worst:.4f}")
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
        store.create(
            build_manifest(run_id, exp_dump, registry, [a.model for a in exp.arms], REPO_ROOT)
        )

    summary = asyncio.run(run_experiment(exp, registry, store, run_id, log=typer.echo))
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
    from dllm_eval.analysis.tables import arm_summary, to_markdown

    store = RunStore(runs_dir / run_id)
    if not store.exists():
        typer.echo(f"no run {run_id} under {runs_dir}", err=True)
        raise typer.Exit(1)
    df = load_calls(store)
    rows = arm_summary(df, seed=store.read_manifest()["experiment"].get("seed", 0))
    typer.echo(to_markdown(rows))
    dest = out_dir / run_id
    dest.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest / "calls.parquet", index=False)
    (dest / "arm_summary.json").write_text(json.dumps(rows, indent=2))
    typer.echo(f"\nwrote {dest}/calls.parquet and arm_summary.json")
