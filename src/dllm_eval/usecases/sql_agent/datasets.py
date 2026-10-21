"""BIRD Mini-Dev (SQLite) loader and stratified sampler (F5.1).

The archive is untrusted data. We extract exactly the members we need (the question file
and the 11 `.sqlite` files) to paths we build ourselves, so a crafted member name can't
write anywhere else. The databases are only ever opened read-only (tools.py).
"""

from __future__ import annotations

import json
import random
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dllm_eval.usecases.codegen.datasets import DatasetSpec, verify

DATASET = "bird-minidev"
Difficulty = Literal["simple", "moderate", "challenging"]
DIFFICULTIES: tuple[Difficulty, ...] = ("simple", "moderate", "challenging")

_ARCHIVE_DIR = "minidev/MINIDEV/"
QUESTIONS_MEMBER = _ARCHIVE_DIR + "mini_dev_sqlite.json"
EXTRACT_DIR = Path("data/bird-minidev")
MARKER = "EXTRACTED.json"


# Gold queries that don't finish in 60 s on the read-only connection (checked 2026-10-21 on
# all 498; the slowest of the rest took 2.1 s). They can't be scored, so they're never
# sampled. tests/unit/test_sql_agent.py pins this set.
GOLD_EXCLUDED = frozenset({"bird/518", "bird/701"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BirdQuestion(_Strict):
    id: str  # "bird/<question_id>"
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: Difficulty


def _db_members(z: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """db_id -> the archive's `dev_databases/<db_id>/<db_id>.sqlite` member."""
    out = {}
    for info in z.infolist():
        parts = info.filename.split("/")
        if (
            len(parts) == 5
            and info.filename.startswith(_ARCHIVE_DIR + "dev_databases/")
            and parts[4] == f"{parts[3]}.sqlite"
            and parts[3].replace("_", "").isalnum()
        ):
            out[parts[3]] = info
    return out


def extract(spec: DatasetSpec, root: Path = Path(".")) -> Path:
    """Verify the pinned archive, then extract the questions and SQLite files (idempotent)."""
    dest = root / EXTRACT_DIR
    marker = dest / MARKER
    if marker.exists() and json.loads(marker.read_text()).get("sha256") == spec.sha256:
        return dest
    archive = verify(spec, root)
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "databases").mkdir(parents=True)
    sizes: dict[str, int] = {}
    with zipfile.ZipFile(archive) as z:
        targets = {"questions.json": z.getinfo(QUESTIONS_MEMBER)}
        targets |= {f"databases/{db}.sqlite": info for db, info in _db_members(z).items()}
        for rel, info in targets.items():
            with z.open(info) as src, (tmp / rel).open("wb") as out:  # CRC-checked by zipfile
                shutil.copyfileobj(src, out, 1 << 20)
            sizes[rel] = info.file_size
    (tmp / MARKER).write_text(json.dumps({"sha256": spec.sha256, "files": sizes}, indent=2))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest


def extracted_dir(spec: DatasetSpec, root: Path = Path(".")) -> Path:
    """The extraction directory, after checking it came from the pinned archive."""
    dest = root / EXTRACT_DIR
    marker = dest / MARKER
    if not marker.exists():
        raise FileNotFoundError(f"{dest} missing: run `dllm-eval fetch-data`")
    info = json.loads(marker.read_text())
    if info.get("sha256") != spec.sha256:
        raise ValueError(f"{dest} was extracted from a different archive; re-run fetch-data")
    for rel, size in info["files"].items():
        p = dest / rel
        if not p.exists() or p.stat().st_size != size:
            raise ValueError(f"{p} is missing or changed since extraction; re-run fetch-data")
    return dest


def db_path(spec: DatasetSpec, db_id: str, root: Path = Path(".")) -> Path:
    if not db_id.replace("_", "").isalnum():
        raise ValueError(f"bad db_id {db_id!r}")
    return extracted_dir(spec, root) / "databases" / f"{db_id}.sqlite"


def load_questions(spec: DatasetSpec, root: Path = Path(".")) -> list[BirdQuestion]:
    """All questions, in file order. The release lists question_id 137 and 138 twice with
    identical content; the duplicates are dropped (500 rows -> 498 questions)."""
    raw = json.loads((extracted_dir(spec, root) / "questions.json").read_text())
    seen: dict[int, dict[str, object]] = {}
    out = []
    for r in raw:
        qid = int(r["question_id"])
        if qid in seen:
            if seen[qid] != r:
                raise ValueError(f"question_id {qid} appears twice with different content")
            continue
        seen[qid] = r
        out.append(
            BirdQuestion(
                id=f"bird/{qid}",
                question_id=qid,
                db_id=r["db_id"],
                question=r["question"],
                evidence=r.get("evidence") or "",
                gold_sql=r["SQL"],
                difficulty=r["difficulty"],
            )
        )
    return out


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Proportional allocation of n draws over strata (largest remainder, ties by name)."""
    total = sum(counts.values())
    if not 0 < n <= total:
        raise ValueError(f"cannot draw {n} of {total}")
    exact = {k: n * c / total for k, c in counts.items()}
    alloc = {k: int(v) for k, v in exact.items()}
    left = n - sum(alloc.values())
    for k in sorted(exact, key=lambda k: (-(exact[k] - alloc[k]), k))[:left]:
        alloc[k] += 1
    return alloc


def stratified_sample(
    questions: list[BirdQuestion], n: int, seed: int, exclude: frozenset[str] = GOLD_EXCLUDED
) -> list[BirdQuestion]:
    """n questions stratified by difficulty (proportional), seeded and order-independent.
    Returned sorted by question_id so the committed id list diffs cleanly."""
    pool = sorted((q for q in questions if q.id not in exclude), key=lambda q: q.question_id)
    by_diff: dict[str, list[BirdQuestion]] = {d: [] for d in DIFFICULTIES}
    for q in pool:
        by_diff[q.difficulty].append(q)
    alloc = allocate(Counter({d: len(v) for d, v in by_diff.items() if v}), n)
    chosen: list[BirdQuestion] = []
    for d, k in alloc.items():
        chosen += random.Random(f"{seed}:{d}").sample(by_diff[d], k)
    return sorted(chosen, key=lambda q: q.question_id)
