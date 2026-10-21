"""Side test (M2-F3): is GPT-6 Luna's slow first token queueing (which Fast mode, i.e.
service_tier "fast"/"priority", would cut) or fixed overhead at OpenAI's front door?

SPENDS TOKENS (~40 tiny calls, a few cents). Run only with explicit approval:

    uv run --env-file .env python scripts/oai_priority_ab.py

Each prompt runs once per tier, in seeded random order, sequentially (no concurrency),
so both tiers see the same network conditions. Records time to response headers, TTFT,
total time, OpenAI's `openai-processing-ms` header, and the tier the response reports.
Raw rows go to results/analysis/oai-priority-ab-<ts>/calls.jsonl (gitignored).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import statistics as st
import time
from pathlib import Path
from typing import Any

import openai

MODEL = "gpt-6-luna"
TIERS = ("default", "fast")
SEED = 21
PROMPTS = [
    "Write a Python function `is_palindrome(s: str) -> bool` ignoring case and non-alphanumerics.",
    "Write a Python function `chunk(xs: list, n: int) -> list` splitting a list into chunks of n.",
    "Write a Python function `roman_to_int(s: str) -> int`.",
    "Write a Python function `flatten(d: dict) -> dict` flattening nested dicts into dotted keys.",
    "Write a Python function `binary_search(xs: list[int], t: int) -> int` returning index or -1.",
    "Write a Python function `word_count(text: str) -> dict[str, int]` counting lowercase words.",
    "Write a Python function `merge_intervals(iv: list[tuple[int, int]]) -> list[tuple[int, int]]`.",  # noqa: E501
    "Write a Python function `rle_encode(s: str) -> str`, e.g. 'aaab' -> 'a3b1'.",
    "Write a Python function `is_valid_ipv4(s: str) -> bool`.",
    "Write a Python function `transpose(m: list[list[int]]) -> list[list[int]]`.",
    "Write a Python function `fib(n: int) -> int` using iteration.",
    "Write a Python function `gcd(a: int, b: int) -> int`.",
    "Write a Python function `is_prime(n: int) -> bool`.",
    "Write a Python function `reverse_words(s: str) -> str` reversing word order.",
    "Write a Python function `count_vowels(s: str) -> int`.",
    "Write a Python function `dedupe(xs: list) -> list` keeping first occurrences in order.",
    "Write a Python function `celsius_to_f(c: float) -> float`.",
    "Write a Python function `second_largest(xs: list[int]) -> int | None`.",
    "Write a Python function `anagrams(a: str, b: str) -> bool`.",
    "Write a Python function `digits_sum(n: int) -> int`.",
]
SUFFIX = " Reply with only the code in one ```python block."


def one_call(client: openai.OpenAI, prompt: str, tier: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    raw = client.chat.completions.with_raw_response.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt + SUFFIX}],
        reasoning_effort="none",
        max_completion_tokens=512,
        service_tier=tier,  # type: ignore[arg-type]
        stream=True,
        stream_options={"include_usage": True},
    )
    t_headers = time.perf_counter() - t0
    proc = raw.headers.get("openai-processing-ms")
    ttft = None
    served = usage = None
    for chunk in raw.parse():
        d = chunk.model_dump()
        served = d.get("service_tier") or served
        if d.get("usage"):
            usage = d["usage"]
        choices = d.get("choices") or []
        if ttft is None and choices and (choices[0].get("delta") or {}).get("content"):
            ttft = time.perf_counter() - t0
    return {
        "tier_requested": tier,
        "tier_served": served,
        "t_headers_s": t_headers,
        "ttft_s": ttft,
        "total_s": time.perf_counter() - t0,
        "processing_ms": float(proc) if proc else None,
        "output_tokens": (usage or {}).get("completion_tokens"),
        "input_tokens": (usage or {}).get("prompt_tokens"),
    }


def main() -> None:
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=0)
    rng = random.Random(SEED)
    out_dir = Path("results/analysis") / f"oai-priority-ab-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, prompt in enumerate(PROMPTS):
        order = list(TIERS)
        rng.shuffle(order)
        for tier in order:
            row = {"prompt": i, **one_call(client, prompt, tier)}
            rows.append(row)
            print(json.dumps(row))
    (out_dir / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def col(tier: str, key: str) -> list[float]:
        return [r[key] for r in rows if r["tier_requested"] == tier and r[key] is not None]

    print(f"\nwrote {out_dir}/calls.jsonl")
    print(
        "| Tier | n | served as | TTFT p50 (s) | TTFT p90 | headers p50 (s) | processing-ms p50 | total p50 (s) | out tok p50 |"  # noqa: E501
    )
    print("|---|---:|---|---:|---:|---:|---:|---:|---:|")
    for tier in TIERS:
        served = sorted({r["tier_served"] for r in rows if r["tier_requested"] == tier}, key=str)
        ttft = sorted(col(tier, "ttft_s"))
        print(
            f"| {tier} | {len(ttft)} | {', '.join(map(str, served))} | {st.median(ttft):.3f} | "
            f"{ttft[int(0.9 * (len(ttft) - 1))]:.3f} | {st.median(col(tier, 't_headers_s')):.3f} | "
            f"{st.median(col(tier, 'processing_ms')):.0f} | {st.median(col(tier, 'total_s')):.3f} | "  # noqa: E501
            f"{st.median(col(tier, 'output_tokens')):.0f} |"
        )
    paired = []
    for i in range(len(PROMPTS)):
        by = {r["tier_requested"]: r for r in rows if r["prompt"] == i}
        if by["default"]["ttft_s"] and by["fast"]["ttft_s"]:
            paired.append(by["default"]["ttft_s"] - by["fast"]["ttft_s"])
    faster = sum(d > 0 for d in paired)
    print(
        f"\npaired TTFT (default - fast): median {st.median(paired):+.3f} s; fast was faster on {faster}/{len(paired)} prompts"  # noqa: E501
    )


if __name__ == "__main__":
    main()
