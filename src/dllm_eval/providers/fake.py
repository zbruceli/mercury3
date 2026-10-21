"""Deterministic offline provider for unit tests and the A4 pipeline run. Never calls a network."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from dllm_eval.providers.base import BaseProvider, ProviderError, classify
from dllm_eval.schemas import ChatRequest, ChatResult, Timing, ToolCall, Usage


class FakeProvider(BaseProvider):
    """Echoes the last user message. Behaviour can be scripted per request:

    provider_extras:
      fake_fail: list of statuses to raise on successive attempts, e.g. [503, 503]
      fake_warning: a warning string to return (tests fail_on_warning)
      fake_tool: name of a tool to "call" with {"q": <last user text>}
      fake_delay_s: seconds each call takes (real asyncio.sleep; tests periodic probing)
      fake_script: multi-turn agent behaviour, one entry per assistant turn (the turn is
        the number of assistant messages already in the history; the last entry repeats).
        Each entry is {"text": str, "calls": [[name, args]], "finish": str}, where args is
        a dict (sent as JSON) or a raw string (sent as-is, e.g. invalid JSON)
    """

    ALLOWED_EXTRAS = frozenset(
        {"fake_fail", "fake_warning", "fake_tool", "fake_delay_s", "fake_script"}
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._attempts: dict[int, int] = {}

    def validate(self, req: ChatRequest) -> None:
        super().validate(req)
        unknown = set(req.provider_extras) - self.ALLOWED_EXTRAS
        if unknown:
            raise ValueError(f"fake: unsupported provider_extras {sorted(unknown)}")

    async def _chat_once(self, req: ChatRequest) -> ChatResult:
        key = id(req)
        attempt = self._attempts.get(key, 0)
        self._attempts[key] = attempt + 1
        failures: list[Any] = list(req.provider_extras.get("fake_fail", []))
        if attempt < len(failures):
            status = failures[attempt]
            error_type = "insufficient_quota" if status == "quota" else None
            code = 429 if status == "quota" else int(status)
            raise ProviderError(
                classify(code, error_type), f"fake {code}", status=code, error_type=error_type
            )

        last_user = next((m.content or "" for m in reversed(req.messages) if m.role == "user"), "")
        text = f"fake:{last_user[:40]}"
        tool_calls = []
        if req.provider_extras.get("fake_tool"):
            tool_calls = [
                ToolCall(id="call_0", name=req.provider_extras["fake_tool"], arguments='{"q": "x"}')
            ]
        finish = "tool_calls" if tool_calls else "stop"
        script = req.provider_extras.get("fake_script")
        if script:
            turn = sum(m.role == "assistant" for m in req.messages)
            step = script[min(turn, len(script) - 1)]
            text = step.get("text", "")
            tool_calls = [
                ToolCall(
                    id=f"call_{turn}_{i}",
                    name=name,
                    arguments=args if isinstance(args, str) else json.dumps(args),
                )
                for i, (name, args) in enumerate(step.get("calls", []))
            ]
            finish = step.get("finish", "tool_calls" if tool_calls else "stop")
        prompt_chars = sum(len(m.content or "") for m in req.messages)
        out_tokens = min(req.max_completion_tokens, max(1, len(text) // 4))
        # yield like a real call; deliberately not the injected backoff sleep
        await asyncio.sleep(float(req.provider_extras.get("fake_delay_s", 0)))
        return ChatResult(
            content=text,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=Usage(
                input_tokens=max(1, prompt_chars // 4),
                cached_input_tokens=0,
                output_tokens=out_tokens,
                reasoning_tokens=0,
            ),
            timing=Timing(
                t_first_byte=0.01, t_first_content=0.02, t_last_content=0.05, t_done=0.05
            ),
            server_model=self.model.model_id,
            warnings=[req.provider_extras["fake_warning"]]
            if req.provider_extras.get("fake_warning")
            else [],
            n_chunks=2,
        )
