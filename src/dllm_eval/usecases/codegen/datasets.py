"""UC1 dataset registry and loaders (F4.1): HumanEval+, MBPP+, LiveCodeBench v6.

Downloaded files are untrusted data. This module only parses them; nothing here executes
dataset code (canonical solutions run only inside the sandbox). LiveCodeBench's private
tests are pickled, so they go through a restricted unpickler that refuses every global.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import pickle
import re
import urllib.request
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_REGISTRY = Path("configs/datasets.yaml")
DatasetName = Literal["humaneval+", "mbpp+", "lcb-v6"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetSpec(_Strict):
    version: str
    url: str
    path: Path
    bytes: int
    sha256: str
    problems: int


class DatasetRegistry(_Strict):
    datasets: dict[str, DatasetSpec]


def load_dataset_registry(path: Path = DEFAULT_REGISTRY) -> DatasetRegistry:
    with path.open() as f:
        return DatasetRegistry.model_validate(yaml.safe_load(f))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(spec: DatasetSpec, root: Path = Path(".")) -> Path:
    """Path to the dataset file, after checking its size and checksum."""
    p = root / spec.path
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run `dllm-eval fetch-data`")
    if p.stat().st_size != spec.bytes or sha256_of(p) != spec.sha256:
        raise ValueError(f"{p} does not match the pinned size/sha256 in configs/datasets.yaml")
    return p


def fetch(spec: DatasetSpec, root: Path = Path(".")) -> Path:
    """Download if missing, then verify. Never overwrites a file that fails verification."""
    p = root / spec.path
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        with urllib.request.urlopen(spec.url, timeout=120) as resp, tmp.open("wb") as out:
            while block := resp.read(1 << 20):
                out.write(block)
        tmp.rename(p)
    return verify(spec, root)


# --- problem model ------------------------------------------------------------------


class Test(_Strict):
    input: str
    output: str


class CodeProblem(_Strict):
    """One UC1 problem, normalised across datasets.

    kind:
      function  - EvalPlus: call `entry_point(*args)` for each input; expected outputs come
                  from running `canonical_solution` in the sandbox, compared with `atol`.
      stdin     - LiveCodeBench AtCoder: a program reads stdin, output compared as text.
      leetcode  - LiveCodeBench LeetCode: `Solution().<method>(*args)`, JSON args per line.
    """

    id: str  # "<dataset>/<native id>", e.g. "humaneval+/HumanEval/0"
    dataset: str
    kind: Literal["function", "stdin", "leetcode"]
    prompt: str  # dataset-native problem text (formatted into a message by prompts.py)
    entry_point: str  # function name, or the LeetCode method name; "" for stdin
    starter_code: str = ""
    canonical_solution: str = ""
    inputs: list[list[Any]] = Field(default_factory=list)  # function kind
    atol: float = 0.0
    tests: list[Test] = Field(default_factory=list)  # stdin / leetcode kinds
    difficulty: str | None = None
    contest_date: str | None = None
    platform: str | None = None


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def load_evalplus(name: str, path: Path) -> list[CodeProblem]:
    out = []
    for r in _read_jsonl(path):
        out.append(
            CodeProblem(
                id=f"{name}/{r['task_id']}",
                dataset=name,
                kind="function",
                prompt=r["prompt"],
                entry_point=r["entry_point"],
                # HumanEval+ canonical solutions are bodies that continue the prompt;
                # MBPP+ ones are complete functions.
                canonical_solution=(
                    r["prompt"] + r["canonical_solution"]
                    if name == "humaneval+"
                    else r["canonical_solution"]
                ),
                inputs=list(r["base_input"]) + list(r["plus_input"]),
                atol=float(r.get("atol") or 0),
            )
        )
    return out


class _NoGlobalsUnpickler(pickle.Unpickler):
    """Plain data only (str/int/list/dict/...). Any class or function lookup is refused,
    which is what makes unpickling untrusted bytes safe."""

    def find_class(self, module: str, name: str) -> Any:
        raise pickle.UnpicklingError(f"refusing global {module}.{name}")


def decode_lcb_private(blob: str) -> list[dict[str, str]]:
    """LiveCodeBench lite stores private tests as base64(zlib(pickle(json-string)))."""
    raw = zlib.decompress(base64.b64decode(blob))
    value = _NoGlobalsUnpickler(io.BytesIO(raw)).load()
    if isinstance(value, bytes):
        value = value.decode()
    data = json.loads(value) if isinstance(value, str) else value
    if not isinstance(data, list):
        raise ValueError("unexpected private test format")
    return data


_METHOD = re.compile(r"^\s*def\s+(\w+)\s*\(\s*self", re.M)


def load_lcb(
    name: str, path: Path, difficulties: Iterable[str] = ("easy", "medium")
) -> list[CodeProblem]:
    keep = set(difficulties)
    out = []
    for r in _read_jsonl(path):
        if r["difficulty"] not in keep:
            continue
        tests = json.loads(r["public_test_cases"]) + decode_lcb_private(r["private_test_cases"])
        types = {t["testtype"] for t in tests}
        if len(types) != 1:
            raise ValueError(f"{r['question_id']}: mixed test types {types}")
        functional = types == {"functional"}
        method = ""
        if functional:
            m = _METHOD.search(r["starter_code"])
            if not m:
                raise ValueError(f"{r['question_id']}: no method in starter code")
            method = m.group(1)
        out.append(
            CodeProblem(
                id=f"{name}/{r['question_id']}",
                dataset=name,
                kind="leetcode" if functional else "stdin",
                prompt=r["question_content"],
                entry_point=method,
                starter_code=r["starter_code"],
                tests=[Test(input=t["input"], output=t["output"]) for t in tests],
                difficulty=r["difficulty"],
                contest_date=r["contest_date"],
                platform=r["platform"],
            )
        )
    return out


def load_problems(
    name: str, registry: DatasetRegistry, root: Path = Path(".")
) -> list[CodeProblem]:
    spec = registry.datasets[name]
    path = verify(spec, root)
    if name in ("humaneval+", "mbpp+"):
        return load_evalplus(name, path)
    if name == "lcb-v6":
        return load_lcb(name, path)
    raise ValueError(f"unknown dataset {name!r}")
