"""OpenAI-compatible chat completions adapter: OpenAI, Gemini compat, Cerebras (F1.3).

Parsing is split from I/O: the assemblers take plain dicts (SDK objects dumped with
model_dump), so recorded fixtures can be replayed through exactly the production code.
"""

from __future__ import annotations

import os
from typing import Any

import openai

from dllm_eval.providers.base import BaseProvider
from dllm_eval.schemas import ChatRequest, ChatResult, Frame, Message, ToolCall, Usage
from dllm_eval.timing import Stopwatch

_KNOWN_TOOL_CALL_KEYS = {"id", "type", "function", "index"}


def map_usage(u: dict[str, Any] | None) -> Usage:
    """Server usage -> Usage. Missing detail objects stay None (M0 F3: Gemini omits them)."""
    if not u:
        return Usage()
    prompt_details = u.get("prompt_tokens_details") or None
    completion_details = u.get("completion_tokens_details") or None
    return Usage(
        input_tokens=u.get("prompt_tokens"),
        cached_input_tokens=prompt_details.get("cached_tokens") if prompt_details else None,
        cache_write_tokens=prompt_details.get("cache_write_tokens") if prompt_details else None,
        output_tokens=u.get("completion_tokens"),
        reasoning_tokens=completion_details.get("reasoning_tokens") if completion_details else None,
    )


def _warnings(d: dict[str, Any]) -> list[str]:
    w = d.get("warning") or d.get("warnings")  # Mercury: top-level `warning` string
    if not w:
        return []
    return [str(x) for x in w] if isinstance(w, list) else [str(w)]


class OpenAIStreamAssembler:
    """Folds chat.completion.chunk dicts into one result.

    In Mercury's diffusing mode every chunk carries the *full* current text, so content
    is replaced (and kept as a frame) instead of appended.
    """

    def __init__(self, *, diffusing: bool = False) -> None:
        self.diffusing = diffusing
        self.parts: list[str] = []
        self.frames: list[Frame] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self._slot_of: dict[int, int] = {}  # wire index -> slot, when ids show a new call
        self.finish_reason: str | None = None
        self.usage: Usage = Usage()
        self.server_model: str | None = None
        self.warnings: list[str] = []
        self.message_extra: dict[str, Any] = {}
        self.n_chunks = 0

    def feed(self, t: float, chunk: dict[str, Any]) -> bool:
        """Consume one chunk received at time t. Returns True if it carried content."""
        self.n_chunks += 1
        self.server_model = self.server_model or chunk.get("model")
        self.warnings += _warnings(chunk)
        if chunk.get("usage"):
            self.usage = map_usage(chunk["usage"])
        has_content = False
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("extra_content"):  # Gemini thought signatures; must be echoed back
                self.message_extra["extra_content"] = delta["extra_content"]
            text = delta.get("content")
            if text:
                has_content = True
                if self.diffusing:
                    self.frames.append(Frame(t=t, text=text))
                else:
                    self.parts.append(text)
            for tc in delta.get("tool_calls") or []:
                has_content = True
                wire = tc.get("index", 0)
                key = self._slot_of.get(wire, wire)
                slot = self.tool_calls.get(key)
                if slot is not None and tc.get("id") and slot["id"] and tc["id"] != slot["id"]:
                    # Gemini streams parallel calls as complete calls that all say index 0;
                    # a new id at a used index is a new call, not more of the old one.
                    key = max(self.tool_calls) + 1
                    self._slot_of[wire] = key
                    slot = None
                if slot is None:
                    slot = self.tool_calls[key] = {
                        "id": None, "name": "", "arguments": "", "extra": {},
                    }  # fmt: skip
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
                slot["extra"].update(
                    {
                        k: v
                        for k, v in tc.items()
                        if k not in _KNOWN_TOOL_CALL_KEYS and v is not None
                    }
                )
        return has_content

    @property
    def content(self) -> str:
        if self.diffusing:
            return self.frames[-1].text if self.frames else ""
        return "".join(self.parts)

    def build_tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(
                id=s["id"] or f"call_{i}",
                name=s["name"],
                arguments=s["arguments"],
                extra=s["extra"],
            )
            for i, s in sorted(self.tool_calls.items())
        ]


def parse_response(d: dict[str, Any]) -> dict[str, Any]:
    """Non-streaming chat.completion dict -> ChatResult fields (minus timing/retries)."""
    choice = (d.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    tool_calls = [
        ToolCall(
            id=tc.get("id") or f"call_{i}",
            name=(tc.get("function") or {}).get("name", ""),
            arguments=(tc.get("function") or {}).get("arguments", ""),
            extra={k: v for k, v in tc.items() if k not in _KNOWN_TOOL_CALL_KEYS and v is not None},
        )
        for i, tc in enumerate(msg.get("tool_calls") or [])
    ]
    extra = {"extra_content": msg["extra_content"]} if msg.get("extra_content") else None
    return {
        "content": msg.get("content") or "",
        "tool_calls": tool_calls,
        "finish_reason": choice.get("finish_reason"),
        "usage": map_usage(d.get("usage")),
        "server_model": d.get("model"),
        "warnings": _warnings(d),
        "raw_assistant": extra,
        "n_chunks": 1,
    }


class OpenAICompatProvider(BaseProvider):
    # provider_extras keys this adapter accepts; anything else is a config error.
    ALLOWED_EXTRAS: frozenset[str] = frozenset()

    def __init__(self, *args: Any, client: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client = client or openai.AsyncOpenAI(
            api_key=os.environ.get(self.provider.api_key_env),
            base_url=self.provider.base_url,
            timeout=300,
            max_retries=0,  # BaseProvider owns retries so they are counted and classified
        )

    def validate(self, req: ChatRequest) -> None:
        super().validate(req)
        unknown = set(req.provider_extras) - self.ALLOWED_EXTRAS
        if unknown:
            raise ValueError(f"{self.model_key}: unsupported provider_extras {sorted(unknown)}")

    def message_to_wire(self, m: Message) -> dict[str, Any]:
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""}
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                    **tc.extra,
                }
                for tc in m.tool_calls
            ]
        if (
            m.role == "assistant"
            and m.raw_provider == self.provider_key
            and isinstance(m.raw, dict)
        ):
            out.update(m.raw)  # e.g. Gemini extra_content (thought signature)
        return out

    def extra_body(self, req: ChatRequest) -> dict[str, Any]:
        return {}

    def build_params(self, req: ChatRequest) -> dict[str, Any]:
        p: dict[str, Any] = {
            "model": self.model.model_id,
            "messages": [self.message_to_wire(m) for m in req.messages],
            self.provider.budget_param: req.max_completion_tokens,
        }
        if req.temperature is not None:
            p["temperature"] = req.temperature
        if req.reasoning_effort is not None and "reasoning_effort" not in self.extra_body(req):
            p["reasoning_effort"] = req.reasoning_effort
        if req.tools:
            p["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                        **({"strict": True} if t.strict else {}),
                    },
                }
                for t in req.tools
            ]
        if req.tool_choice is not None:
            p["tool_choice"] = req.tool_choice
        if req.response_format is not None:
            rf = req.response_format
            p["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": rf.name, "strict": rf.strict, "schema": rf.schema_},
            }
        body = self.extra_body(req)
        if body:
            p["extra_body"] = body
        if req.stream:
            p["stream"] = True
            p["stream_options"] = {"include_usage": True}
        return p

    def is_diffusing(self, req: ChatRequest) -> bool:
        return False

    async def _chat_once(self, req: ChatRequest) -> ChatResult:
        params = self.build_params(req)
        sw = Stopwatch(self.clock)
        sw.start()
        raw: list[Any] | None = [] if self.record_raw else None
        if not req.stream:
            resp = await self.client.chat.completions.create(**params)
            sw.mark_first_byte()
            d = resp.model_dump(mode="json")
            fields = parse_response(d)
            if fields["content"] or fields["tool_calls"]:
                sw.mark_content()
            if raw is not None:
                raw.append(d)
            return ChatResult(**fields, timing=sw.finish(), raw_response=raw)

        stream = await self.client.chat.completions.create(**params)
        sw.mark_first_byte()
        asm = OpenAIStreamAssembler(diffusing=self.is_diffusing(req))
        async for chunk in stream:
            t = sw.now()
            d = chunk.model_dump(mode="json")
            if asm.feed(t, d):
                sw.mark_content(t)
            if raw is not None:
                raw.append({"t": t, "chunk": d})
        return ChatResult(
            content=asm.content,
            tool_calls=asm.build_tool_calls(),
            finish_reason=asm.finish_reason,
            usage=asm.usage,
            timing=sw.finish(),
            server_model=asm.server_model,
            warnings=asm.warnings,
            n_chunks=asm.n_chunks,
            frames=asm.frames if asm.diffusing else None,
            raw_assistant=asm.message_extra or None,
            raw_response=raw,
        )
