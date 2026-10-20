"""Run directory: frozen manifest + append-only calls.jsonl (F2.3, F2.4).

Raw logs are immutable (CLAUDE.md rule 3): this module only ever creates and appends.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import subprocess
import sys
from collections.abc import Iterable, Iterator
from importlib import metadata
from pathlib import Path
from typing import Any

from dllm_eval.config import ModelRegistry

REDACT_KEYS = frozenset({"api_key", "authorization", "x-api-key", "x-goog-api-key"})
REDACTED = "[REDACTED]"


def secret_values(registry: ModelRegistry) -> list[str]:
    """Current values of every provider key env var, so they can be scrubbed from logs."""
    values = [os.environ.get(p.api_key_env, "") for p in registry.providers.values()]
    return [v for v in values if len(v) >= 8]


def redact(obj: Any, secrets: Iterable[str] = ()) -> Any:
    secrets = list(secrets)
    if isinstance(obj, dict):
        return {
            k: REDACTED if str(k).lower() in REDACT_KEYS else redact(v, secrets)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        for s in secrets:
            if s in obj:
                obj = obj.replace(s, REDACTED)
        return obj
    return obj


def git_state(cwd: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    status = run("status", "--porcelain")
    return {"sha": run("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


def package_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in ("dllm-eval", "openai", "anthropic", "pydantic"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def build_manifest(
    run_id: str,
    experiment: dict[str, Any],
    registry: ModelRegistry,
    model_keys: Iterable[str],
    repo_root: Path,
) -> dict[str, Any]:
    keys = sorted(set(model_keys))
    provider_keys = sorted({registry.models[k].provider for k in keys})
    return {
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git": git_state(repo_root),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": package_versions(),
        "experiment": experiment,
        # Frozen pricing/limits snapshot: cost is always recomputed from this (PRD R11).
        "models": {k: registry.models[k].model_dump(mode="json") for k in keys},
        "providers": {
            k: registry.providers[k].model_dump(mode="json", exclude={"api_key_env"})
            for k in provider_keys
        },
    }


class RunStore:
    MANIFEST = "manifest.json"
    CALLS = "calls.jsonl"

    def __init__(self, run_dir: Path, secrets: Iterable[str] = ()) -> None:
        self.dir = run_dir
        self.secrets = list(secrets)

    @property
    def manifest_path(self) -> Path:
        return self.dir / self.MANIFEST

    @property
    def calls_path(self) -> Path:
        return self.dir / self.CALLS

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def create(self, manifest: dict[str, Any]) -> None:
        if self.exists():
            raise FileExistsError(f"{self.dir} already has a manifest; resume instead")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(redact(manifest, self.secrets), indent=2))

    def read_manifest(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.manifest_path.read_text())
        return result

    def append_call(self, record: dict[str, Any]) -> None:
        line = json.dumps(redact(record, self.secrets), default=str)
        with self.calls_path.open("a") as f:
            f.write(line + "\n")
            f.flush()

    def iter_calls(self) -> Iterator[dict[str, Any]]:
        if not self.calls_path.exists():
            return
        with self.calls_path.open() as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def latest_by_job(self) -> dict[str, dict[str, Any]]:
        """Last record per job_id (a resumed job appends a newer record; nothing is edited)."""
        out: dict[str, dict[str, Any]] = {}
        for rec in self.iter_calls():
            out[rec["job_id"]] = rec
        return out

    def completed_job_ids(self) -> set[str]:
        return {j for j, r in self.latest_by_job().items() if r.get("status") == "ok"}
