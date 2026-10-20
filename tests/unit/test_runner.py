"""Runner + CLI over the fake provider: the TEST_PLAN A4 pipeline check (no network)."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from dllm_eval.cli import app
from dllm_eval.config import ModelRegistry
from dllm_eval.experiment import Experiment
from dllm_eval.logging_store import RunStore
from dllm_eval.runner import dry_run, expand_jobs, run_experiment
from tests.conftest import FAKE_REGISTRY

EXP: dict[str, Any] = {
    "name": "fake-e2e",
    "seed": 7,
    "repeats": 2,
    "budget_usd": 1.0,
    "max_concurrency_per_provider": 3,
    "warmup_calls_per_arm": 1,
    "arms": [
        {"name": "instant", "model": "fake-diffusion", "reasoning_effort": "instant", "temperature": 0.5, "max_completion_tokens": 50},
        {"name": "none", "model": "fake-ar", "reasoning_effort": "none", "max_completion_tokens": 50, "expected_output_tokens": 10},
    ],
    "workload": {
        "items": [
            {"id": f"p{i}", "messages": [{"role": "user", "content": f"prompt number {i}"}]} for i in range(5)
        ]
    },
}  # fmt: skip


def exp(**overrides: Any) -> Experiment:
    d = copy.deepcopy(EXP)
    d.update(overrides)
    return Experiment.model_validate(d)


def run(e: Experiment, reg: ModelRegistry, tmp: Path) -> tuple[Any, RunStore]:
    store = RunStore(tmp / "run")
    store.create({"run_id": "r"}) if not store.exists() else None
    return asyncio.run(run_experiment(e, reg, store, "r", log=lambda _: None)), store


def test_jobs_are_interleaved_deterministically() -> None:
    a, b = expand_jobs(exp()), expand_jobs(exp())
    assert [j.job_id for j in a] == [j.job_id for j in b]
    assert len(a) == 2 * 5 * 2
    arms = [j.arm.name for j in a]
    assert arms != sorted(arms)  # shuffled, not blocked by model
    assert sum(j.warmup for j in a) == 2  # one per arm


def test_check_against_rejects_bad_arm(fake_registry: ModelRegistry) -> None:
    bad = copy.deepcopy(EXP)
    bad["arms"][0]["temperature"] = 0.2
    with pytest.raises(ValueError, match="temperature"):
        Experiment.model_validate(bad).check_against(fake_registry)


def test_dry_run_estimates(fake_registry: ModelRegistry) -> None:
    rows = {r.arm_id: r for r in dry_run(exp(), fake_registry)}
    r = rows["fake-ar:none"]
    assert r.jobs == 10
    assert r.output_tokens_worst == 500 and r.output_tokens_expected == 100
    assert r.cost_worst > r.cost_expected > 0


def test_end_to_end_run_and_resume(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    s, store = run(exp(), fake_registry, tmp_path)
    assert (s.planned, s.ok, s.errors, s.aborted) == (20, 20, 0, None)
    recs = list(store.iter_calls())
    assert len(recs) == 20
    rec = recs[0]
    assert rec["status"] == "ok" and rec["cost_usd"] > 0 and "raw_response" not in rec["result"]
    assert rec["request"]["model_key"] in {"fake-diffusion", "fake-ar"}

    s2, _ = run(exp(), fake_registry, tmp_path)  # resume: nothing left to do
    assert (s2.skipped_done, s2.ok) == (20, 0)
    assert s2.spent_usd == pytest.approx(s.spent_usd)


def test_warning_aborts_run(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    e = copy.deepcopy(EXP)
    e["arms"][1]["provider_extras"] = {"fake_warning": "temperature reset"}
    e["max_concurrency_per_provider"] = 1
    s, store = run(Experiment.model_validate(e), fake_registry, tmp_path)
    assert s.aborted and "warning" in s.aborted
    assert any(r["status"] == "warning" for r in store.iter_calls())
    assert s.not_started > 0


def test_billing_error_aborts_run(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    e = copy.deepcopy(EXP)
    e["arms"][0]["provider_extras"] = {"fake_fail": ["quota"]}
    e["max_concurrency_per_provider"] = 1
    s, store = run(Experiment.model_validate(e), fake_registry, tmp_path)
    assert s.aborted and "run-fatal" in s.aborted
    err = next(r for r in store.iter_calls() if r["status"] == "error")
    assert err["error"]["kind"] == "run_fatal"


def test_budget_guard_stops_launching(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    s, _ = run(exp(budget_usd=0.00002, max_concurrency_per_provider=1), fake_registry, tmp_path)
    assert s.aborted and "budget" in s.aborted
    assert 0 < s.ok < 20


# --- CLI ---------------------------------------------------------------------------


@pytest.fixture
def cli_files(tmp_path: Path) -> tuple[Path, Path]:
    models = tmp_path / "models.yaml"
    models.write_text(yaml.safe_dump(FAKE_REGISTRY))
    cfg = tmp_path / "exp.yaml"
    cfg.write_text(yaml.safe_dump(EXP))
    return models, cfg


def test_cli_requires_live_flag(cli_files: tuple[Path, Path], tmp_path: Path) -> None:
    models, cfg = cli_files
    r = CliRunner().invoke(
        app, ["run", str(cfg), "--models", str(models), "--runs-dir", str(tmp_path / "runs")]
    )
    assert r.exit_code == 2
    assert "--live" in r.output
    assert not (tmp_path / "runs").exists()


def test_cli_dry_run(cli_files: tuple[Path, Path]) -> None:
    models, cfg = cli_files
    r = CliRunner().invoke(app, ["run", str(cfg), "--models", str(models), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "fake-ar:none" in r.output and "worst-case" in r.output


def test_cli_live_then_analyze(cli_files: tuple[Path, Path], tmp_path: Path) -> None:
    models, cfg = cli_files
    runs = tmp_path / "runs"
    r = CliRunner().invoke(
        app, ["run", str(cfg), "--models", str(models), "--runs-dir", str(runs), "--live"]
    )
    assert r.exit_code == 0, r.output
    [run_dir] = list(runs.iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["experiment"]["name"] == "fake-e2e"
    assert set(manifest["models"]) == {"fake-ar", "fake-diffusion"}
    assert "api_key_env" not in manifest["providers"]["fakeco"]

    out = tmp_path / "analysis"
    a = CliRunner().invoke(
        app, ["analyze", run_dir.name, "--runs-dir", str(runs), "--out-dir", str(out)]
    )
    assert a.exit_code == 0, a.output
    assert "`fake-ar:none` | 10 | 10 | 0 | 0 |" in a.output
    rows = json.loads((out / run_dir.name / "arm_summary.json").read_text())
    by_arm = {row["arm"]: row for row in rows}
    assert by_arm["fake-ar:none"]["latency_n"] == 9  # warm-up call excluded
    assert (out / run_dir.name / "calls.parquet").exists()

    # resuming with a changed config is refused
    changed = copy.deepcopy(EXP)
    changed["repeats"] = 3
    cfg.write_text(yaml.safe_dump(changed))
    r2 = CliRunner().invoke(
        app,
        [
            "run",
            str(cfg),
            "--models",
            str(models),
            "--runs-dir",
            str(runs),
            "--live",
            "--resume",
            run_dir.name,
        ],
    )
    assert r2.exit_code == 1 and "changed" in r2.output
