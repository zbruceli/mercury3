"""Typed schema for configs/models.yaml (the model registry)."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MODELS_PATH = Path("configs/models.yaml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderKind(StrEnum):
    OPENAI_COMPAT = "openai_compat"
    ANTHROPIC = "anthropic"
    FAKE = "fake"  # deterministic offline provider for tests and dry pipeline runs


class Architecture(StrEnum):
    DIFFUSION = "diffusion"
    AUTOREGRESSIVE = "autoregressive"


class Role(StrEnum):
    PRIMARY = "primary"
    LINEAGE = "lineage"
    BASELINE = "baseline"
    HARDWARE_CONTROL = "hardware_control"


class RateLimits(_Strict):
    """Per-minute limits. Providers publish different token splits, so each is optional."""

    requests_per_min: int = Field(gt=0)
    input_tokens_per_min: int | None = Field(default=None, gt=0)
    output_tokens_per_min: int | None = Field(default=None, gt=0)
    total_tokens_per_min: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _has_a_token_limit(self) -> Self:
        if (
            self.input_tokens_per_min is None
            and self.output_tokens_per_min is None
            and self.total_tokens_per_min is None
        ):
            raise ValueError("rate_limits needs at least one token limit")
        return self


class Provider(_Strict):
    kind: ProviderKind
    base_url: str | None = None
    api_key_env: str
    # Name of the output-budget field on OpenAI-compatible endpoints (Gemini's compat
    # layer takes max_tokens; verified in R0).
    budget_param: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    tier: str | None = None
    rate_limits: RateLimits | None = None
    docs_url: str
    verified_on: dt.date | None = None


class Pricing(_Strict):
    """USD per 1M tokens."""

    input: float = Field(ge=0)
    cached_input: float = Field(ge=0)
    output: float = Field(ge=0)


class LongContextPricing(_Strict):
    """Higher prices for requests whose prompt exceeds threshold_tokens."""

    threshold_tokens: int = Field(gt=0)
    pricing: Pricing


class Supports(_Strict):
    streaming: bool = True
    tools: bool = False
    structured_outputs: bool = False
    diffusing: bool = False


class Model(_Strict):
    provider: str
    model_id: str | None
    architecture: Architecture
    role: Role
    enabled: bool
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature_range: tuple[float, float] | None = None
    reasoning_efforts: list[str] = Field(default_factory=list)
    supports: Supports = Field(default_factory=Supports)
    pricing: Pricing | None = None
    pricing_regular: Pricing | None = None
    long_context: LongContextPricing | None = None
    verified_on: dt.date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _enabled_models_are_complete(self) -> Self:
        if self.enabled:
            missing = [
                name
                for name in ("model_id", "pricing", "verified_on")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"enabled model is missing: {', '.join(missing)}")
        if self.temperature_range is not None:
            lo, hi = self.temperature_range
            if lo > hi:
                raise ValueError("temperature_range must be (low, high)")
        return self


class ModelRegistry(_Strict):
    schema_version: int
    providers: dict[str, Provider]
    models: dict[str, Model]

    @model_validator(mode="after")
    def _providers_exist(self) -> Self:
        for key, model in self.models.items():
            if model.provider not in self.providers:
                raise ValueError(f"model {key!r} references unknown provider {model.provider!r}")
        return self

    def enabled_models(self) -> dict[str, Model]:
        return {k: m for k, m in self.models.items() if m.enabled}

    def stale_entries(self, today: dt.date, max_age_days: int) -> list[str]:
        """Enabled models (and their providers) not re-verified within max_age_days."""
        cutoff = today - dt.timedelta(days=max_age_days)
        stale: list[str] = []
        for key, model in self.enabled_models().items():
            if model.verified_on is None or model.verified_on < cutoff:
                stale.append(f"model:{key}")
            provider = self.providers[model.provider]
            if provider.verified_on is None or provider.verified_on < cutoff:
                stale.append(f"provider:{model.provider}")
        return sorted(set(stale))


def load_registry(path: Path = DEFAULT_MODELS_PATH) -> ModelRegistry:
    with path.open() as f:
        return ModelRegistry.model_validate(yaml.safe_load(f))
