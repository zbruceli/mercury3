"""Cost engine (F6.3): server usage x frozen price table. Never looks prices up live."""

from __future__ import annotations

import math

from dllm_eval.config import Model, Pricing
from dllm_eval.schemas import Usage


def pricing_for(model: Model, input_tokens: int, *, regular: bool = False) -> Pricing:
    """Long-context tier above its threshold; `regular` swaps a promo for the list price."""
    if model.long_context and input_tokens > model.long_context.threshold_tokens:
        return model.long_context.pricing
    if regular and model.pricing_regular is not None:
        return model.pricing_regular
    if model.pricing is None:
        raise ValueError("model has no pricing")
    return model.pricing


def tokens_cost(
    model: Model,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    *,
    regular: bool = False,
) -> float:
    p = pricing_for(model, input_tokens, regular=regular)
    uncached = max(0, input_tokens - cached_tokens)
    return (uncached * p.input + cached_tokens * p.cached_input + output_tokens * p.output) / 1e6


def call_cost(usage: Usage, model: Model, *, regular: bool = False) -> float | None:
    """USD for one call, or None when usage is incomplete.

    Unknown cached tokens are treated as uncached (conservative; M0 F3). Anthropic cache
    writes (1.25x input) are not priced separately; BUDGET_ESTIMATE section 6 covers it.
    """
    if usage.input_tokens is None or usage.output_tokens is None:
        return None
    return tokens_cost(
        model,
        usage.input_tokens,
        usage.cached_input_tokens or 0,
        usage.output_tokens,
        regular=regular,
    )


def estimate_tokens(text: str) -> int:
    """About 4 characters per English token (Inception FAQ). Dry-run estimates only."""
    return max(1, math.ceil(len(text) / 4))
