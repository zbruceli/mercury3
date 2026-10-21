"""Tiny hand-written UC1 datasets in the real on-disk formats, for offline tests.

Every piece of code here is ours (never downloaded), so the host-side test executor may
run it.
"""

import base64
import gzip
import hashlib
import json
import pickle
import zlib
from pathlib import Path
from typing import Any

import yaml

HE = [
    {
        "task_id": "HumanEval/0",
        "prompt": 'from typing import List\n\n\ndef add_all(xs: List[int]) -> int:\n    """Sum a list."""\n',
        "entry_point": "add_all",
        "canonical_solution": "    return sum(xs)\n",
        "test": "",
        "contract": "",
        "base_input": [[[1, 2, 3]], [[]]],
        "atol": 0,
        "plus_input": [[[10, -10, 5]]],
    },
    {
        "task_id": "HumanEval/1",
        "prompt": 'def mean(xs: list) -> float:\n    """Average of xs."""\n',
        "entry_point": "mean",
        "canonical_solution": "    return sum(xs) / len(xs)\n",
        "test": "",
        "contract": "",
        "base_input": [[[1.0, 2.0]], [[0.1, 0.2, 0.3]]],
        "atol": 0,
        "plus_input": [],
    },
]

MBPP = [
    {
        "task_id": "Mbpp/2",
        "prompt": '"""\nWrite a function to find the shared elements.\nassert set(shared((1, 2), (2, 3))) == {2}\n"""\n',
        "entry_point": "shared",
        "canonical_solution": "def shared(a, b):\n    return tuple(sorted(set(a) & set(b)))\n",
        "base_input": [[[1, 2], [2, 3]], [[5], [6]]],
        "atol": 0,
        "plus_input": [[[1, 2, 3], [3, 2]]],
        "contract": "",
        "assertion": "",
    },
]


def _private(tests: list[dict[str, str]]) -> str:
    return base64.b64encode(zlib.compress(pickle.dumps(json.dumps(tests)))).decode()


LCB = [
    {
        "question_title": "Double",
        "question_content": "Read an integer N and print 2N.",
        "platform": "atcoder",
        "question_id": "abc001_a",
        "contest_id": "abc001",
        "contest_date": "2025-01-04T00:00:00",
        "starter_code": "",
        "difficulty": "easy",
        "public_test_cases": json.dumps([{"input": "3\n", "output": "6\n", "testtype": "stdin"}]),
        "private_test_cases": _private([{"input": "10\n", "output": "20\n", "testtype": "stdin"}]),
        "metadata": "{}",
    },
    {
        "question_title": "Two Sum Count",
        "question_content": "Return how many pairs sum to target.",
        "platform": "leetcode",
        "question_id": "9001",
        "contest_id": "w1",
        "contest_date": "2025-02-01T00:00:00",
        "starter_code": "class Solution:\n    def countPairs(self, nums: List[int], target: int) -> int:\n        ",
        "difficulty": "medium",
        "public_test_cases": json.dumps(
            [{"input": "[1, 2, 3]\n4", "output": "1", "testtype": "functional"}]
        ),
        "private_test_cases": _private(
            [{"input": "[1, 1, 1]\n2", "output": "3", "testtype": "functional"}]
        ),
        "metadata": "{}",
    },
    {
        "question_title": "Too Hard",
        "question_content": "Filtered out by difficulty.",
        "platform": "atcoder",
        "question_id": "abc001_g",
        "contest_id": "abc001",
        "contest_date": "2025-01-04T00:00:00",
        "starter_code": "",
        "difficulty": "hard",
        "public_test_cases": json.dumps([{"input": "1\n", "output": "1\n", "testtype": "stdin"}]),
        "private_test_cases": _private([]),
        "metadata": "{}",
    },
]


def _write(path: Path, rows: list[dict[str, Any]], gz: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r) + "\n" for r in rows).encode()
    path.write_bytes(gzip.compress(data) if gz else data)


def make_datasets(root: Path) -> Path:
    """Write the three fixture datasets plus a matching registry under root; return the
    registry path relative to root."""
    files = {
        "humaneval+": (Path("data/raw/he/he.jsonl.gz"), HE, True),
        "mbpp+": (Path("data/raw/mbpp/mbpp.jsonl.gz"), MBPP, True),
        "lcb-v6": (Path("data/raw/lcb/test6.jsonl"), LCB, False),
    }
    reg: dict[str, Any] = {"datasets": {}}
    for name, (rel, rows, gz) in files.items():
        _write(root / rel, rows, gz)
        blob = (root / rel).read_bytes()
        reg["datasets"][name] = {
            "version": "fixture",
            "url": "https://example.invalid/" + rel.name,
            "path": str(rel),
            "bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "problems": len(rows),
        }
    reg_path = Path("configs/datasets.yaml")
    (root / reg_path).parent.mkdir(parents=True, exist_ok=True)
    (root / reg_path).write_text(yaml.safe_dump(reg))
    return reg_path
