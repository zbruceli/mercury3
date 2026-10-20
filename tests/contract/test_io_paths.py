"""Drive the real adapters' _chat_once through a stub SDK client that replays R0 recordings.

Covers the I/O path (stream iteration, timing marks, raw capture) without any network.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dllm_eval.config import ModelRegistry
from dllm_eval.providers import build_provider
from dllm_eval.schemas import ChatRequest, Message
from tests.conftest import RECORDINGS

R0 = RECORDINGS / "r0"


class _Obj:
    def __init__(self, d: dict[str, Any]) -> None:
        self._d = d

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return self._d


class _Stream:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> "_Stream":
        return self

    async def __anext__(self) -> _Obj:
        try:
            return _Obj(next(self._items))
        except StopIteration:
            raise StopAsyncIteration from None


def stub_client(path: Path, key: str) -> tuple[Any, list[dict[str, Any]]]:
    rec = json.loads(path.read_text())
    sent: list[dict[str, Any]] = []

    async def create(**params: Any) -> Any:
        sent.append(params)
        if "chunks" in rec:
            return _Stream([c[key] for c in rec["chunks"]])
        return _Obj(rec["response"])

    ns = SimpleNamespace(create=create)
    return SimpleNamespace(chat=SimpleNamespace(completions=ns), messages=ns), sent


def clock() -> Any:
    t = [0.0]

    def tick() -> float:
        t[0] += 0.01
        return t[0]

    return tick


def ask(model: str, **kw: Any) -> ChatRequest:
    return ChatRequest(
        model_key=model,
        messages=[Message(role="user", content="Reply with the single word: ready")],
        max_completion_tokens=64,
        **kw,
    )


def test_mercury_stream_io(repo_registry: ModelRegistry) -> None:
    client, sent = stub_client(R0 / "mercury-3" / "stream.json", "chunk")
    p = build_provider("mercury-3", repo_registry, client=client, clock=clock(), record_raw=True)
    r = asyncio.run(p.chat(ask("mercury-3", reasoning_effort="instant")))
    assert r.content == "ready"
    assert r.usage.output_tokens == 5
    assert r.timing.t_first_byte is not None
    assert r.timing.ttft is not None and r.timing.ttft < r.timing.total_s
    assert r.n_chunks == 3 and len(r.raw_response) == 3
    assert sent[0]["extra_body"] == {"reasoning_effort": "instant"}


def test_mercury_diffusing_io(repo_registry: ModelRegistry) -> None:
    client, _ = stub_client(R0 / "mercury-3" / "diffusing.json", "chunk")
    p = build_provider("mercury-3", repo_registry, client=client, clock=clock())
    r = asyncio.run(p.chat(ask("mercury-3", provider_extras={"diffusing": True})))
    assert r.frames is not None and len(r.frames) == 10
    assert r.content == r.frames[-1].text
    assert r.raw_response is None  # record_raw off


def test_openai_compat_nonstream_io(repo_registry: ModelRegistry) -> None:
    client, sent = stub_client(R0 / "gpt-6-luna" / "tool.json", "chunk")
    p = build_provider("gpt-6-luna", repo_registry, client=client, clock=clock())
    r = asyncio.run(p.chat(ask("gpt-6-luna", reasoning_effort="none", stream=False)))
    assert r.tool_calls[0].parsed() == {"city": "Paris"}
    assert r.timing.ttft is not None
    assert "stream" not in sent[0]


def test_anthropic_stream_io(repo_registry: ModelRegistry) -> None:
    client, sent = stub_client(R0 / "claude-haiku-5-5" / "stream.json", "event")
    p = build_provider(
        "claude-haiku-5-5", repo_registry, client=client, clock=clock(), record_raw=True
    )
    r = asyncio.run(
        p.chat(
            ask(
                "claude-haiku-5-5",
                reasoning_effort="low",
                provider_extras={"thinking": {"type": "disabled"}},
            )
        )
    )
    assert r.content == "Ready"
    assert r.usage.reasoning_tokens == 0
    assert r.finish_reason == "end_turn"
    assert r.raw_assistant == [{"type": "text", "text": "Ready"}]
    assert sent[0]["thinking"] == {"type": "disabled"} and sent[0]["stream"] is True


def test_anthropic_nonstream_io(repo_registry: ModelRegistry) -> None:
    client, _ = stub_client(R0 / "claude-haiku-5-5" / "tool.json", "event")
    p = build_provider(
        "claude-haiku-5-5", repo_registry, client=client, clock=clock(), record_raw=True
    )
    r = asyncio.run(p.chat(ask("claude-haiku-5-5", stream=False)))
    assert r.tool_calls[0].name == "get_weather"
    assert r.raw_response and r.raw_response[0]["type"] == "message"
    # the assistant turn can go straight back into the next request
    msg = r.as_message("anthropic")
    assert msg.raw_provider == "anthropic" and msg.raw[0]["type"] == "tool_use"
