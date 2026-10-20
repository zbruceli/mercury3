"""Per-arm summary table: reliability, latency, throughput, cost."""

from __future__ import annotations

from typing import Any

import pandas as pd

from dllm_eval.stats import summarize


def latency_sample(df: pd.DataFrame) -> pd.DataFrame:
    """Rows eligible for latency stats: ok, not warm-up, not retried (CLAUDE.md)."""
    return df[(df["status"] == "ok") & (~df["warmup"].astype(bool)) & (df["retries"] == 0)]


def _fmt(s: Any, digits: int = 3) -> str:
    return "—" if s is None else f"{s:.{digits}f}"


def arm_summary(df: pd.DataFrame, *, seed: int = 0) -> list[dict[str, Any]]:
    rows = []
    for arm, g in df.groupby("arm", sort=True):
        lat = latency_sample(g)
        tok_s = (lat["output_tokens"] / lat["total_s"]).where(lat["total_s"] > 0)
        ttft = summarize(lat["ttft_s"].dropna().tolist(), seed=seed)
        total = summarize(lat["total_s"].dropna().tolist(), seed=seed)
        tps = summarize(tok_s.dropna().tolist(), seed=seed)
        rows.append(
            {
                "arm": arm,
                "jobs": len(g),
                "ok": int((g["status"] == "ok").sum()),
                "errors": int((g["status"] != "ok").sum()),
                "retried": int((g["retries"] > 0).sum()),
                "latency_n": len(lat),
                "ttft_p50": ttft.p50 if ttft else None,
                "ttft_p95": ttft.p95 if ttft else None,
                "total_p50": total.p50 if total else None,
                "total_p95": total.p95 if total else None,
                "out_tok_s_p50": tps.p50 if tps else None,
                "output_tokens": int(g["output_tokens"].fillna(0).sum()),
                "reasoning_tokens": (
                    None
                    if g["reasoning_tokens"].isna().all()
                    else int(g["reasoning_tokens"].fillna(0).sum())
                ),
                "cost_usd": float(g["cost_usd"].fillna(0).sum()),
                "cost_usd_regular": float(g["cost_usd_regular"].fillna(0).sum()),
            }
        )
    return rows


def to_markdown(rows: list[dict[str, Any]]) -> str:
    head = (
        "| Arm | Jobs | OK | Err | Retried | TTFT p50 (s) | TTFT p95 | Total p50 (s) | Total p95 "
        "| Out tok/s p50 | Output tok | Reasoning tok | Cost $ |"
    )
    sep = "|---|" + "---:|" * 12
    lines = [head, sep]
    for r in rows:
        reasoning = "unknown" if r["reasoning_tokens"] is None else str(r["reasoning_tokens"])
        lines.append(
            f"| `{r['arm']}` | {r['jobs']} | {r['ok']} | {r['errors']} | {r['retried']} | "
            f"{_fmt(r['ttft_p50'])} | {_fmt(r['ttft_p95'])} | {_fmt(r['total_p50'])} | "
            f"{_fmt(r['total_p95'])} | {_fmt(r['out_tok_s_p50'], 0)} | {r['output_tokens']} | "
            f"{reasoning} | {r['cost_usd']:.6f} |"
        )
    return "\n".join(lines)


def probe_markdown(probes: list[dict[str, Any]]) -> str:
    """One row per probe record (start, resume, periodic) from probes.jsonl."""

    def ms(v: float | None) -> str:
        return "—" if v is None else f"{v * 1000:.0f}"

    lines = [
        "| Provider | When | Label | OK samples | DNS | Connect (~RTT) | TLS | TTFB | Total |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in probes:
        m = p["median"]
        ok = sum(1 for s in p["samples"] if s.get("error") is None)
        lines.append(
            f"| {p['provider']} | {p['at']} | {p['label']} | {ok}/{len(p['samples'])} | "
            f"{ms(m['dns_s'])} | {ms(m['connect_s'])} | {ms(m['tls_s'])} | {ms(m['ttfb_s'])} | "
            f"{ms(m['total_s'])} |"
        )
    return "\n".join(lines)
