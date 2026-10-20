"""dllm-eval command line. M0 ships only config validation; run/analyze/smoke land in M1."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from dllm_eval.config import DEFAULT_MODELS_PATH, load_registry

app = typer.Typer(no_args_is_help=True, add_completion=False)

# TEST_PLAN B1: model ids and prices must be re-verified within 7 days of a real run.
MAX_VERIFY_AGE_DAYS = 7


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
