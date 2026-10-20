"""Inception (Mercury) adapter: OpenAI-compatible, with Mercury-only knobs in extra_body (F1.2)."""

from __future__ import annotations

from typing import Any

from dllm_eval.providers.openai_compat import OpenAICompatProvider
from dllm_eval.schemas import ChatRequest


class InceptionProvider(OpenAICompatProvider):
    # diffusing: stream the denoising frames (visualization only, never for latency; PRD R7)
    # realtime: lower-TTFT mode (PRD OQ3)
    ALLOWED_EXTRAS = frozenset(
        {"diffusing", "realtime", "reasoning_summary", "reasoning_summary_wait"}
    )

    def validate(self, req: ChatRequest) -> None:
        super().validate(req)
        if req.provider_extras.get("diffusing") and not req.stream:
            raise ValueError("diffusing requires stream=True")

    def extra_body(self, req: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = dict(req.provider_extras)
        if req.reasoning_effort is not None:
            body["reasoning_effort"] = req.reasoning_effort
        return body

    def is_diffusing(self, req: ChatRequest) -> bool:
        return bool(req.provider_extras.get("diffusing"))
