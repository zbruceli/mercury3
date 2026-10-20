"""Provider factory. Everything outside this package talks to BaseProvider.chat() only."""

from __future__ import annotations

from typing import Any

from dllm_eval.config import ModelRegistry, ProviderKind
from dllm_eval.providers.anthropic import AnthropicProvider
from dllm_eval.providers.base import BaseProvider, ErrorKind, ProviderError, RateLimiter
from dllm_eval.providers.fake import FakeProvider
from dllm_eval.providers.inception import InceptionProvider
from dllm_eval.providers.openai_compat import OpenAICompatProvider

__all__ = ["BaseProvider", "ErrorKind", "ProviderError", "RateLimiter", "build_provider"]


def build_provider(
    model_key: str,
    registry: ModelRegistry,
    *,
    limiters: dict[str, RateLimiter] | None = None,
    **kwargs: Any,
) -> BaseProvider:
    """Construct the adapter for a model. Pass a shared `limiters` dict so every model on
    one provider (e.g. mercury-3 and mercury-2.5) draws from the same rate-limit window."""
    model = registry.models[model_key]
    provider_key = model.provider
    provider = registry.providers[provider_key]
    if limiters is not None:
        kwargs["limiter"] = limiters.setdefault(provider_key, RateLimiter(provider.rate_limits))
    cls: type[BaseProvider]
    if provider.kind is ProviderKind.ANTHROPIC:
        cls = AnthropicProvider
    elif provider.kind is ProviderKind.FAKE:
        cls = FakeProvider
    elif provider_key == "inception":
        cls = InceptionProvider
    else:
        cls = OpenAICompatProvider
    return cls(model_key, model, provider_key, provider, **kwargs)
