"""Grading harness that runs INSIDE the sandbox container (F4.3). Standard library only.

Protocol: a JSON object {"jobs": [...]} on stdin, {"results": [...]} on stdout.

Every candidate (and every canonical solution) runs in its own fresh subprocess with a
time limit. Results come back as tagged JSON (never pickle), and comparison happens in
this process, which never executes candidate code. Threat model: the container protects
the host; grading assumes candidates are not actively adversarial (same as EvalPlus).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from typing import Any

# Some HumanEval+ outputs are integers with far more than 4,300 digits.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)

PY = sys.executable
# Address-space cap per solution process: a runaway candidate gets MemoryError instead of
# getting the whole container OOM-killed (which would lose every result in the batch).
MEMORY_LIMIT_BYTES = 768 * 1024 * 1024
FN_TIMEOUT_CAP_S = 60.0
CANONICAL_TIMEOUT_S = 60.0
TEST_TIMEOUT_S = 6.0  # per LiveCodeBench test, as in LCB's grader
JOB_BUDGET_S = 120.0  # total wall time for one LCB job's tests

# LiveCodeBench prepends a similar import block to every solution.
LCB_IMPORTS = (
    "from typing import *\nfrom functools import *\nfrom collections import *\n"
    "from itertools import *\nfrom heapq import *\nfrom bisect import *\nfrom string import *\n"
    "from operator import *\nfrom math import *\nimport math, re, sys, string, random, heapq, "
    "bisect, itertools, collections, functools, operator, copy\nsys.setrecursionlimit(1 << 25)\n"
)

# Runs one solution over a list of inputs; writes tagged-JSON outputs. Executed as its own
# process so a crashing or looping candidate cannot touch the grader.
FN_RUNNER = r"""
import copy, hashlib, json, math, sys
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)
BIG_JSON = 1_000_000  # outputs larger than this are compared by hash

def _key(v):
    return json.dumps(v, sort_keys=True)

def enc(x):
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        if math.isnan(x):
            return {"t": "float", "v": "nan"}
        if math.isinf(x):
            return {"t": "float", "v": "inf" if x > 0 else "-inf"}
        return x
    if isinstance(x, list):
        return [enc(i) for i in x]
    if isinstance(x, tuple):
        return {"t": "tuple", "v": [enc(i) for i in x]}
    if isinstance(x, (set, frozenset)):  # sorted so equal sets encode identically
        return {"t": "set", "v": sorted((enc(i) for i in x), key=_key)}
    if isinstance(x, dict):
        return {"t": "dict", "v": sorted(([enc(k), enc(v)] for k, v in x.items()), key=_key)}
    if isinstance(x, bytes):
        return {"t": "bytes", "v": x.hex()}
    return {"t": "repr", "v": repr(x)}

# Port of evalplus/data/mbpp.py mbpp_deserialize_inputs (MIT): MBPP+ stores tuples, sets
# and complex numbers as JSON lists/strings, and EvalPlus converts them back per task.
def mbpp_deserialize(task_id, inputs):
    if task_id in [2, 116, 132, 143, 222, 261, 273, 394, 399, 421, 424, 429, 470, 560, 579,
                   596, 616, 630, 726, 740, 744, 809]:
        return [[tuple(lst) for lst in inp] for inp in inputs]
    if task_id in [63, 64, 70, 94, 120, 237, 272, 299, 400, 409, 417, 438, 473, 614, 780]:
        return [[[tuple(lst) for lst in lst_lst] for lst_lst in inp] for inp in inputs]
    if task_id in [75, 413, 444, 753]:
        return [[[tuple(lst) for lst in inp[0]]] + [inp[1]] for inp in inputs]
    if task_id in (106, 750):
        return [[inp[0]] + [tuple(inp[1])] for inp in inputs]
    if task_id == 115:
        return [[[set(item) if isinstance(item, list) and len(item) else {} for item in inp[0]]]
                for inp in inputs]
    if task_id == 124:
        return [(float(inp[0]), complex(inp[1])) for inp in inputs]
    if task_id in [250, 405, 446, 617, 720, 763, 808]:
        return [[tuple(inp[0])] + [inp[1]] for inp in inputs]
    if task_id in [259, 401, 445]:
        out = [[[tuple(lst) for lst in lst_lst] for lst_lst in inp] for inp in inputs]
        return [[tuple(lst) for lst in inp] for inp in out]
    if task_id == 278:
        out = [[[tuple(i) if isinstance(i, list) else i for i in inp[0]]] for inp in inputs]
        return [[tuple(lst) for lst in inp] for inp in out]
    if task_id == 307:
        return [[tuple(inp[0])] + [inp[1], inp[2]] for inp in inputs]
    if task_id == 722:
        return [[{k: tuple(v) for k, v in inp[0].items()}] + inp[1:] for inp in inputs]
    if task_id == 252:
        return [[complex(inp[0])] for inp in inputs]
    if task_id in [580, 615, 791]:
        def to_tuple(x):
            return tuple(to_tuple(i) for i in x) if isinstance(x, list) else x
        return [to_tuple(inp) for inp in inputs]
    return inputs

spec = json.load(open(sys.argv[1]))
ns = {"__name__": "__candidate__"}
try:
    exec(compile(open(spec["code_path"]).read(), "solution.py", "exec"), ns)
    fn = ns[spec["entry_point"]]
except BaseException as e:
    json.dump({"load_error": type(e).__name__ + ": " + str(e)[:200]}, open(spec["out_path"], "w"))
    sys.exit(0)
inputs = json.load(open(spec["inputs_path"]))
if spec.get("mbpp_task") is not None:
    inputs = mbpp_deserialize(spec["mbpp_task"], inputs)
out = []
for args in inputs:
    try:
        v = enc(fn(*copy.deepcopy(args)))
        blob = _key(v)
        if len(blob) > BIG_JSON:
            v = {"t": "hash", "v": hashlib.sha256(blob.encode()).hexdigest(), "n": len(blob)}
        out.append({"ok": True, "v": v})
    except BaseException as e:
        out.append({"ok": False, "err": type(e).__name__})
json.dump({"outputs": out}, open(spec["out_path"], "w"))
"""

LEETCODE_RUNNER = r"""
import json, sys
src = open(sys.argv[1]).read()
ns = {"__name__": "__candidate__"}
exec(compile(src, "solution.py", "exec"), ns)
args = [json.loads(line) for line in sys.stdin.read().split("\n") if line.strip()]
result = getattr(ns["Solution"](), sys.argv[2])(*args)
print(json.dumps(result))
"""


def dec(x: Any) -> Any:
    if isinstance(x, list):
        return [dec(i) for i in x]
    if isinstance(x, dict) and "t" in x:
        t, v = x["t"], x["v"]
        if t == "float":
            return float(v)
        if t == "tuple":
            return tuple(dec(i) for i in v)
        if t == "set":
            return {dec(i) for i in v}
        if t == "dict":
            return {dec(k): dec(val) for k, val in v}
        if t == "bytes":
            return bytes.fromhex(v)
        if t == "hash":
            return ("__hash__", v, x["n"])
        return ("__repr__", v)
    return x


def _is_floats(x: Any) -> bool:
    if isinstance(x, float):
        return True
    if isinstance(x, (list, tuple)) and x:
        return all(isinstance(i, float) for i in x)
    return False


def _allclose(out: Any, exp: Any, atol: float, rtol: float = 1e-07) -> bool:
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        if isinstance(out, bool) or not isinstance(out, (int, float)):
            return False
        if math.isnan(exp) or math.isnan(out):
            return math.isnan(exp) and math.isnan(out)
        return abs(out - exp) <= atol + rtol * abs(exp)
    if isinstance(exp, (list, tuple)) and isinstance(out, (list, tuple)):
        return len(out) == len(exp) and all(
            _allclose(o, e, atol) for o, e in zip(out, exp, strict=True)
        )
    return bool(out == exp)


# --- EvalPlus special oracles (evalplus/eval/_special_oracle.py, MIT) -----------------
MBPP_OUTPUT_NOT_NONE_TASKS = ["check_str", "text_match_three", "text_starta_endb"]
MBPP_OUTPUT_SET_EQ_TASKS = [
    "similar_elements", "find_char_long", "common_in_nested_lists", "extract_singly",
    "larg_nnum", "intersection_array", "find_dissimilar", "Diff",
]  # fmt: skip


def _surface_area(base_edge: float, height: float) -> float:  # Mbpp/581
    slant_height = math.sqrt((base_edge / 2) ** 2 + height**2)
    return round(base_edge**2 + 4 * (base_edge * slant_height) / 2)


def _digit_distance_nums(num1: int, num2: int) -> int:  # Mbpp/558
    a, b = str(num1), str(num2)
    width = max(len(a), len(b))
    return sum(abs(int(x) - int(y)) for x, y in zip(a.zfill(width), b.zfill(width), strict=True))


def _poly(xs: list[float], x: float) -> float:  # HumanEval/32
    return sum(coeff * math.pow(x, i) for i, coeff in enumerate(xs))


def evalplus_check(
    dataset: str, entry_point: str, inp: list[Any], exp: Any, out: Any, atol: float
) -> tuple[bool, float]:
    """One input, EvalPlus semantics (evalplus/eval/__init__.py, unsafe_execute). Returns
    (passed, atol for the next input): EvalPlus raises atol to 1e-6 on a float output and
    keeps it for the rest of the problem."""
    try:
        exact = out == exp
        if dataset == "mbpp+":
            if entry_point == "are_equivalent":
                exact = True
            elif entry_point == "sum_div":
                exact = exact or out == 0
            elif entry_point == "surface_Area":
                exact = exact or abs(out - _surface_area(*inp)) <= atol
            elif entry_point == "digit_distance_nums":
                exact = exact or out == _digit_distance_nums(*inp)
            elif entry_point in MBPP_OUTPUT_SET_EQ_TASKS:
                exact = set(out) == set(exp)
            elif entry_point in MBPP_OUTPUT_NOT_NONE_TASKS:
                not_none = exp is not None
                exact = out == not_none if isinstance(out, bool) else not_none == (out is not None)
        if dataset == "humaneval+" and entry_point == "find_zero":
            return abs(_poly(inp[0], out)) <= atol, atol  # inp == [xs]
        if atol == 0 and _is_floats(exp):
            atol = 1e-6
        if not exact and atol != 0:
            return type(out) is type(exp) and _allclose(out, exp, atol), atol
        return bool(exact), atol
    except Exception:
        return False, atol


def outputs_equal(exp: Any, out: Any, atol: float) -> bool:
    """EvalPlus semantics: exact equality, except floats (atol defaults to 1e-6 for them)."""
    if atol == 0 and _is_floats(exp):
        atol = 1e-6
    if out == exp:
        return True
    if atol != 0:
        return type(out) is type(exp) and _allclose(out, exp, atol)
    return False


def lcb_text_equal(out: str, exp: str) -> bool:
    """LiveCodeBench stdin comparison: per line, whitespace-split tokens, numbers as Decimal."""
    a = [line.strip() for line in out.strip().splitlines()]
    b = [line.strip() for line in exp.strip().splitlines()]
    if a == b:
        return True
    if len(a) != len(b):
        return False
    for la, lb in zip(a, b, strict=True):
        if la == lb:
            continue
        ta, tb = la.split(), lb.split()
        if len(ta) != len(tb):
            return False
        for x, y in zip(ta, tb, strict=True):
            if x == y:
                continue
            try:
                if Decimal(x) != Decimal(y):
                    return False
            except InvalidOperation:
                return False
    return True


def _limit_memory() -> None:  # runs in the child just before exec
    # Enforced on Linux (the sandbox). macOS does not support RLIMIT_AS, so host-side
    # tests skip it; there the container's own memory limit is the only cap.
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))


def _run_fn(
    work: str,
    tag: str,
    code: str,
    entry_point: str,
    inputs_path: str,
    timeout: float,
    mbpp_task: int | None = None,
) -> tuple[dict[str, Any], float]:
    code_path = os.path.join(work, f"{tag}.py")
    out_path = os.path.join(work, f"{tag}.out.json")
    spec_path = os.path.join(work, f"{tag}.spec.json")
    runner_path = os.path.join(work, "fn_runner.py")
    with open(code_path, "w") as f:
        f.write(code)
    with open(runner_path, "w") as f:
        f.write(FN_RUNNER)
    with open(spec_path, "w") as f:
        json.dump(
            {
                "code_path": code_path,
                "entry_point": entry_point,
                "inputs_path": inputs_path,
                "out_path": out_path,
                "mbpp_task": mbpp_task,
            },
            f,
        )
    t0 = time.monotonic()
    try:
        subprocess.run(
            [PY, "-I", "-B", runner_path, spec_path],
            cwd=work,
            timeout=timeout,
            capture_output=True,
            check=False,
            preexec_fn=_limit_memory,  # safe: the harness is single-threaded
        )
    except subprocess.TimeoutExpired:
        return {"timeout": True}, time.monotonic() - t0
    elapsed = time.monotonic() - t0
    try:
        with open(out_path) as f:
            result: dict[str, Any] = json.load(f)
        return result, elapsed
    except (OSError, ValueError):
        return {"load_error": "no output (crashed)"}, elapsed


# Reference outputs for the most recent problem in this container. Scoring sorts jobs by
# problem, so a batch's consecutive candidates for one problem run the reference once.
# One entry only, to keep memory bounded.
_CANON_CACHE: dict[str, tuple[dict[str, Any], float]] = {}


def grade_function(job: dict[str, Any], work: str) -> dict[str, Any]:
    inputs_path = os.path.join(work, "inputs.json")
    with open(inputs_path, "w") as f:
        json.dump(job["inputs"], f)
    total = len(job["inputs"])
    mbpp_task = job.get("mbpp_task")
    key = job.get("cache_key")
    if key is not None and key in _CANON_CACHE:
        canon, canon_s = _CANON_CACHE[key]
    else:
        canon, canon_s = _run_fn(
            work,
            "canonical",
            job["canonical"],
            job["entry_point"],
            inputs_path,
            CANONICAL_TIMEOUT_S,
            mbpp_task,
        )
        if key is not None and "outputs" in canon:
            _CANON_CACHE.clear()
            _CANON_CACHE[key] = (canon, canon_s)
    if "outputs" not in canon:
        return {
            "status": "harness_error",
            "passed": 0,
            "total": total,
            "detail": f"canonical failed: {canon}",
        }
    # Candidate gets 5x the canonical runtime (min 10 s, max 60 s); tests may override.
    limit = float(job.get("time_limit_s") or min(FN_TIMEOUT_CAP_S, max(10.0, 5 * canon_s + 2)))
    cand, _ = _run_fn(
        work, "candidate", job["candidate"], job["entry_point"], inputs_path, limit, mbpp_task
    )
    if cand.get("timeout"):
        return {
            "status": "timeout",
            "passed": 0,
            "total": total,
            "detail": f"exceeded {limit:.0f}s",
        }
    if "load_error" in cand:
        return {"status": "error", "passed": 0, "total": total, "detail": cand["load_error"]}
    passed = 0
    first_fail = None
    atol = float(job.get("atol") or 0)
    dataset, entry_point = job.get("dataset", ""), job["entry_point"]
    dropped = 0
    ref_atol = atol
    for i, (e, o) in enumerate(zip(canon["outputs"], cand["outputs"], strict=True)):
        # An input is a test only if the reference passes it: it must not raise (or hit the
        # memory cap), and must satisfy its own oracle (e.g. HumanEval/32 find_zero, where
        # float error on huge coefficients can exceed atol). Same rule for every model.
        valid = e["ok"]
        if valid:
            exp_v = dec(e["v"])
            valid, ref_atol = evalplus_check(
                dataset, entry_point, job["inputs"][i], exp_v, exp_v, ref_atol
            )
        if not valid:
            total -= 1
            dropped += 1
            continue
        ok = False
        if o["ok"]:
            ok, atol = evalplus_check(
                dataset, entry_point, job["inputs"][i], dec(e["v"]), dec(o["v"]), atol
            )
        if ok:
            passed += 1
        elif first_fail is None:
            first_fail = i if o["ok"] else f"{i}: raised {o['err']}"
    if total == 0:  # every input dropped: never let that count as a pass
        return {"status": "harness_error", "passed": 0, "total": 0, "dropped_inputs": dropped,
                "detail": f"no valid inputs ({dropped} dropped: reference fails them)"}  # fmt: skip
    status = "pass" if passed == total else "fail"
    return {
        "status": status,
        "passed": passed,
        "total": total,
        "detail": "; ".join(
            x
            for x in (
                None if first_fail is None else f"first failing input {first_fail}",
                f"{dropped} inputs dropped (reference fails them)" if dropped else None,
            )
            if x
        )
        or None,
        "dropped_inputs": dropped,
    }


def grade_tests(job: dict[str, Any], work: str) -> dict[str, Any]:
    kind = job["kind"]
    code = LCB_IMPORTS + "\n" + job["candidate"]
    prog = os.path.join(work, "solution.py")
    with open(prog, "w") as f:
        f.write(code)
    cmd = [PY, "-I", "-B", prog]
    if kind == "leetcode":
        runner = os.path.join(work, "lc_runner.py")
        with open(runner, "w") as f:
            f.write(LEETCODE_RUNNER)
        cmd = [PY, "-I", "-B", runner, prog, job["entry_point"]]
    tests = job["tests"]
    per_test = float(job.get("time_limit_s") or TEST_TIMEOUT_S)
    started = time.monotonic()
    for i, t in enumerate(tests):
        if time.monotonic() - started > JOB_BUDGET_S:
            return {
                "status": "timeout",
                "passed": i,
                "total": len(tests),
                "detail": "job time budget exceeded",
            }
        try:
            proc = subprocess.run(
                cmd,
                cwd=work,
                input=t["input"],
                capture_output=True,
                text=True,
                timeout=per_test,
                check=False,
                preexec_fn=_limit_memory,  # safe: the harness is single-threaded
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "passed": i,
                "total": len(tests),
                "detail": f"test {i} exceeded {per_test:.0f}s",
            }
        if proc.returncode != 0:
            err = (proc.stderr.strip().splitlines() or ["?"])[-1][:200]
            return {  # non-zero exit = the program crashed (wrong output is "fail")
                "status": "error",
                "passed": i,
                "total": len(tests),
                "detail": f"test {i}: {err}",
            }
        if kind == "leetcode":
            try:
                ok = json.loads(proc.stdout) == json.loads(t["output"])
            except ValueError:
                ok = False
        else:
            ok = lcb_text_equal(proc.stdout, t["output"])
        if not ok:  # LCB stops at the first failing test
            return {
                "status": "fail",
                "passed": i,
                "total": len(tests),
                "detail": f"test {i}: wrong answer",
            }
    return {"status": "pass", "passed": len(tests), "total": len(tests), "detail": None}


def grade(job: dict[str, Any]) -> dict[str, Any]:
    t0 = time.monotonic()
    if not job.get("candidate", "").strip():
        res: dict[str, Any] = {
            "status": "no_code",
            "passed": 0,
            "total": 0,
            "detail": "nothing extracted",
        }
    else:
        with tempfile.TemporaryDirectory() as work:
            try:
                res = (
                    grade_function(job, work)
                    if job["kind"] == "function"
                    else grade_tests(job, work)
                )
            except Exception as e:  # a grader bug must not take down the batch
                res = {
                    "status": "harness_error",
                    "passed": 0,
                    "total": 0,
                    "detail": f"{type(e).__name__}: {e}"[:300],
                }
    res["job_id"] = job["job_id"]
    res["grade_s"] = round(time.monotonic() - t0, 3)
    return res


def main() -> None:
    req = json.load(sys.stdin)
    json.dump({"results": [grade(j) for j in req["jobs"]]}, sys.stdout)


if __name__ == "__main__":
    main()
