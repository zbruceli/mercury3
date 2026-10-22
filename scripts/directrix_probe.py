"""Grade the P1 directrix probe (configs/experiments/p1_directrix_probe.yaml). Offline.

Every answer is graded twice in the Docker sandbox, against the textbook directrix
c - (b^2+1)/(4a) and against the buggy MBPP reference c - (b^2+1)*4a, on MBPP+'s 105 inputs
for Mbpp/430. Prints one row per prompt variant and arm: budget-outs, output tokens, time,
and which formula the code implements. Grades go to results/analysis/<run>/directrix.jsonl.

    uv run python scripts/directrix_probe.py <run_id>
"""

from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

from dllm_eval.logging_store import RunStore
from dllm_eval.sandbox.executor import DockerExecutor, load_sandbox_config
from dllm_eval.usecases.codegen.datasets import CodeProblem, load_dataset_registry, load_problems
from dllm_eval.usecases.codegen.scoring import grading_job

TEXTBOOK = "def parabola_directrix(a, b, c):\n    return c - (b * b + 1) / (4 * a)\n"


def references() -> dict[str, CodeProblem]:
    mbpp = next(
        p for p in load_problems("mbpp+", load_dataset_registry()) if p.id.endswith("/Mbpp/430")
    )
    base = {
        "kind": "function",
        "prompt": mbpp.prompt,
        "entry_point": "parabola_directrix",
        "inputs": mbpp.inputs,
    }
    return {
        "mbpp": CodeProblem(
            id="probe/directrix-mbpp",
            dataset="probe",
            canonical_solution=mbpp.canonical_solution,
            atol=0.0,
            **base,
        ),
        "textbook": CodeProblem(
            id="probe/directrix-textbook",
            dataset="probe",
            canonical_solution=TEXTBOOK,
            atol=1e-6,
            **base,
        ),
    }


def main(run_id: str) -> None:
    store = RunStore(Path("results/runs") / run_id)
    recs = [r for r in store.latest_by_job().values() if r["status"] == "ok"]
    refs = references()
    jobs = []
    for r in recs:
        for name, prob in refs.items():
            job, _ = grading_job(f"{r['job_id']}|{name}", prob, r["result"]["content"])
            jobs.append(job)
    graded = {g["job_id"]: g for g in DockerExecutor(load_sandbox_config()).run(jobs)}

    out = Path("results/analysis") / run_id / "directrix.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in recs:
        res = r["result"]
        u = res["usage"]
        row = {
            "job_id": r["job_id"],
            "arm": r["arm"],
            "variant": r["item_id"].removeprefix("directrix-"),
            "finish": res["finish_reason"],
            "budget_out": res["finish_reason"] in ("length", "max_tokens"),
            "output_tokens": u.get("output_tokens"),
            "reasoning_tokens": u.get("reasoning_tokens"),
            "total_s": res["timing"]["t_done"],
            **{f"{n}_status": graded[f"{r['job_id']}|{n}"]["status"] for n in refs},
        }
        rows.append(row)
    out.write_text("".join(json.dumps(x) + "\n" for x in rows))

    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for x in rows:
        groups[(str(x["variant"]), str(x["arm"]))].append(x)
    print(
        "| Variant | Arm | n | Budget-out | Output tok p50 | Time p50 (s) | Textbook | MBPP ref |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for (variant, arm), xs in sorted(groups.items()):
        tok = st.median(int(x["output_tokens"] or 0) for x in xs)  # type: ignore[call-overload]
        sec = st.median(float(x["total_s"]) for x in xs)  # type: ignore[arg-type]
        bo = sum(bool(x["budget_out"]) for x in xs)
        tb = sum(x["textbook_status"] == "pass" for x in xs)
        mb = sum(x["mbpp_status"] == "pass" for x in xs)
        n = len(xs)
        cells = [variant, f"`{arm}`", n, f"{bo}/{n}", f"{tok:,.0f}", f"{sec:.1f}", f"{tb}/{n}"]
        print("| " + " | ".join(map(str, cells)) + f" | {mb}/{n} |")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main(sys.argv[1])
