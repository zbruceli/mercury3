"""Harness validation in the real Docker sandbox (not run by default: `make test-sandbox`).

1. Every EvalPlus canonical solution, graded as a candidate, must pass. A failure means the
   grader is wrong, not a model. Canonical solutions are downloaded code, so they only ever
   run inside the container.
2. Inputs the reference itself fails are dropped (same for every model). The known drops
   are pinned below so any change in grading semantics shows up here.
3. The container really is locked down: no network, no writes outside /tmp.
"""

from pathlib import Path

import pytest

from dllm_eval.sandbox.executor import DockerExecutor, docker_available, load_sandbox_config
from dllm_eval.usecases.codegen.datasets import load_dataset_registry, load_problems
from dllm_eval.usecases.codegen.scoring import grading_job
from tests.conftest import REPO

pytestmark = pytest.mark.sandbox

# Verified 2026-10-20 (docs/M2_UC1_RESULTS.md): float error on huge coefficients
# (HumanEval/32 find_zero) and a 1.66M-combination output over the memory cap (Mbpp/255).
EXPECTED_DROPS = {"humaneval+/HumanEval/32": 7, "mbpp+/Mbpp/255": 1}


def _executor() -> DockerExecutor:
    try:
        cfg = load_sandbox_config(REPO / "configs" / "sandbox.yaml")
    except ValueError as e:
        pytest.skip(f"sandbox image not pinned: {e}")
    if not docker_available():
        pytest.skip("Docker daemon not running")
    return DockerExecutor(cfg)


@pytest.mark.parametrize("dataset", ["humaneval+", "mbpp+"])
def test_canonical_solutions_pass(dataset: str) -> None:
    executor = _executor()
    reg = load_dataset_registry(REPO / "configs" / "datasets.yaml")
    if not (REPO / reg.datasets[dataset].path).exists():
        pytest.skip("dataset not downloaded (dllm-eval fetch-data)")
    problems = load_problems(dataset, reg, Path(REPO))
    jobs = [grading_job(p.id, p, f"```python\n{p.canonical_solution}\n```")[0] for p in problems]
    results = executor.run(jobs)
    assert len(results) == len(problems)
    failed = [(r["job_id"], r["status"], r.get("detail")) for r in results if r["status"] != "pass"]
    assert not failed, f"{len(failed)} canonical solutions failed: {failed[:10]}"
    drops = {r["job_id"]: r["dropped_inputs"] for r in results if r.get("dropped_inputs")}
    assert drops == {k: v for k, v in EXPECTED_DROPS.items() if k.startswith(dataset)}


def test_container_is_locked_down() -> None:
    executor = _executor()

    def job(job_id: str, code: str) -> dict[str, object]:
        return {"job_id": job_id, "kind": "stdin", "candidate": code, "entry_point": "",
                "tests": [{"input": "", "output": "ok"}]}  # fmt: skip

    probes = [
        job(
            "net",
            "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=3)\nprint('ok')\n",
        ),
        job("dns", "import socket\nsocket.gethostbyname('example.com')\nprint('ok')\n"),
        job("write", "open('/harness/x', 'w').write('x')\nprint('ok')\n"),
        job("etc", "open('/etc/pwned', 'w').write('x')\nprint('ok')\n"),
        job("tmp", "open('/tmp/fine', 'w').write('x')\nprint('ok')\n"),  # control: allowed
    ]
    got = {r["job_id"]: r for r in executor.run(probes)}
    for blocked in ("net", "dns", "write", "etc"):  # each must crash, never print "ok"
        assert got[blocked]["status"] == "error", (blocked, got[blocked])
    assert got["tmp"]["status"] == "pass"
