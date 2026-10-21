import statistics

import pytest

from dllm_eval.config import ModelRegistry
from dllm_eval.cost import call_cost, estimate_tokens, tokens_cost
from dllm_eval.logging_store import REDACTED, RunStore, redact
from dllm_eval.schemas import ToolCall, Usage
from dllm_eval.stats import bootstrap_ci, percentile, summarize
from dllm_eval.timing import Stopwatch

# --- timing ----------------------------------------------------------------------


def test_stopwatch_marks_relative_to_start() -> None:
    ticks = iter([100.0, 100.2, 100.5, 101.0, 101.5])
    sw = Stopwatch(clock=lambda: next(ticks))
    sw.start()  # 100.0
    sw.mark_first_byte()  # 0.2
    sw.mark_content()  # 0.5 -> first
    sw.mark_content()  # 1.0 -> last
    t = sw.finish()  # 1.5
    assert (t.t_first_byte, t.ttft, t.decode_s, t.total_s) == pytest.approx((0.2, 0.5, 0.5, 1.5))


def test_stopwatch_without_content_has_no_ttft() -> None:
    ticks = iter([0.0, 2.0])
    sw = Stopwatch(clock=lambda: next(ticks))
    sw.start()
    t = sw.finish()
    assert t.ttft is None and t.decode_s is None and t.total_s == 2.0


# --- schemas ---------------------------------------------------------------------


def test_tool_call_parsed_handles_bad_json() -> None:
    assert ToolCall(id="1", name="f", arguments='{"a": 1}').parsed() == {"a": 1}
    assert ToolCall(id="1", name="f", arguments="{oops").parsed() is None
    assert ToolCall(id="1", name="f", arguments="[1]").parsed() is None


# --- cost ------------------------------------------------------------------------


def test_cost_uses_cached_rate(repo_registry: ModelRegistry) -> None:
    m = repo_registry.models["mercury-3"]  # promo 0.10 / 0.01 / 0.35
    u = Usage(
        input_tokens=1_000_000,
        cached_input_tokens=400_000,
        output_tokens=1_000_000,
        reasoning_tokens=500_000,
    )
    # reasoning is inside output_tokens: not billed twice
    assert call_cost(u, m) == pytest.approx(0.6 * 0.10 + 0.4 * 0.01 + 0.35)
    assert call_cost(u, m, regular=True) == pytest.approx(0.6 * 0.20 + 0.4 * 0.02 + 0.70)


def test_cost_unknown_cache_is_treated_as_uncached(repo_registry: ModelRegistry) -> None:
    m = repo_registry.models["gemini-3.5-flash-lite"]
    u = Usage(input_tokens=1_000_000, cached_input_tokens=None, output_tokens=0)
    assert call_cost(u, m) == pytest.approx(0.30)


def test_cost_incomplete_usage_is_none(repo_registry: ModelRegistry) -> None:
    assert call_cost(Usage(input_tokens=10), repo_registry.models["mercury-3"]) is None


def test_long_context_tier(repo_registry: ModelRegistry) -> None:
    haiku = repo_registry.models["claude-haiku-5-5"]
    assert tokens_cost(haiku, 100_000, 0, 0) == pytest.approx(0.01)
    assert tokens_cost(haiku, 100_001, 0, 0) == pytest.approx(100_001 * 0.50 / 1e6)


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcdefgh") == 2


# --- stats -----------------------------------------------------------------------


def test_percentile_matches_numpy_linear() -> None:
    xs = [1, 2, 3, 4, 10]
    assert percentile(xs, 50) == 3
    assert percentile(xs, 95) == pytest.approx(8.8)
    assert percentile([5], 95) == 5


def test_bootstrap_is_deterministic_and_brackets_mean() -> None:
    xs = [float(i % 7) for i in range(200)]
    a = bootstrap_ci(xs, seed=1)
    assert a == bootstrap_ci(xs, seed=1)
    assert a[0] < statistics.fmean(xs) < a[1]


def test_summarize_skips_missing() -> None:
    s = summarize([1.0, float("nan"), 3.0])
    assert s is not None and s.n == 2 and s.mean == 2.0
    assert summarize([]) is None


# --- store / redaction -------------------------------------------------------------


def test_redact_keys_and_secret_values() -> None:
    secret = "sk-test-1234567890"
    obj = {"Authorization": "Bearer x", "nested": [{"msg": f"key {secret} leaked"}], "n": 1}
    assert redact(obj, [secret]) == {
        "Authorization": REDACTED,
        "nested": [{"msg": f"key {REDACTED} leaked"}],
        "n": 1,
    }


def test_store_is_append_only_and_resumable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = RunStore(tmp_path / "run", secrets=["sk-secret-value"])
    store.create({"run_id": "r", "note": "sk-secret-value"})
    with pytest.raises(FileExistsError):
        store.create({"run_id": "r"})
    store.append_call({"job_id": "a", "status": "error"})
    store.append_call({"job_id": "b", "status": "ok"})
    store.append_call({"job_id": "a", "status": "ok"})  # resumed retry supersedes
    assert store.completed_job_ids() == {"a", "b"}
    assert len(list(store.iter_calls())) == 3  # nothing rewritten
    assert "sk-secret-value" not in store.manifest_path.read_text()


def test_pause_spanning_calls_are_flagged() -> None:
    from dllm_eval.analysis.load import mark_pause_spanning

    rows = [
        {"started_at": "2026-10-21T04:00:00+00:00", "wall_s": 5},     # before the pause
        {"started_at": "2026-10-21T04:00:30+00:00", "wall_s": 120},   # in flight across it
        {"started_at": "2026-10-21T04:20:00+00:00", "wall_s": 3},     # after it
    ]  # fmt: skip
    gaps = mark_pause_spanning(rows, gap_s=60)
    assert len(gaps) == 1
    assert [r["spans_pause"] for r in rows] == [False, True, False]


def test_pareto_chart_from_scores(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import pandas as pd

    from dllm_eval.analysis.charts import pareto_points, plot_pareto, write_points_csv

    calls = pd.DataFrame(
        [
            {
                "arm": a,
                "status": "ok",
                "warmup": False,
                "retries": 0,
                "spans_pause": False,
                "total_s": t,
            }
            for a, t in [("mercury-3:low", 0.5), ("mercury-3:low", 0.7), ("gpt-6-luna:none", 1.2)]
        ]
    )
    scores = [
        {"arm": "mercury-3:low", "item_id": "p1", "status": "pass"},
        {"arm": "mercury-3:low", "item_id": "p2", "status": "fail"},
        {"arm": "gpt-6-luna:none", "item_id": "p1", "status": "pass"},
        {"arm": "gpt-6-luna:none", "item_id": "p2", "status": "harness_error"},  # excluded
    ]
    pts = {p.arm: p for p in pareto_points(calls, scores)}
    assert pts["mercury-3:low"].pass1 == 0.5 and pts["mercury-3:low"].latency_p50_s == 0.6
    assert pts["gpt-6-luna:none"].problems == 1
    png = plot_pareto(list(pts.values()), tmp_path / "f.png", title="t", subtitle="s")
    assert png.stat().st_size > 10_000
    assert "pass1" in write_points_csv(list(pts.values()), tmp_path / "f.csv").read_text()
