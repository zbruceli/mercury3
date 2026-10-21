"""Runs the grading harness in an isolated container (F4.3).

Model-generated code runs ONLY through DockerExecutor (CLAUDE.md). HostExecutor exists so
the harness logic can be tested offline on our own fixture code; it refuses to run unless
the test suite sets DLLM_EVAL_ALLOW_HOST_EXEC=1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

HARNESS_DIR = Path(__file__).resolve().parent
# Docker Desktop installs its CLI either system-wide or per user (~/.docker/bin).
DOCKER_CANDIDATES = ("/usr/local/bin/docker", "~/.docker/bin/docker", "/opt/homebrew/bin/docker")


def find_docker() -> str:
    """The docker CLI: from PATH, else Docker Desktop's install locations; "docker" if none."""
    found = shutil.which("docker")
    if found:
        return found
    for c in DOCKER_CANDIDATES:
        p = Path(c).expanduser()
        if p.exists():
            return str(p)
    return "docker"


DEFAULT_SANDBOX_CONFIG = Path("configs/sandbox.yaml")


class SandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image: str  # must be pinned by digest: python:3.12-slim@sha256:...
    cpus: float = Field(default=1.0, gt=0)
    memory: str = "1g"
    pids_limit: int = Field(default=256, gt=0)
    tmpfs_size: str = "256m"
    batch_size: int = Field(default=20, gt=0)  # grading jobs per container
    parallel: int = Field(default=4, gt=0)  # containers at once
    batch_timeout_s: float = Field(default=1800, gt=0)


def load_sandbox_config(path: Path = DEFAULT_SANDBOX_CONFIG) -> SandboxConfig:
    with path.open() as f:
        cfg = SandboxConfig.model_validate(yaml.safe_load(f))
    if "@sha256:" not in cfg.image:
        raise ValueError(f"sandbox image must be pinned by digest, got {cfg.image!r}")
    return cfg


class Executor(Protocol):
    def run(self, jobs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]: ...


def _batches(jobs: Sequence[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [list(jobs[i : i + size]) for i in range(0, len(jobs), size)]


def _parse(stdout: str, batch: list[dict[str, Any]], stderr: str) -> list[dict[str, Any]]:
    try:
        results: list[dict[str, Any]] = json.loads(stdout)["results"]
        return results
    except (ValueError, KeyError):
        detail = f"sandbox produced no results: {stderr.strip()[-300:]}"
        return [
            {
                "job_id": j["job_id"],
                "status": "harness_error",
                "passed": 0,
                "total": 0,
                "detail": detail,
            }
            for j in batch
        ]


class DockerExecutor:
    def __init__(self, cfg: SandboxConfig, docker: str | None = None) -> None:
        self.cfg = cfg
        self.docker = docker or find_docker()

    def command(self) -> list[str]:
        c = self.cfg
        return [
            self.docker, "run", "--rm", "-i",
            "--network", "none",
            "--cpus", str(c.cpus),
            "--memory", c.memory, "--memory-swap", c.memory,
            "--pids-limit", str(c.pids_limit),
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={c.tmpfs_size}",
            "--user", "65534:65534",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{HARNESS_DIR}:/harness:ro",
            c.image,
            "python", "-I", "-B", "/harness/harness.py",
        ]  # fmt: skip

    def _run_batch(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            proc = subprocess.run(
                self.command(),
                input=json.dumps({"jobs": batch}),
                capture_output=True,
                text=True,
                timeout=self.cfg.batch_timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _parse("", batch, "batch timed out")
        return _parse(proc.stdout, batch, proc.stderr)

    def _run_batch_isolating(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """If a container dies (e.g. OOM), re-run its jobs one per container so a single
        bad candidate cannot erase the other results in its batch."""
        results = self._run_batch(batch)
        lost = all(r["status"] == "harness_error" for r in results)
        if lost and len(batch) > 1:
            return [r for job in batch for r in self._run_batch([job])]
        return results

    def run(self, jobs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        with ThreadPoolExecutor(self.cfg.parallel) as pool:
            parts = pool.map(self._run_batch_isolating, _batches(jobs, self.cfg.batch_size))
            return [r for part in parts for r in part]


class HostExecutor:
    """TESTS ONLY: runs the harness as a host subprocess. Never use it on model output."""

    ENV_FLAG = "DLLM_EVAL_ALLOW_HOST_EXEC"

    def run(self, jobs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        if os.environ.get(self.ENV_FLAG) != "1":
            raise PermissionError("HostExecutor is for the test suite only; use DockerExecutor")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(HARNESS_DIR / "harness.py")],
            input=json.dumps({"jobs": list(jobs)}),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        return _parse(proc.stdout, list(jobs), proc.stderr)


def docker_available(docker: str | None = None) -> bool:
    docker = docker or find_docker()
    try:
        return (
            subprocess.run(
                [docker, "info"], capture_output=True, timeout=15, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
