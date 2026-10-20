"""Estimate tokens and cost for the TEST_PLAN B5 runs (R0-R5).

Reads configs/budget_assumptions.yaml (token guesses) and configs/models.yaml (prices),
then prints Markdown tables. Makes no API calls.

    uv run python scripts/estimate_budget.py [assumptions.yaml]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from dllm_eval.config import Model, Pricing, load_registry

ROOT = Path(__file__).parents[1]
ASSUMPTIONS = ROOT / "configs" / "budget_assumptions.yaml"
MERCURY_FREE_CREDIT = 100_000_000  # one-time, shared across Inception models


@dataclass
class Tokens:
    input: float = 0.0  # includes cached
    cached: float = 0.0
    output: float = 0.0  # visible + reasoning

    def __add__(self, other: Tokens) -> Tokens:
        return Tokens(
            self.input + other.input, self.cached + other.cached, self.output + other.output
        )

    def scaled(self, k: float) -> Tokens:
        return Tokens(self.input * k, self.cached * k, self.output * k)

    @property
    def total(self) -> float:
        return self.input + self.output


def cost(t: Tokens, p: Pricing) -> float:
    uncached = t.input - t.cached
    return (uncached * p.input + t.cached * p.cached_input + t.output * p.output) / 1e6


def uc1_calls(calls: float, inp: int, visible: int, reasoning: int) -> Tokens:
    return Tokens(input=calls * inp, output=calls * (visible + reasoning))


def uc2_tasks(tasks: float, cfg: dict[str, Any], reasoning_per_turn: int) -> Tokens:
    per_turn_input = [cfg["base_input"] + k * cfg["growth_per_turn"] for k in range(cfg["turns"])]
    inp = sum(per_turn_input)
    cached = cfg["cached_fraction"] * sum(per_turn_input[1:])
    out = cfg["turns"] * (cfg["visible_output_per_turn"] + reasoning_per_turn)
    return Tokens(tasks * inp, tasks * cached, tasks * out)


def estimate(a: dict[str, Any]) -> dict[str, dict[str, Tokens]]:
    """Returns {run: {model: Tokens}} before contingency."""
    arms: dict[str, dict[str, str]] = a["arms"]
    rt = a["reasoning_tokens"]
    runs = a["runs"]
    out: dict[str, dict[str, Tokens]] = defaultdict(lambda: defaultdict(Tokens))

    flat = runs["R0_smoke"]["flat_tokens_per_model"]
    for model in arms:
        out["R0 smoke"][model] = Tokens(input=flat["input"], output=flat["output"])

    r4 = runs["R4_uc2_main"]

    p = runs["R1_pilot"]
    ev = runs["R2_uc1_main"]["workloads"]["uc1_evalplus"]
    for model, model_arms in arms.items():
        for tier in model_arms.values():
            calls = p["uc1"]["items"] * p["uc1"]["repeats"]
            out["R1 pilot"][model] += uc1_calls(
                calls, ev["input"], ev["visible_output"], rt["uc1_evalplus"][tier]
            )
            if model not in r4["exclude_models"]:
                tasks = p["uc2"]["tasks"] * p["uc2"]["repeats"]
                out["R1 pilot"][model] += uc2_tasks(tasks, r4, rt["uc2_turn"][tier])

    for wl_name, wl in runs["R2_uc1_main"]["workloads"].items():
        for model, model_arms in arms.items():
            for tier in model_arms.values():
                calls = wl["items"] * wl["repeats"]
                out["R2 UC1 main"][model] += uc1_calls(
                    calls, wl["input"], wl["visible_output"], rt[wl_name][tier]
                )

    r3 = runs["R3_uc1_length"]
    for model, model_arms in r3["arms"].items():
        for tier in model_arms.values():
            calls = r3["items"] * r3["repeats"]
            out["R3 UC1 length"][model] += uc1_calls(
                calls, r3["input"], r3["visible_output"], rt[r3["workload"]][tier]
            )

    strict = r4["strict_subrun"]
    excluded_arms: dict[str, list[str]] = r4.get("exclude_arms", {})
    for model, model_arms in arms.items():
        if model in r4["exclude_models"]:
            continue
        for arm, tier in model_arms.items():
            if arm in excluded_arms.get(model, []):
                continue
            out["R4 UC2 main"][model] += uc2_tasks(
                r4["tasks"] * r4["repeats"], r4, rt["uc2_turn"][tier]
            )
        if model not in strict["exclude_models"]:
            lowest = next(iter(model_arms.values()))
            out["R4 UC2 main"][model] += uc2_tasks(
                strict["tasks"] * strict["repeats"], r4, rt["uc2_turn"][lowest]
            )

    r5 = runs["R5_diffusion_capture"]
    out["R5 diffusion"][r5["model"]] = uc1_calls(
        r5["prompts"] * r5["repeats"], r5["input"], r5["visible_output"], 0
    )
    return out


def fmt_tok(n: float) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e5 else f"{n / 1e3:.0f}k"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ASSUMPTIONS
    a = yaml.safe_load(path.read_text())
    models: dict[str, Model] = load_registry(ROOT / "configs" / "models.yaml").models
    k = 1 + a["contingency"]
    est = estimate(a)

    def price(m: str) -> Pricing:
        p = models[m].pricing
        assert p is not None, m
        return p

    w = sys.stdout.write
    w(f"<!-- Generated by scripts/estimate_budget.py from {path.name}")
    w(" and models.yaml. Do not edit by hand. -->\n\n")
    w(f"Contingency applied to totals: +{a['contingency']:.0%}\n\n")

    w("### Per run (before contingency)\n\n")
    w("| Run | Model | Input | (cached) | Output | Total tokens | Cost (billed price) |\n")
    w("|---|---|---:|---:|---:|---:|---:|\n")
    for run, per_model in est.items():
        for m, t in per_model.items():
            w(
                f"| {run} | `{m}` | {fmt_tok(t.input)} | {fmt_tok(t.cached)} | "
                f"{fmt_tok(t.output)} | {fmt_tok(t.total)} | ${cost(t, price(m)):,.2f} |\n"
            )

    totals: dict[str, Tokens] = defaultdict(Tokens)
    for per_model in est.values():
        for m, t in per_model.items():
            totals[m] += t

    w("\n### Per model, all runs (with contingency)\n\n")
    w("| Model | Total tokens | Output tokens | Cost (billed price) | Cost (regular price) |\n")
    w("|---|---:|---:|---:|---:|\n")
    grand_billed = grand_regular = 0.0
    mercury_tokens = 0.0
    baseline_cost = 0.0
    for m, t in totals.items():
        tk = t.scaled(k)
        billed = cost(tk, price(m))
        regular = cost(tk, models[m].pricing_regular or price(m))
        grand_billed += billed
        grand_regular += regular
        if models[m].provider == "inception":
            mercury_tokens += tk.total
        else:
            baseline_cost += billed
        w(
            f"| `{m}` | {fmt_tok(tk.total)} | {fmt_tok(tk.output)} | "
            f"${billed:,.2f} | ${regular:,.2f} |\n"
        )
    w(f"| **All** | | | **${grand_billed:,.2f}** | **${grand_regular:,.2f}** |\n")

    w("\n### Budget check\n\n")
    share = mercury_tokens / MERCURY_FREE_CREDIT
    w(
        f"- Inception (Mercury 3 + 2.5): **{fmt_tok(mercury_tokens)} tokens**, "
        f"{share:.0%} of the 100M free credit.\n"
    )
    w(f"- Paid baselines (Anthropic, OpenAI, Google, Cerebras): **${baseline_cost:,.2f}**.\n")


if __name__ == "__main__":
    main()
