"""M4 offline tests: failure tagging (F6.6), cross-use-case figures (F6.4), and the report
generator (F6.7). Run directories are synthetic; nothing calls an API."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from typer.testing import CliRunner

from dllm_eval.analysis import assets, diffusion, failures, report, uc2
from dllm_eval.analysis.charts import _crosses, _place_labels, add_footnote, add_header
from dllm_eval.cli import app
from dllm_eval.logging_store import RunStore

# --- synthetic records ----------------------------------------------------------------


def uc1_rec(job: str, *, content: str = "```python\nx = 1\n```", finish: str = "stop",
            status: str = "ok", out: int = 50, reasoning: int = 0, total: float = 1.0,
            ttft: float = 0.5) -> dict[str, Any]:  # fmt: skip
    arm, rest = job.split("/", 1)
    item, repeat = rest.rsplit("/", 1)
    return {
        "job_id": job, "arm": arm, "model": arm.split(":")[0], "item_id": item,
        "repeat": int(repeat), "warmup": False, "status": status,
        "started_at": "2026-10-21T04:00:00+00:00", "wall_s": total,
        "request": {"messages": [{"role": "user", "content": f"Solve {item}"}]},
        "result": {
            "content": content, "tool_calls": [], "finish_reason": finish, "retries": 0,
            "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": out,
                      "reasoning_tokens": reasoning},
            "timing": {"t_first_content": ttft, "t_last_content": total, "t_done": total},
        },
        "cost_usd": 0.001, "cost_usd_regular": 0.002 if arm.startswith("mercury") else 0.001,
    }  # fmt: skip


def turn(index: int, calls: list[tuple[str, str]], *, content: str = "", nudged: bool = False,
         finish: str = "tool_calls", t: float = 0.4) -> dict[str, Any]:  # fmt: skip
    return {
        "index": index, "n_messages": 2 + index, "t_start": index * t, "nudged": nudged,
        "result": {"content": content, "finish_reason": finish, "retries": 0,
                   "usage": {"input_tokens": 200, "cached_input_tokens": 0, "output_tokens": 30,
                             "reasoning_tokens": 0},
                   "timing": {"t_first_content": t / 2, "t_done": t}},
        "tools": [{"tool_call_id": f"c{index}{k}", "name": n, "validity": "valid", "detail": "",
                   "status": st, "text": "", "exec_s": 0.001}
                  for k, (n, st) in enumerate(calls)],
    }  # fmt: skip


def uc2_rec(job: str, outcome: str, turns: list[dict[str, Any]],
            sqls: list[str] = (), answer: str | None = "SELECT 1") -> dict[str, Any]:  # type: ignore[assignment]  # fmt: skip
    arm, rest = job.split("/", 1)
    item, repeat = rest.rsplit("/", 1)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": f"Q {item}"}]
    msgs += [{"role": "assistant", "content": None,
              "tool_calls": [{"id": "x", "name": "run_sql", "arguments": json.dumps({"query": q})}]}
             for q in sqls]  # fmt: skip
    return {
        "job_id": job, "arm": arm, "model": arm.split(":")[0], "item_id": item,
        "repeat": int(repeat), "warmup": False, "status": "ok",
        "started_at": "2026-10-21T04:00:00+00:00", "wall_s": 0.4 * len(turns),
        "request": {"messages": msgs[:2]},
        "task": {"outcome": outcome, "answer_sql": answer if outcome == "submitted" else None,
                 "wall_s": 0.4 * len(turns), "model_s": 0.4 * len(turns), "tool_s": 0.01,
                 "turns": turns, "messages": msgs},
        "cost_usd": 0.0005, "cost_usd_regular": 0.001 if arm.startswith("mercury") else 0.0005,
    }  # fmt: skip


def uc2_score(job: str, ex: bool, pred_status: str = "ok") -> dict[str, Any]:
    arm, rest = job.split("/", 1)
    item, repeat = rest.rsplit("/", 1)
    return {"job_id": job, "arm": arm, "item_id": item, "repeat": int(repeat),
            "difficulty": "simple", "status": "ok", "pred_status": pred_status, "ex": ex,
            "ex_bird": ex}  # fmt: skip


# --- failure tagging ------------------------------------------------------------------


def test_uc1_tags() -> None:
    sc = {"status": "fail"}
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0"), {"status": "pass"}) is None
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0"), sc) == "wrong_answer"
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0", content="", finish="length"),
                            {"status": "no_code"}) == "budget_out_hidden"  # fmt: skip
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0", content="We need…", finish="length"),
                            {"status": "no_code"}) == "think_aloud_runaway"  # fmt: skip
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0"), {"status": "timeout"}) == "timeout"
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0"), {"status": "error"}) == "runtime_error"
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0", status="error"), None) == "api_error"
    assert failures.uc1_tag(uc1_rec("m:a/p/1/0"), {"status": "harness_error"}) is None


def test_uc2_tags() -> None:
    sub = [turn(0, [("list_tables", "ok")]), turn(1, [("submit_answer", "answer")])]
    j = "m:a/bird/1/0"
    assert failures.uc2_tag(uc2_rec(j, "submitted", sub), uc2_score(j, True)) is None
    assert failures.uc2_tag(uc2_rec(j, "submitted", sub), uc2_score(j, False)) == "wrong_result"
    blind = [turn(0, [("submit_answer", "answer")])]
    assert failures.uc2_tag(uc2_rec(j, "submitted", blind), uc2_score(j, False)) == "blind_wrong"
    assert failures.uc2_tag(uc2_rec(j, "submitted", sub), uc2_score(j, False, "sql_error")) == (
        "answer_sql_error"
    )
    cap = [turn(i, [("run_sql", "ok")]) for i in range(8)]
    assert failures.uc2_tag(uc2_rec(j, "turn_cap", cap, ["SELECT a", "select  A"]), None) == (
        "turn_cap_repeat"  # whitespace and case don't make a query new
    )
    assert failures.uc2_tag(uc2_rec(j, "turn_cap", cap, ["SELECT a", "SELECT b"]), None) == (
        "turn_cap_exploring"
    )
    prose = [*cap[:6], turn(6, [], nudged=True), turn(7, [], nudged=True)]
    assert failures.uc2_tag(uc2_rec(j, "turn_cap", prose), None) == "turn_cap_prose"
    assert failures.uc2_tag(uc2_rec(j, "budget_out", sub), None) == "budget_out"
    assert failures.uc2_tag(uc2_rec(j, "invalid_answer", sub), None) == "schema_violation"
    assert failures.uc2_tag(uc2_rec(j, "empty_response", sub), None) == "empty_response"
    assert failures.uc2_tag(uc2_rec(j, "error", sub), None) == "api_error"


def test_tag_run_manual_override_and_aggregate() -> None:
    recs = [uc1_rec("m:a/p/1/0"), uc1_rec("m:a/p/2/0"), uc1_rec("m:b/p/1/0")]
    scores = [
        {"job_id": "m:a/p/1/0", "status": "fail"},
        {"job_id": "m:a/p/2/0", "status": "pass"},
        {"job_id": "m:b/p/1/0", "status": "timeout"},
    ]
    manual = [{"run_id": "r", "job_id": "m:b/p/1/0", "tag": "wrong_answer"},
              {"run_id": "other", "job_id": "zzz", "tag": "timeout"}]  # fmt: skip
    fs = failures.tag_run("r", recs, scores, kind="uc1", manual=manual)
    assert {(f.job_id, f.tag, f.source) for f in fs} == {
        ("m:a/p/1/0", "wrong_answer", "rule"),
        ("m:b/p/1/0", "wrong_answer", "manual"),
    }
    rows = {r["arm"]: r for r in failures.aggregate(fs, {"m:a": 2, "m:b": 1}, failures.UC1_TAGS)}
    assert rows["m:a"]["failures"] == 1 and rows["m:b"]["wrong_answer"] == 1
    md = failures.aggregate_markdown(list(rows.values()), failures.UC1_TAGS)
    assert "`wrong_answer`" in md and "`timeout`" not in md  # unused tags are dropped
    with pytest.raises(ValueError, match="didn't fail"):
        failures.tag_run("r", recs, scores, kind="uc1",
                         manual=[{"run_id": "r", "job_id": "m:a/p/2/0", "tag": "timeout"}])  # fmt: skip
    with pytest.raises(ValueError, match="unknown uc1 tag"):
        failures.tag_run("r", recs, scores, kind="uc1",
                         manual=[{"run_id": "r", "job_id": "m:a/p/1/0", "tag": "turn_cap_prose"}])  # fmt: skip
    with pytest.raises(ValueError, match="not in r"):
        failures.tag_run("r", recs, scores, kind="uc1",
                         manual=[{"run_id": "r", "job_id": "nope", "tag": "timeout"}])  # fmt: skip


def test_excerpt_and_evidence() -> None:
    assert failures.excerpt("short") == "short"
    long = "a" * 2000 + "MIDDLE" + "z" * 2000
    ex = failures.excerpt(long, 10, 5)
    assert ex.startswith("a" * 10) and ex.endswith("z" * 5) and "MIDDLE" not in ex
    assert "characters omitted" in ex
    ev = failures.uc1_evidence(uc1_rec("m:a/p/1/0", content="", finish="length"),
                               {"status": "no_code", "passed": 0, "total": 0})  # fmt: skip
    assert ev["text"] == "(no visible text)" and ("finish", "length") in ev["facts"]
    ev2 = failures.uc2_evidence(
        uc2_rec("m:a/bird/1/0", "turn_cap", [turn(0, [], content="hi " * 100, nudged=True)]), None
    )
    assert "nudged" in ev2["steps"][0] and "…" in ev2["steps"][0]


def test_read_manual(tmp_path: Path) -> None:
    assert failures.read_manual(tmp_path / "missing.csv") == []
    p = tmp_path / "t.csv"
    p.write_text('run_id,job_id,tag,gallery,title,note\nr,j,timeout,1,T,"a, b"\n')
    assert failures.read_manual(p)[0]["note"] == "a, b"
    p.write_text("run_id,job_id,tag,gallery,title,note\nr,,timeout,1,T,n\n")
    with pytest.raises(ValueError, match="job_id"):
        failures.read_manual(p)


# --- figures and helpers --------------------------------------------------------------


def test_paired_ex_delta() -> None:
    scores = [uc2_score(f"a:x/bird/{q}/0", True) for q in range(4)] + [
        uc2_score(f"ref:y/bird/{q}/0", q < 2) for q in range(4)
    ]
    d, lo, hi = uc2.paired_ex_delta(scores, "a:x", "ref:y", seed=1)
    assert d == pytest.approx(0.5) and lo <= d <= hi
    with pytest.raises(ValueError, match="no shared"):
        uc2.paired_ex_delta(scores, "a:x", "missing:z")


def test_cost_points_and_cdf_table() -> None:
    calls = pd.DataFrame([
        {"arm": "mercury-3:low", "cost_usd": 0.01, "cost_usd_regular": 0.02},
        {"arm": "mercury-3:low", "cost_usd": 0.01, "cost_usd_regular": 0.02},
        {"arm": "gpt-6-luna:none", "cost_usd": 0.03, "cost_usd_regular": 0.03},
    ])  # fmt: skip
    scores = [
        {"arm": "mercury-3:low", "status": "pass"},
        {"arm": "mercury-3:low", "status": "fail"},
    ]
    pts = {p.arm: p for p in assets.uc1_cost_points(calls, scores)}
    m = pts["mercury-3:low"]
    assert (m.successes, m.jobs) == (1, 2)
    assert m.per_1k == pytest.approx(20.0) and m.per_1k_regular == pytest.approx(40.0)
    assert pts["gpt-6-luna:none"].successes == 0  # no successes: cost per success undefined
    u2 = assets.uc2_cost_points([{"arm": "a:b", "correct": 2, "tasks": 4, "cost_usd": 0.01,
                                  "cost_usd_regular": 0.01}])  # fmt: skip
    assert u2[0].per_1k == pytest.approx(5.0) and u2[0].success_rate == 0.5
    t = assets.cdf_table({"uc1": {"a:b": [1.0, 2.0, 3.0], "c:d": []}})
    assert set(t["arm"]) == {"a:b"} and t["seconds"].max() == 3.0 and t["n"].iloc[0] == 3


def test_label_helpers() -> None:
    import matplotlib.pyplot as plt

    assert _crosses((0, 0), (2, 2), (0, 2), (2, 0))
    assert not _crosses((0, 0), (1, 1), (2, 0), (3, 1))
    fig, ax = plt.subplots(figsize=(6, 4))
    top = add_header(fig, "Title", "subtitle " * 40, wrap=60)
    bottom = add_footnote(fig, "foot " * 80, wrap=60)
    assert 0 < bottom < top < 1
    ax.set_xscale("log")
    ax.set_xlim(0.1, 10)
    ax.set_ylim(0, 100)
    labels = [("A · low", 1.0, 50.0), ("A · high", 1.05, 49.0), ("B", 3.0, 80.0)]
    _place_labels(fig, ax, labels, bars=[(1.0, 40, 60)], lines=[[(1.0, 50.0), (1.05, 49.0)]])
    texts = [t for t in ax.texts if t.get_text() in {"A · low", "A · high", "B"}]
    assert len(texts) == 3
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    boxes = [t.get_window_extent(r) for t in texts]
    assert not boxes[0].overlaps(boxes[1])
    plt.close(fig)


# --- report end to end on synthetic runs ----------------------------------------------

UC1_ARMS = ["mercury-3:instant", "mercury-3:low", "mercury-3:medium", "mercury-3:high",
            "gpt-oss-120b-cerebras:low", "gpt-oss-120b-cerebras:medium",
            "claude-haiku-5-5:thinking-off", "claude-haiku-5-5:medium", "gpt-6-luna:none",
            "gpt-6-luna:medium"]  # fmt: skip
UC2_ARMS = [a for a in UC1_ARMS if a != "gpt-6-luna:medium"]
STRICT_ARMS = ["mercury-3:instant", "claude-haiku-5-5:thinking-off", "gpt-6-luna:none"]


def write_run(runs: Path, run_id: str, arms: list[str], records: list[dict[str, Any]],
              kind: str) -> None:  # fmt: skip
    models = {a.split(":")[0] for a in arms}
    store = RunStore(runs / run_id)
    store.create({
        "run_id": run_id, "created_at": "2026-10-21T04:00:00+00:00",
        "git": {"sha": "abcdef1234567"},
        "experiment": {"seed": 1, "workload": {"kind": kind},
                       "arms": [{"model": a.split(":")[0], "name": a.split(":")[1]} for a in arms]},
        "models": {m: {"model_id": m, "verified_on": "2026-10-20"} for m in models},
    })  # fmt: skip
    for r in records:
        store.append_call(r)


def build_fixture(tmp: Path) -> tuple[report.ReportConfig, Path, Path]:
    runs, analysis = tmp / "runs", tmp / "analysis"
    uc1, uc1s = [], []
    for arm in UC1_ARMS:
        for p in range(3):
            j = f"{arm}/he/{p}/0"
            runaway = arm == "mercury-3:high" and p == 2
            uc1.append(uc1_rec(j, content="" if runaway else "```python\nx=1\n```",
                               finish="length" if runaway else "stop", total=0.3 + p))  # fmt: skip
            uc1s.append({"job_id": j, "arm": arm, "item_id": f"he/{p}",
                         "status": "no_code" if runaway else ("pass" if p < 2 else "fail"),
                         "passed": 1, "total": 1})  # fmt: skip
    write_run(runs, "r2", UC1_ARMS, uc1, "codegen")
    length = [uc1_rec(f"{a}/{b}-01/{k}", out=1000, total=2.0, ttft=0.5)
              for a in ("mercury-3:instant", "gpt-oss-120b-cerebras:low",
                        "claude-haiku-5-5:thinking-off")
              for b in ("short", "long") for k in range(2)]  # fmt: skip
    write_run(runs, "r3", ["mercury-3:instant"], length, "prompts")
    ok_turns = [turn(0, [("list_tables", "ok")]), turn(1, [("submit_answer", "answer")])]
    uc2r, uc2s = [], []
    for arm in UC2_ARMS:
        for q in range(4):
            j = f"{arm}/bird/{q}/0"
            uc2r.append(uc2_rec(j, "submitted", ok_turns))
            uc2s.append(uc2_score(j, q < (3 if arm.startswith("claude") else 2)))
    write_run(runs, "r4", UC2_ARMS, uc2r, "sql_agent")
    strict, stricts = [], []
    for arm in STRICT_ARMS:
        for q in range(4):
            j = f"{arm}/bird/{q}/0"
            bad = arm == "mercury-3:instant" and q == 3
            strict.append(uc2_rec(j, "invalid_answer" if bad else "submitted", ok_turns))
            stricts.append(uc2_score(j, q < 2))
    write_run(runs, "r4b", STRICT_ARMS, strict, "sql_agent")
    for rid, name, rows in (("r2", "scores.jsonl", uc1s), ("r4", "uc2_scores.jsonl", uc2s),
                            ("r4b", "uc2_scores.jsonl", stricts)):  # fmt: skip
        (analysis / rid).mkdir(parents=True)
        (analysis / rid / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    tags = tmp / "tags.csv"
    with tags.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(failures.MANUAL_FIELDS)
        w.writerow(["r2", "mercury-3:high/he/2/0", "budget_out_hidden", "1", "Runaway", "Note."])
        w.writerow(["r4b", "mercury-3:instant/bird/3/0", "", "1", "Leak", "Schema note."])
    cfg = report.ReportConfig.model_validate({
        "as_of": "2026-10-21", "client": "test host", "service_tier": "Standard tiers",
        "runs": {"uc1": "r2", "uc1_length": "r3", "uc2": "r4", "uc2_strict": "r4b"},
        "manual_tags": str(tags),
    })  # fmt: skip
    return cfg, runs, analysis


def test_report_build_end_to_end(tmp_path: Path) -> None:
    cfg, runs, analysis = build_fixture(tmp_path)
    out = tmp_path / "reports"
    written = report.build(cfg, runs, analysis, out, log=lambda _: None)
    names = {p.name for p in written}
    assert {"fig1_pareto_uc1.png", "fig2_task_vs_call_speedup_uc2.png", "fig3_latency_cdf.png",
            "fig4_cost_per_success.png", "table_reliability.md", "failure_gallery.md",
            "decision_matrix.md", "README.md", "failure_modes.md"} <= names  # fmt: skip
    for csv_name in ("fig1_pareto_uc1", "fig2_task_vs_call_speedup_uc2", "fig3_latency_cdf",
                     "fig4_cost_per_success"):  # fmt: skip
        assert (out / "assets" / f"{csv_name}.csv").stat().st_size > 50
    gallery = (out / "failure_gallery.md").read_text()
    assert "## 1. Runaway" in gallery and "## 2. Leak" in gallery
    assert "tag `schema_violation`" in gallery  # empty manual tag falls back to the rule
    rel = (out / "table_reliability.md").read_text()
    assert "Mercury 3 · instant | 1 (25.0%)" in rel
    matrix = (out / "decision_matrix.md").read_text()
    assert "As of 2026-10-21" in matrix and "commit abcdef1" in matrix
    assert "Pending: needs the R5" in (out / "README.md").read_text()
    modes = pd.read_csv(out / "assets" / "failure_modes.csv")
    assert modes.loc[modes["arm"] == "mercury-3:high", "budget_out_hidden"].sum() == 1


def test_report_cli(tmp_path: Path) -> None:
    cfg, runs, analysis = build_fixture(tmp_path)
    conf = tmp_path / "report.yaml"
    conf.write_text(json.dumps(json.loads(cfg.model_dump_json())))
    res = CliRunner().invoke(app, ["report", "--config", str(conf), "--runs-dir", str(runs),
                                   "--analysis-dir", str(analysis), "--out-dir",
                                   str(tmp_path / "out")])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert (tmp_path / "out" / "README.md").exists()
    (analysis / "r4" / "uc2_scores.jsonl").unlink()
    res = CliRunner().invoke(app, ["report", "--config", str(conf), "--runs-dir", str(runs),
                                   "--analysis-dir", str(analysis)])  # fmt: skip
    assert res.exit_code == 1 and "dllm-eval score" in res.output


# --- diffusion replay (F6.5) and dynamics (F7.2) --------------------------------------

R0_DIFFUSING = Path(__file__).parents[1] / "fixtures/recordings/r0/mercury-3/diffusing.json"


def r0_frames() -> list[dict[str, Any]]:
    d = json.loads(R0_DIFFUSING.read_text())
    return [
        {"t": c["t"], "text": c["chunk"]["choices"][0]["delta"]["content"]}
        for c in d["chunks"]
        if c["chunk"]["choices"] and c["chunk"]["choices"][0]["delta"].get("content")
    ]


def test_final_mask() -> None:
    assert all(diffusion.final_mask("ab\ncd", "ab\ncd"))
    m = diffusion.final_mask("aXc\nzzz", "abc\nzzz")
    assert m == [True, False, True, True, True, True, True]
    assert diffusion.final_mask("", "abc") == []


def test_dynamics_and_gif_from_r0_recording(tmp_path: Path) -> None:
    from PIL import Image

    rec = {"job_id": "mercury-3:instant/split/0", "arm": "mercury-3:instant", "item_id": "split",
           "repeat": 0, "status": "ok", "result": {"frames": r0_frames()}}  # fmt: skip
    frames = diffusion.frames_of(rec)
    dyn = diffusion.dynamics(frames)
    assert dyn["frames"] == 10 and dyn["final_chars"] == 3127
    assert dyn["length_steps"] == 6  # the text grows in blocks
    assert dyn["regressions"] == 3 and dyn["similarity_trace"][-1] == 1.0
    gif = diffusion.render_gif(frames, tmp_path / "d.gif", title="t", footnote="f")
    with Image.open(gif) as im:
        assert im.n_frames == 10 and im.size == (1600, 900)  # 16:9 for blog lead images
        im.seek(9)
        assert im.info["duration"] == 3000  # final frame held
    small = diffusion.render_gif(frames, tmp_path / "s.gif", title="t", footnote="f",
                                 size=(800, 450), columns=1)  # fmt: skip
    with Image.open(small) as im:
        assert im.size == (800, 450)  # long text is cut with a "more lines" note
    rows = diffusion.dynamics_rows([rec, {**rec, "status": "error"}, {**rec, "result": {}}])
    assert len(rows) == 1 and rows[0]["similarity_trace"].endswith("1.000")
    with pytest.raises(ValueError, match="no frames"):
        diffusion.dynamics([])


def test_report_with_diffusion_run(tmp_path: Path) -> None:
    cfg, runs, analysis = build_fixture(tmp_path)
    rec = {"job_id": "mercury-3:instant-diffusing/split/0", "arm": "mercury-3:instant-diffusing",
           "model": "mercury-3", "item_id": "split", "repeat": 0, "status": "ok",
           "request": {"messages": [{"role": "user", "content": "Split an image."}]},
           "result": {"frames": r0_frames()}}  # fmt: skip
    write_run(runs, "r5", ["mercury-3:instant-diffusing"], [rec], "prompts")
    cfg = cfg.model_copy(update={"runs": cfg.runs.model_copy(update={"diffusion": "r5"})})
    out = tmp_path / "reports"
    names = {p.name for p in report.build(cfg, runs, analysis, out, log=lambda _: None)}
    assert {"diffusion_2026.gif", "diffusion_dynamics.csv"} <= names
    assert "diffusion_2026.gif" in (out / "README.md").read_text()


def test_excerpt_head_only() -> None:
    ex = failures.excerpt("h" * 100 + "t" * 300, 50, 0)
    assert ex.startswith("h" * 50) and "tttt" not in ex and ex.endswith("omitted …]")
