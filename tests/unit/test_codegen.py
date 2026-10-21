"""UC1 (M2) offline tests. Only our own fixture code is ever executed (via HostExecutor)."""

import asyncio
import base64
import hashlib
import json
import pickle
import zlib
from pathlib import Path
from typing import Any

import pytest

from dllm_eval.config import ModelRegistry
from dllm_eval.experiment import CodegenSource, CodegenWorkload, Experiment
from dllm_eval.logging_store import RunStore
from dllm_eval.runner import resolve_items, run_experiment
from dllm_eval.sandbox import harness
from dllm_eval.sandbox.executor import (
    DockerExecutor,
    HostExecutor,
    SandboxConfig,
    load_sandbox_config,
)
from dllm_eval.usecases.codegen import prompts
from dllm_eval.usecases.codegen.datasets import (
    decode_lcb_private,
    load_dataset_registry,
    load_problems,
    verify,
)
from dllm_eval.usecases.codegen.extract import build_candidate, extract_code
from dllm_eval.usecases.codegen.passk import pass_at_k
from dllm_eval.usecases.codegen.scoring import grading_job, score_run, summarize, to_markdown
from dllm_eval.usecases.codegen.workload import select_problems
from tests.conftest import FAKE_REGISTRY
from tests.fixtures.codegen import make_datasets


@pytest.fixture
def ds_root(tmp_path: Path) -> Path:
    make_datasets(tmp_path)
    return tmp_path


@pytest.fixture
def problems(ds_root: Path) -> dict[str, Any]:
    reg = load_dataset_registry(ds_root / "configs/datasets.yaml")
    out = {}
    for name in ("humaneval+", "mbpp+", "lcb-v6"):
        for p in load_problems(name, reg, ds_root):
            out[p.id] = p
    return out


@pytest.fixture
def host_exec(monkeypatch: pytest.MonkeyPatch) -> HostExecutor:
    monkeypatch.setenv(HostExecutor.ENV_FLAG, "1")
    return HostExecutor()


# --- datasets ----------------------------------------------------------------------


def test_loaders_normalise_all_three_formats(problems: dict[str, Any]) -> None:
    he = problems["humaneval+/HumanEval/0"]
    assert he.kind == "function" and he.entry_point == "add_all"
    assert he.canonical_solution.startswith("from typing import List")  # prompt + body
    assert he.inputs == [[[1, 2, 3]], [[]], [[10, -10, 5]]]  # base + plus
    assert problems["mbpp+/Mbpp/2"].canonical_solution.startswith("def shared")
    stdin = problems["lcb-v6/abc001_a"]
    assert stdin.kind == "stdin" and len(stdin.tests) == 2  # public + private
    lc = problems["lcb-v6/9001"]
    assert lc.kind == "leetcode" and lc.entry_point == "countPairs"
    assert "lcb-v6/abc001_g" not in problems  # hard problems are filtered out


def test_checksum_mismatch_is_refused(ds_root: Path) -> None:
    reg = load_dataset_registry(ds_root / "configs/datasets.yaml")
    path = ds_root / reg.datasets["mbpp+"].path
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="sha256"):
        verify(reg.datasets["mbpp+"], ds_root)


def test_restricted_unpickler_refuses_globals() -> None:
    class Evil:
        def __reduce__(self) -> Any:
            return (print, ("pwned",))

    blob = base64.b64encode(zlib.compress(pickle.dumps(Evil()))).decode()
    with pytest.raises(pickle.UnpicklingError, match="refusing global"):
        decode_lcb_private(blob)


# --- prompts -----------------------------------------------------------------------

# Prompts are frozen: changing any template text requires a new PROMPT_VERSION and a new
# entry here, so old runs stay attributable to the exact prompt they used.
PROMPT_DIGESTS = {"uc1-v1": "a4cff70229d2b6ad5f7691ae862bc3e08f303e6e24db9e4b7de6197176d15fb8"}


def _template_digest() -> str:
    parts = [
        prompts.SYSTEM,
        prompts.FUNCTION_HUMANEVAL,
        prompts.FUNCTION_MBPP,
        prompts.STDIN,
        prompts.LEETCODE,
    ]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def test_prompt_templates_are_frozen() -> None:
    assert _template_digest() == PROMPT_DIGESTS[prompts.PROMPT_VERSION]


def test_messages_per_kind(problems: dict[str, Any]) -> None:
    he = prompts.messages(problems["humaneval+/HumanEval/0"])
    assert [m.role for m in he] == ["system", "user"]
    assert "def add_all" in (he[1].content or "")
    mb = prompts.user_text(problems["mbpp+/Mbpp/2"])
    assert "named `shared`" in mb and '"""' not in mb
    assert "standard input" in prompts.user_text(problems["lcb-v6/abc001_a"])
    assert "class Solution" in prompts.user_text(problems["lcb-v6/9001"])


# --- extraction --------------------------------------------------------------------


def test_extract_prefers_last_block_defining_entry_point(problems: dict[str, Any]) -> None:
    p = problems["humaneval+/HumanEval/0"]
    text = "Draft:\n```python\ndef add_all(xs):\n    return 0\n```\nFinal:\n```python\ndef add_all(xs):\n    return sum(xs)\n```\nUsage:\n```python\nprint(add_all([1]))\n```"
    ex = extract_code(text, p)
    assert ex.method == "fenced" and "return sum(xs)" in ex.code


def test_extract_truncated_unfenced_and_none(problems: dict[str, Any]) -> None:
    p = problems["lcb-v6/abc001_a"]
    assert extract_code("```python\nprint(2*int(input()))\n", p).method == "truncated_fence"
    assert extract_code("import sys\nprint(1)", p).method == "unfenced"
    assert extract_code("I cannot solve this.", p).method == "none"


def test_build_candidate_adds_humaneval_imports(problems: dict[str, Any]) -> None:
    code = build_candidate(
        problems["humaneval+/HumanEval/0"], "def add_all(xs: List[int]):\n    return sum(xs)\n"
    )
    assert code.startswith("from typing import List")
    assert build_candidate(problems["mbpp+/Mbpp/2"], "x = 1\n") == "x = 1\n"


# --- pass@k ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "c", "k", "want"),
    [(5, 1, 1, 0.2), (5, 0, 1, 0.0), (5, 5, 5, 1.0), (10, 3, 5, 1 - 21 / 252), (5, 4, 2, 1.0)],
)
def test_pass_at_k(n: int, c: int, k: int, want: float) -> None:
    assert pass_at_k(n, c, k) == pytest.approx(want)


def test_pass_at_k_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="k=5"):
        pass_at_k(3, 1, 5)


# --- harness comparison rules --------------------------------------------------------


def test_outputs_equal_evalplus_rules() -> None:
    assert harness.outputs_equal(0.3, 0.1 + 0.2, 0)  # floats get atol 1e-6 by default
    assert harness.outputs_equal((1, 2), (1, 2), 0)
    assert not harness.outputs_equal((1, 2), [1, 2], 0)  # tuple vs list differ
    assert harness.outputs_equal([1.0, 2.0], [1.0000001, 2.0], 0)
    assert not harness.outputs_equal("a", "b", 0)


def test_lcb_text_equal() -> None:
    assert harness.lcb_text_equal("6\n", "6")
    assert harness.lcb_text_equal("1 2  \n3\n", "1 2\n3")
    assert harness.lcb_text_equal("0.50", "0.5")  # numeric tokens compare as Decimal
    assert not harness.lcb_text_equal("1 2", "1 3")
    assert not harness.lcb_text_equal("1\n2", "1")


# --- harness end to end (fixture code only, via HostExecutor) ------------------------


def _job(problems: dict[str, Any], pid: str, code: str, **kw: Any) -> dict[str, Any]:
    job, _ = grading_job(pid, problems[pid], f"```python\n{code}```")
    job.update(kw)
    return job


def test_harness_grades_every_status(problems: dict[str, Any], host_exec: HostExecutor) -> None:
    jobs = [
        _job(
            problems,
            "humaneval+/HumanEval/0",
            "def add_all(xs: List[int]) -> int:\n    return sum(xs)\n",
        ),
        _job(
            problems,
            "humaneval+/HumanEval/1",
            "def mean(xs):\n    return sum(xs) / len(xs) + 1e-9\n",
        ),  # within tolerance
        _job(
            problems, "mbpp+/Mbpp/2", "def shared(a, b):\n    return list(set(a) & set(b))\n"
        ),  # list != tuple
        _job(problems, "lcb-v6/abc001_a", "print(2 * int(input()))\n"),
        _job(
            problems,
            "lcb-v6/9001",
            "class Solution:\n    def countPairs(self, nums, target):\n        return sum(1 for i in range(len(nums)) for j in range(i + 1, len(nums)) if nums[i] + nums[j] == target)\n",
        ),
        _job(problems, "humaneval+/HumanEval/0", "def add_all(xs:\n"),  # syntax error
        _job(problems, "lcb-v6/abc001_a", "while True:\n    pass\n", time_limit_s=1),
        _job(
            problems,
            "humaneval+/HumanEval/0",
            "def add_all(xs):\n    while True:\n        pass\n",
            time_limit_s=1,
        ),
        {**_job(problems, "lcb-v6/abc001_a", "x"), "candidate": ""},
    ]
    for i, j in enumerate(jobs):
        j["job_id"] = f"j{i}"
    got = {r["job_id"]: r for r in host_exec.run(jobs)}
    assert [got[f"j{i}"]["status"] for i in range(len(jobs))] == [
        "pass", "pass", "fail", "pass", "pass", "error", "timeout", "timeout", "no_code",
    ]  # fmt: skip
    assert got["j0"]["passed"] == got["j0"]["total"] == 3
    assert got["j2"]["passed"] == 0


def test_host_executor_requires_test_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HostExecutor.ENV_FLAG, raising=False)
    with pytest.raises(PermissionError):
        HostExecutor().run([])


def test_docker_command_is_locked_down() -> None:
    cmd = DockerExecutor(SandboxConfig(image="python:3.12-slim@sha256:" + "0" * 64)).command()
    joined = " ".join(cmd)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--user 65534:65534",
        "--pids-limit",
        ":/harness:ro",
    ):
        assert flag in joined
    assert cmd[-4:] == ["python", "-I", "-B", "/harness/harness.py"]


def test_sandbox_config_requires_digest(tmp_path: Path) -> None:
    p = tmp_path / "sandbox.yaml"
    p.write_text("image: python:3.12-slim\n")
    with pytest.raises(ValueError, match="digest"):
        load_sandbox_config(p)
    p.write_text("image: python:3.12-slim@sha256:" + "a" * 64 + "\n")
    assert load_sandbox_config(p).batch_size == 20


# --- workload selection -------------------------------------------------------------


def _workload(**src: Any) -> CodegenWorkload:
    return CodegenWorkload(
        kind="codegen", prompt_version=prompts.PROMPT_VERSION, sources=[CodegenSource(**src)]
    )


def test_select_sample_is_seeded(ds_root: Path) -> None:
    w = _workload(dataset="humaneval+", sample=1)
    a = select_problems(w, 7, root=ds_root)
    assert a == select_problems(w, 7, root=ds_root)
    assert len(a) == 1


def test_select_ids_and_errors(ds_root: Path) -> None:
    got = select_problems(_workload(dataset="lcb-v6", ids=["9001"]), 0, root=ds_root)
    assert [p.id for p in got] == ["lcb-v6/9001"]
    with pytest.raises(ValueError, match="unknown ids"):
        select_problems(_workload(dataset="lcb-v6", ids=["nope"]), 0, root=ds_root)
    with pytest.raises(ValueError, match="sample"):
        select_problems(_workload(dataset="mbpp+", sample=5), 0, root=ds_root)
    stale = CodegenWorkload(
        kind="codegen", prompt_version="uc1-v0", sources=[CodegenSource(dataset="mbpp+")]
    )
    with pytest.raises(ValueError, match="prompt_version"):
        select_problems(stale, 0, root=ds_root)
    with pytest.raises(ValueError, match="either"):
        CodegenSource(dataset="mbpp+", sample=1, ids=["Mbpp/2"])


# --- runner + scoring end to end ----------------------------------------------------


def _codegen_exp() -> Experiment:
    return Experiment.model_validate(
        {
            "name": "uc1-fake",
            "repeats": 2,
            "budget_usd": 1.0,
            "network_probe": False,
            "arms": [
                {
                    "name": "none",
                    "model": "fake-ar",
                    "reasoning_effort": "none",
                    "max_completion_tokens": 64,
                }
            ],
            "workload": {
                "kind": "codegen",
                "prompt_version": prompts.PROMPT_VERSION,
                "sources": [{"dataset": "lcb-v6"}, {"dataset": "mbpp+"}],
            },
        }
    )


def test_resolve_items_for_codegen(ds_root: Path) -> None:
    items = resolve_items(_codegen_exp(), root=ds_root)
    assert [i.id for i in items] == ["lcb-v6/9001", "lcb-v6/abc001_a", "mbpp+/Mbpp/2"]
    assert items[0].messages[0].role == "system"


def test_score_run_and_resume(
    ds_root: Path, problems: dict[str, Any], host_exec: HostExecutor, tmp_path: Path
) -> None:
    store = RunStore(tmp_path / "run")
    store.create({"run_id": "r"})
    answers = {
        "lcb-v6/abc001_a": "```python\nprint(2 * int(input()))\n```",
        "lcb-v6/9001": "No idea.",
        "mbpp+/Mbpp/2": "```python\ndef shared(a, b):\n    return tuple(sorted(set(a) & set(b)))\n```",
    }
    for item, content in answers.items():
        for r in range(2):
            store.append_call(
                {
                    "job_id": f"fake:none/{item}/{r}",
                    "arm": "fake:none",
                    "model": "fake",
                    "item_id": item,
                    "repeat": r,
                    "status": "ok",
                    "result": {"content": content},
                }
            )
    store.append_call(
        {
            "job_id": "fake:none/x/0",
            "arm": "fake:none",
            "model": "fake",
            "item_id": "mbpp+/Mbpp/2",
            "repeat": 9,
            "status": "error",
        }
    )
    out = tmp_path / "scores.jsonl"
    rows = score_run(store, problems, host_exec, out, log=lambda _: None)
    assert len(rows) == 6
    assert (
        score_run(store, problems, host_exec, out, log=lambda _: None) == rows
    )  # resume: nothing re-graded
    summary = {r["dataset"]: r for r in summarize(rows, {"fake:none": 1})}
    assert summary["mbpp+"]["pass@1"] == 1.0
    assert summary["lcb-v6"]["pass@1"] == 0.5  # one of two problems solved
    assert summary["lcb-v6"]["pass@5"] is None  # only 2 samples per problem
    assert summary["lcb-v6"]["no_code"] == 2
    assert summary["mbpp+"]["api_errors"] == 1
    assert "| `fake:none` | lcb-v6 | 2 | 4 | 50.0% |" in to_markdown(list(summary.values()))


def test_runner_runs_a_codegen_experiment(ds_root: Path, tmp_path: Path) -> None:
    reg = ModelRegistry.model_validate(FAKE_REGISTRY)
    store = RunStore(tmp_path / "run2")
    store.create({"run_id": "r"})
    exp = _codegen_exp()
    s = asyncio.run(
        run_experiment(
            exp, reg, store, "r", items=resolve_items(exp, root=ds_root), log=lambda _: None
        )
    )
    assert s.ok == 6
    assert {r["item_id"] for r in store.iter_calls()} == {
        "lcb-v6/9001",
        "lcb-v6/abc001_a",
        "mbpp+/Mbpp/2",
    }
    first = json.loads(store.calls_path.read_text().splitlines()[0])
    assert first["request"]["messages"][0]["content"] == prompts.SYSTEM


def test_harness_grade_in_process(problems: dict[str, Any]) -> None:
    """Same grading paths as the HostExecutor test, but in-process so coverage sees them.
    Candidates are our fixture code and still run in their own subprocesses."""
    cases = [
        ("humaneval+/HumanEval/0", "def add_all(xs):\n    return sum(xs)\n", "pass"),
        ("humaneval+/HumanEval/0", "def add_all(xs):\n    raise ValueError()\n", "fail"),
        ("humaneval+/HumanEval/0", "x = (\n", "error"),
        ("lcb-v6/abc001_a", "print(2 * int(input()))\n", "pass"),
        ("lcb-v6/abc001_a", "print(3)\n", "fail"),
        ("lcb-v6/abc001_a", "import sys\nsys.exit(1)\n", "error"),
        (
            "lcb-v6/9001",
            "class Solution:\n    def countPairs(self, nums, target):\n        return 'x'\n",
            "fail",
        ),
    ]
    for i, (pid, code, want) in enumerate(cases):
        job = _job(problems, pid, code)
        job["job_id"] = f"k{i}"
        assert harness.grade(job)["status"] == want, (pid, code)
    bad_canonical = _job(problems, "mbpp+/Mbpp/2", "def shared(a, b):\n    return ()\n")
    bad_canonical |= {"job_id": "c", "canonical": "def shared(a, b):\n    while True: pass\n"}
    harness_mod_timeout = harness.CANONICAL_TIMEOUT_S
    try:
        harness.CANONICAL_TIMEOUT_S = 1
        assert harness.grade(bad_canonical)["status"] == "harness_error"
    finally:
        harness.CANONICAL_TIMEOUT_S = harness_mod_timeout


def test_tagged_json_roundtrip() -> None:
    ns: dict[str, Any] = {}
    exec(harness.FN_RUNNER.split("spec = json.load")[0], ns)  # just the enc() helper
    enc = ns["enc"]
    value = {
        "k": (1, 2.5, float("inf")),
        "s": frozenset({1, 2}),
        "b": b"\x00\xff",
        "n": None,
        "l": [True, "x"],
    }
    back = harness.dec(json.loads(json.dumps(enc(value))))
    assert back == {
        "k": (1, 2.5, float("inf")),
        "s": {1, 2},
        "b": b"\x00\xff",
        "n": None,
        "l": [True, "x"],
    }
    nan = harness.dec(json.loads(json.dumps(enc(float("nan")))))
    assert nan != nan
    assert harness.dec(enc(object()))[0] == "__repr__"


# --- Docker executor with a faked `docker` process ------------------------------------


class _Proc:
    def __init__(self, stdout: str, stderr: str = "") -> None:
        self.stdout, self.stderr, self.returncode = stdout, stderr, 0


def test_docker_executor_batches_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    from dllm_eval.sandbox import executor as ex

    seen: list[int] = []

    def fake_run(cmd: list[str], *, input: str, **kw: Any) -> _Proc:
        jobs = json.loads(input)["jobs"]
        seen.append(len(jobs))
        if jobs[0]["job_id"] == "bad":
            return _Proc("not json", "boom")
        if jobs[0]["job_id"] == "slow":
            raise sp.TimeoutExpired(cmd, 1)
        return _Proc(
            json.dumps(
                {
                    "results": [
                        {"job_id": j["job_id"], "status": "pass", "passed": 1, "total": 1}
                        for j in jobs
                    ]
                }
            )
        )

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    d = DockerExecutor(SandboxConfig(image="i@sha256:" + "0" * 64, batch_size=2, parallel=2))
    out = d.run([{"job_id": f"j{i}"} for i in range(5)])
    assert sorted(seen) == [1, 2, 2] and [r["job_id"] for r in out] == [f"j{i}" for i in range(5)]
    assert d.run([{"job_id": "bad"}])[0]["status"] == "harness_error"
    assert "timed out" in d.run([{"job_id": "slow"}])[0]["detail"]


def test_docker_available_handles_missing_binary() -> None:
    from dllm_eval.sandbox.executor import docker_available

    assert docker_available("definitely-not-a-docker-binary") is False


# --- CLI: score / fetch-data guards --------------------------------------------------


def test_cli_score_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from dllm_eval import cli

    runs = tmp_path / "runs"
    r = CliRunner().invoke(cli.app, ["score", "nope", "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "no run" in r.output

    prompts_run = RunStore(runs / "p")
    prompts_run.create(
        {
            "experiment": json.loads(
                Experiment.model_validate(
                    {**_codegen_exp().model_dump(), "workload": {"items": []}}
                ).model_dump_json()
            )
        }
    )
    r = CliRunner().invoke(cli.app, ["score", "p", "--runs-dir", str(runs)])
    assert r.exit_code == 1 and "not a UC1" in r.output

    cg = RunStore(runs / "c")
    cg.create({"experiment": json.loads(_codegen_exp().model_dump_json())})
    unpinned = tmp_path / "sandbox.yaml"
    unpinned.write_text("image: python:3.12-slim\n")
    r = CliRunner().invoke(
        cli.app, ["score", "c", "--runs-dir", str(runs), "--sandbox", str(unpinned)]
    )
    assert r.exit_code == 1 and "digest" in r.output

    pinned = tmp_path / "pinned.yaml"
    pinned.write_text("image: python:3.12-slim@sha256:" + "0" * 64 + "\n")
    monkeypatch.setattr("dllm_eval.sandbox.executor.docker_available", lambda *a: False)
    r = CliRunner().invoke(
        cli.app, ["score", "c", "--runs-dir", str(runs), "--sandbox", str(pinned)]
    )
    assert r.exit_code == 1 and "Docker" in r.output


def test_cli_fetch_data_verifies(ds_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from dllm_eval import cli

    monkeypatch.chdir(ds_root)
    r = CliRunner().invoke(cli.app, ["fetch-data"])
    assert r.exit_code == 0, r.output
    assert r.output.count("sha256 verified") == 3
    (ds_root / "data/raw/he/he.jsonl.gz").write_bytes(b"tampered")
    r = CliRunner().invoke(cli.app, ["fetch-data"])
    assert r.exit_code == 1 and "FAILED" in r.output


def test_stdin_extraction_skips_sample_io_blocks(problems: dict[str, Any]) -> None:
    p = problems["lcb-v6/abc001_a"]
    text = "Program:\n```python\nprint(2 * int(input()))\n```\nSample:\n```\n3\n6\n```"
    assert "input()" in extract_code(text, p).code


def test_summarize_separates_budget_out() -> None:
    rows = [
        {
            "job_id": "a",
            "arm": "m:high",
            "dataset": "lcb-v6",
            "item_id": "x",
            "status": "no_code",
            "extraction": "none",
        },
        {
            "job_id": "b",
            "arm": "m:high",
            "dataset": "lcb-v6",
            "item_id": "x",
            "status": "no_code",
            "extraction": "none",
        },
        {
            "job_id": "c",
            "arm": "m:high",
            "dataset": "lcb-v6",
            "item_id": "y",
            "status": "pass",
            "extraction": "fenced",
        },
    ]
    [s] = summarize(rows, finish={"a": "length", "b": "stop", "c": "stop"})
    assert s["budget_out"] == 1 and s["no_code"] == 1
    assert s["pass@1"] == 0.5  # budget-out still fails


def test_score_run_is_chunked_and_survives_a_crash(
    problems: dict[str, Any], host_exec: HostExecutor, tmp_path: Path
) -> None:
    store = RunStore(tmp_path / "run")
    store.create({"run_id": "r"})
    for r in range(5):
        store.append_call(
            {
                "job_id": f"a/{r}",
                "arm": "a",
                "model": "m",
                "item_id": "lcb-v6/abc001_a",
                "repeat": r,
                "status": "ok",
                "result": {"content": "```python\nprint(2 * int(input()))\n```"},
            }
        )

    class CrashAfterFirstChunk:
        calls = 0

        def run(self, jobs: Any) -> list[dict[str, Any]]:
            self.calls += 1
            if self.calls > 1:
                raise KeyboardInterrupt  # e.g. the process is killed mid-run
            return host_exec.run(jobs)

    out = tmp_path / "scores.jsonl"
    with pytest.raises(KeyboardInterrupt):
        score_run(store, problems, CrashAfterFirstChunk(), out, chunk_size=2, log=lambda _: None)
    assert len(out.read_text().splitlines()) == 2  # first chunk was saved
    rows = score_run(store, problems, host_exec, out, chunk_size=2, log=lambda _: None)
    assert sorted(r["job_id"] for r in rows) == [
        f"a/{r}" for r in range(5)
    ]  # resumed, no duplicates


def test_reference_outputs_are_cached_per_problem(problems: dict[str, Any]) -> None:
    harness._CANON_CACHE.clear()
    good = _job(
        problems, "mbpp+/Mbpp/2", "def shared(a, b):\n    return tuple(sorted(set(a) & set(b)))\n"
    )
    good["job_id"] = "g1"
    assert harness.grade(good)["status"] == "pass"
    # Same problem, broken reference: only passes if the cached reference outputs are reused.
    reused = {**good, "job_id": "g2", "canonical": "raise SystemExit(1)\n"}
    assert harness.grade(reused)["status"] == "pass"
    other = {**reused, "job_id": "g3", "cache_key": "different-problem"}
    assert (
        harness.grade(other)["status"] == "harness_error"
    )  # no cache hit -> reference really runs
    harness._CANON_CACHE.clear()
