"""Anthropic Messages API adapter for Claude (F1.4), via the official SDK.

Same split as openai_compat: pure assemblers over dumped events, then thin I/O.
"""

from __future__ import annotations

import json
import os
from typing import Any

import anthropic

from dllm_eval.providers.base import BaseProvider
from dllm_eval.schemas import ChatRequest, ChatResult, Message, ToolCall, Usage
from dllm_eval.timing import Stopwatch


def map_usage(u: dict[str, Any] | None) -> Usage:
    """Anthropic's input_tokens excludes cache reads/writes; Usage.input_tokens includes them."""
    if not u:
        return Usage()
    base = u.get("input_tokens")
    read = u.get("cache_read_input_tokens")
    write = u.get("cache_creation_input_tokens")
    details = u.get("output_tokens_details") or None
    return Usage(
        input_tokens=None if base is None else base + (read or 0) + (write or 0),
        cached_input_tokens=read,
        cache_write_tokens=write,
        output_tokens=u.get("output_tokens"),
        # M0 F5: thinking is reported separately; absent means unknown, not zero.
        reasoning_tokens=details.get("thinking_tokens") if details else None,
    )


def _merge_usage(old: dict[str, Any], new: dict[str, Any] | None) -> dict[str, Any]:
    """message_delta usage is cumulative; keep non-null fields from message_start."""
    merged = dict(old)
    for k, v in (new or {}).items():
        if v is not None:
            merged[k] = v
    return merged


def _blocks_to_fields(blocks: list[dict[str, Any]]) -> tuple[str, list[ToolCall]]:
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = [
        ToolCall(id=b["id"], name=b["name"], arguments=json.dumps(b.get("input") or {}))
        for b in blocks
        if b.get("type") == "tool_use"
    ]
    return text, calls


def _replay_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only what the API accepts back in an assistant turn (thinking must be echoed)."""
    keep = {
        "text": ("type", "text"),
        "tool_use": ("type", "id", "name", "input"),
        "thinking": ("type", "thinking", "signature"),
        "redacted_thinking": ("type", "data"),
    }
    out = []
    for b in blocks:
        fields = keep.get(b.get("type", ""))
        if fields:
            out.append({k: b[k] for k in fields if k in b})
    return out


class AnthropicStreamAssembler:
    def __init__(self) -> None:
        self.blocks: dict[int, dict[str, Any]] = {}
        self.json_parts: dict[int, list[str]] = {}
        self.usage_raw: dict[str, Any] = {}
        self.stop_reason: str | None = None
        self.server_model: str | None = None
        self.n_chunks = 0

    def feed(self, t: float, ev: dict[str, Any]) -> bool:
        """Consume one stream event. Returns True for visible text or tool-input deltas."""
        self.n_chunks += 1
        kind = ev.get("type")
        if kind == "message_start":
            msg = ev.get("message") or {}
            self.server_model = msg.get("model")
            self.usage_raw = _merge_usage(self.usage_raw, msg.get("usage"))
        elif kind == "content_block_start":
            self.blocks[ev["index"]] = dict(ev.get("content_block") or {})
        elif kind == "content_block_delta":
            block = self.blocks.setdefault(ev["index"], {})
            delta = ev.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta" and delta.get("text"):
                block["text"] = block.get("text", "") + delta["text"]
                return True
            if dt == "input_json_delta":
                self.json_parts.setdefault(ev["index"], []).append(delta.get("partial_json", ""))
                return bool(delta.get("partial_json"))
            if dt == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
            elif dt == "signature_delta":
                block["signature"] = delta.get("signature")
        elif kind == "message_delta":
            self.stop_reason = (ev.get("delta") or {}).get("stop_reason") or self.stop_reason
            self.usage_raw = _merge_usage(self.usage_raw, ev.get("usage"))
        return False

    def final_blocks(self) -> list[dict[str, Any]]:
        out = []
        for i, block in sorted(self.blocks.items()):
            b = dict(block)
            if b.get("type") == "tool_use":
                raw = "".join(self.json_parts.get(i, []))
                try:
                    b["input"] = json.loads(raw) if raw else (b.get("input") or {})
                except ValueError:
                    b["input"] = {"_unparsed": raw}
            out.append(b)
        return out


def parse_response(d: dict[str, Any]) -> dict[str, Any]:
    blocks = d.get("content") or []
    text, calls = _blocks_to_fields(blocks)
    return {
        "content": text,
        "tool_calls": calls,
        "finish_reason": d.get("stop_reason"),
        "usage": map_usage(d.get("usage")),
        "server_model": d.get("model"),
        "raw_assistant": _replay_blocks(blocks),
        "n_chunks": 1,
    }


class AnthropicProvider(BaseProvider):
    # thinking: e.g. {"type": "disabled"} for Haiku's thinking-off arm (allowed at effort <= high)
    ALLOWED_EXTRAS = frozenset({"thinking"})

    def __init__(self, *args: Any, client: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client = client or anthropic.AsyncAnthropic(
            api_key=os.environ.get(self.provider.api_key_env),
            timeout=300,
            max_retries=0,
        )

    def validate(self, req: ChatRequest) -> None:
        super().validate(req)
        unknown = set(req.provider_extras) - self.ALLOWED_EXTRAS
        if unknown:
            raise ValueError(f"{self.model_key}: unsupported provider_extras {sorted(unknown)}")
        seen_other = False
        for m in req.messages:
            if m.role != "system":
                seen_other = True
            elif seen_other:
                raise ValueError("system messages are only supported at the start of the history")

    def messages_to_wire(self, messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        system = "\n\n".join(m.content or "" for m in messages if m.role == "system") or None
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content or "",
                }
                # All results for one assistant turn go in a single user message.
                if (
                    out
                    and out[-1]["role"] == "user"
                    and isinstance(out[-1]["content"], list)
                    and all(b.get("type") == "tool_result" for b in out[-1]["content"])
                ):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m.role == "assistant":
                if m.raw_provider == self.provider_key and isinstance(m.raw, list):
                    out.append({"role": "assistant", "content": m.raw})
                    continue
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.parsed() or {},
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "user", "content": m.content or ""})
        return system, out

    def build_params(self, req: ChatRequest) -> dict[str, Any]:
        system, messages = self.messages_to_wire(req.messages)
        p: dict[str, Any] = {
            "model": self.model.model_id,
            "max_tokens": req.max_completion_tokens,
            "messages": messages,
        }
        if system:
            p["system"] = system
        if req.temperature is not None:
            p["temperature"] = req.temperature
        output_config: dict[str, Any] = {}
        if req.reasoning_effort is not None:
            output_config["effort"] = req.reasoning_effort
        if req.response_format is not None:
            output_config["format"] = {"type": "json_schema", "schema": req.response_format.schema_}
        if output_config:
            p["output_config"] = output_config
        if "thinking" in req.provider_extras:
            p["thinking"] = req.provider_extras["thinking"]
        if req.tools:
            p["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                    **({"strict": True} if t.strict else {}),
                }
                for t in req.tools
            ]
        if req.tool_choice is not None:
            p["tool_choice"] = {"type": {"required": "any"}.get(req.tool_choice, req.tool_choice)}
        if req.stream:
            p["stream"] = True
        return p

    async def _chat_once(self, req: ChatRequest) -> ChatResult:
        params = self.build_params(req)
        sw = Stopwatch(self.clock)
        sw.start()
        raw: list[Any] | None = [] if self.record_raw else None
        if not req.stream:
            resp = await self.client.messages.create(**params)
            sw.mark_first_byte()
            d = resp.model_dump(mode="json")
            fields = parse_response(d)
            if fields["content"] or fields["tool_calls"]:
                sw.mark_content()
            if raw is not None:
                raw.append(d)
            return ChatResult(**fields, timing=sw.finish(), raw_response=raw)

        stream = await self.client.messages.create(**params)
        sw.mark_first_byte()
        asm = AnthropicStreamAssembler()
        async for event in stream:
            t = sw.now()
            d = event.model_dump(mode="json")
            if asm.feed(t, d):
                sw.mark_content(t)
            if raw is not None:
                raw.append({"t": t, "event": d})
        blocks = asm.final_blocks()
        text, calls = _blocks_to_fields(blocks)
        return ChatResult(
            content=text,
            tool_calls=calls,
            finish_reason=asm.stop_reason,
            usage=map_usage(asm.usage_raw),
            timing=sw.finish(),
            server_model=asm.server_model,
            n_chunks=asm.n_chunks,
            raw_assistant=_replay_blocks(blocks),
            raw_response=raw,
        )
