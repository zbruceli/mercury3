import asyncio
import random
from typing import Any

import pytest

from dllm_eval.config import ModelRegistry
from dllm_eval.providers import build_provider
from dllm_eval.providers.anthropic import AnthropicProvider, AnthropicStreamAssembler
from dllm_eval.providers.base import ErrorKind, ProviderError, RateLimiter, RetryPolicy, classify
from dllm_eval.providers.inception import InceptionProvider
from dllm_eval.providers.openai_compat import OpenAICompatProvider, OpenAIStreamAssembler
from dllm_eval.schemas import ChatRequest, JsonSchemaFormat, Message, ToolCall, ToolSpec

WEATHER = ToolSpec(
    name="get_weather",
    description="Weather for a city",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    strict=True,
)


def req(model_key: str, **kw: Any) -> ChatRequest:
    base: dict[str, Any] = {
        "model_key": model_key,
        "messages": [Message(role="user", content="hi")],
        "max_completion_tokens": 64,
    }
    base.update(kw)
    return ChatRequest(**base)


def provider(repo_registry: ModelRegistry, key: str) -> Any:
    return build_provider(key, repo_registry, client=object())


# --- classification -------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "etype", "kind"),
    [
        (429, None, ErrorKind.RETRY),
        (503, None, ErrorKind.RETRY),
        (529, "overloaded_error", ErrorKind.RETRY),
        (None, None, ErrorKind.RETRY),  # connection error / timeout
        (429, "insufficient_quota", ErrorKind.RUN_FATAL),
        (401, None, ErrorKind.RUN_FATAL),
        (402, None, ErrorKind.RUN_FATAL),
        (404, None, ErrorKind.RUN_FATAL),
        (400, "invalid_request_error", ErrorKind.CALL_FATAL),
        (422, None, ErrorKind.CALL_FATAL),
    ],
)
def test_classify(status: int | None, etype: str | None, kind: ErrorKind) -> None:
    assert classify(status, etype) is kind


def test_retry_delay_is_bounded_full_jitter() -> None:
    p = RetryPolicy(base_delay_s=1, max_delay_s=8)
    rng = random.Random(0)
    for attempt in range(1, 10):
        d = p.delay(attempt, rng)
        assert 0 <= d <= min(8, 2 ** (attempt - 1))


# --- factory / validation -------------------------------------------------------


def test_factory_picks_adapter_and_shares_limiter(repo_registry: ModelRegistry) -> None:
    limiters: dict[str, RateLimiter] = {}
    m3 = build_provider("mercury-3", repo_registry, limiters=limiters, client=object())
    m25 = build_provider("mercury-2.5", repo_registry, limiters=limiters, client=object())
    assert isinstance(m3, InceptionProvider)
    assert m3.limiter is m25.limiter  # one Inception rate-limit window
    assert isinstance(provider(repo_registry, "claude-haiku-5-5"), AnthropicProvider)
    assert type(provider(repo_registry, "gpt-6-luna")) is OpenAICompatProvider


@pytest.mark.parametrize(
    ("key", "kw", "match"),
    [
        ("mercury-3", {"temperature": 0.2}, "temperature"),
        ("mercury-3", {"reasoning_effort": "minimal"}, "reasoning_effort"),
        ("mercury-3", {"max_completion_tokens": 200000}, "max_completion_tokens"),
        ("mercury-3", {"provider_extras": {"thinking": {}}}, "provider_extras"),
        ("mercury-3", {"provider_extras": {"diffusing": True}, "stream": False}, "diffusing"),
        ("gpt-6-luna", {"provider_extras": {"diffusing": True}}, "provider_extras"),
    ],
)
def test_validation_rejects_bad_settings(
    repo_registry: ModelRegistry, key: str, kw: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        provider(repo_registry, key).validate(req(key, **kw))


# --- request mapping ------------------------------------------------------------


def test_inception_puts_mercury_knobs_in_extra_body(repo_registry: ModelRegistry) -> None:
    p = provider(repo_registry, "mercury-3").build_params(
        req("mercury-3", reasoning_effort="instant", provider_extras={"diffusing": True})
    )
    assert "reasoning_effort" not in p
    assert p["extra_body"] == {"diffusing": True, "reasoning_effort": "instant"}
    assert p["max_completion_tokens"] == 64
    assert p["stream_options"] == {"include_usage": True}


def test_openai_compat_effort_is_top_level(repo_registry: ModelRegistry) -> None:
    p = provider(repo_registry, "gpt-6-luna").build_params(
        req("gpt-6-luna", reasoning_effort="none")
    )
    assert p["reasoning_effort"] == "none"
    assert "extra_body" not in p


def test_gemini_uses_max_tokens(repo_registry: ModelRegistry) -> None:
    p = provider(repo_registry, "gemini-3.5-flash-lite").build_params(req("gemini-3.5-flash-lite"))
    assert p["max_tokens"] == 64
    assert "max_completion_tokens" not in p


def test_openai_tools_schema_and_history(repo_registry: ModelRegistry) -> None:
    gemini = provider(repo_registry, "gemini-3.5-flash-lite")
    history = [
        Message(role="system", content="be brief"),
        Message(role="user", content="weather?"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="c1", name="get_weather", arguments='{"city":"Paris"}')],
            raw={"extra_content": {"google": {"thought_signature": "sig"}}},
            raw_provider="google",
        ),
        Message(role="tool", tool_call_id="c1", content="sunny"),
    ]
    p = gemini.build_params(
        req(
            "gemini-3.5-flash-lite",
            messages=history,
            tools=[WEATHER],
            tool_choice="auto",
            response_format=JsonSchemaFormat(name="A", schema={"type": "object"}),
        )
    )
    assert p["tools"][0]["function"]["strict"] is True
    assert p["response_format"]["json_schema"] == {
        "name": "A",
        "strict": True,
        "schema": {"type": "object"},
    }
    assistant = p["messages"][2]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"city":"Paris"}'
    assert assistant["extra_content"] == {"google": {"thought_signature": "sig"}}  # echoed back
    assert p["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "sunny"}


def test_raw_is_not_echoed_to_a_different_provider(repo_registry: ModelRegistry) -> None:
    luna = provider(repo_registry, "gpt-6-luna")
    m = Message(role="assistant", content="x", raw={"extra_content": 1}, raw_provider="google")
    assert "extra_content" not in luna.message_to_wire(m)


def test_anthropic_mapping(repo_registry: ModelRegistry) -> None:
    haiku = provider(repo_registry, "claude-haiku-5-5")
    history = [
        Message(role="system", content="be brief"),
        Message(role="user", content="weather in Paris and Rome?"),
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(id="t1", name="get_weather", arguments='{"city":"Paris"}'),
                ToolCall(id="t2", name="get_weather", arguments='{"city":"Rome"}'),
            ],
        ),
        Message(role="tool", tool_call_id="t1", content="sunny"),
        Message(role="tool", tool_call_id="t2", content="rain"),
    ]
    p = haiku.build_params(
        req(
            "claude-haiku-5-5",
            messages=history,
            reasoning_effort="low",
            tools=[WEATHER],
            tool_choice="required",
            response_format=JsonSchemaFormat(name="A", schema={"type": "object"}),
            provider_extras={"thinking": {"type": "disabled"}},
        )
    )
    assert p["system"] == "be brief"
    assert p["max_tokens"] == 64
    assert p["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    assert p["thinking"] == {"type": "disabled"}
    assert p["tool_choice"] == {"type": "any"}
    assert p["tools"][0]["input_schema"] == WEATHER.parameters
    assert p["messages"][1]["content"][1]["input"] == {"city": "Rome"}
    # both tool results in ONE user message (parallel tool use rule)
    results = p["messages"][2]
    assert results["role"] == "user"
    assert [b["tool_use_id"] for b in results["content"]] == ["t1", "t2"]


def test_anthropic_rejects_mid_conversation_system(repo_registry: ModelRegistry) -> None:
    msgs = [Message(role="user", content="a"), Message(role="system", content="b")]
    with pytest.raises(ValueError, match="system"):
        provider(repo_registry, "claude-haiku-5-5").validate(req("claude-haiku-5-5", messages=msgs))


def test_anthropic_replays_native_blocks(repo_registry: ModelRegistry) -> None:
    haiku = provider(repo_registry, "claude-haiku-5-5")
    blocks = [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "x"}]
    _, wire = haiku.messages_to_wire(
        [Message(role="assistant", raw=blocks, raw_provider="anthropic")]
    )
    assert wire == [{"role": "assistant", "content": blocks}]


# --- stream assembly (synthetic chunks for cases R0 did not record) ---------------


def test_openai_streamed_tool_call_deltas_are_joined() -> None:
    asm = OpenAIStreamAssembler()
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "weather", "arguments": '{"ci'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ty":"Paris"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 7}},
    ]  # fmt: skip
    assert all(asm.feed(i * 0.1, c) for i, c in enumerate(chunks[:3]))
    assert not asm.feed(0.3, chunks[3])
    [call] = asm.build_tool_calls()
    assert (call.id, call.name, call.parsed()) == ("c1", "get_weather", {"city": "Paris"})
    assert asm.finish_reason == "tool_calls"
    assert asm.usage.reasoning_tokens is None  # no details object -> unknown


def test_parallel_calls_streamed_at_one_index_stay_separate() -> None:
    """Gemini's compat layer streams each parallel call whole, all at index 0 (R1 UC2 pilot:
    two describe_table calls were merged into 'describe_tabledescribe_table')."""
    asm = OpenAIStreamAssembler()
    sig = {"google": {"thought_signature": "s1"}}
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "type": "function", "extra_content": sig,
                                                 "function": {"name": "describe_table", "arguments": '{"name":"bond"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "b", "type": "function",
                                                 "function": {"name": "describe_table", "arguments": '{"name":"molecule"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"id": "c", "function": {"name": "list_tables", "arguments": "{}"}}]},
                      "finish_reason": "tool_calls"}]},
    ]  # fmt: skip
    for i, c in enumerate(chunks):
        asm.feed(i * 0.1, c)
    calls = asm.build_tool_calls()
    assert [(c.id, c.name, c.parsed()) for c in calls] == [
        ("a", "describe_table", {"name": "bond"}),
        ("b", "describe_table", {"name": "molecule"}),
        ("c", "list_tables", {}),
    ]
    assert calls[0].extra == {"extra_content": sig} and calls[1].extra == {}


def test_parallel_calls_with_null_index() -> None:
    """R4: Gemini also sends "index": null; that crashed the slot logic (TypeError)."""
    asm = OpenAIStreamAssembler()
    for i, cid in enumerate(["a", "b"]):
        asm.feed(i * 0.1, {"choices": [{"delta": {"tool_calls": [
            {"index": None, "id": cid, "function": {"name": "run_sql", "arguments": f'{{"query":"{cid}"}}'}}
        ]}}]})  # fmt: skip
    assert [(c.id, c.parsed()) for c in asm.build_tool_calls()] == [
        ("a", {"query": "a"}),
        ("b", {"query": "b"}),
    ]


def test_anthropic_streamed_tool_and_thinking() -> None:
    asm = AnthropicStreamAssembler()
    events = [
        {"type": "message_start", "message": {"model": "m", "usage": {"input_tokens": 5, "cache_read_input_tokens": 3}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "f", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"a": '}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "1}"}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9, "output_tokens_details": {"thinking_tokens": 4}}},
    ]  # fmt: skip
    content_flags = [asm.feed(0.0, e) for e in events]
    assert content_flags == [
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        False,
    ]  # thinking is not TTFT
    blocks = asm.final_blocks()
    assert blocks[0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    assert blocks[1]["input"] == {"a": 1}
    from dllm_eval.providers.anthropic import map_usage

    u = map_usage(asm.usage_raw)
    assert (u.input_tokens, u.cached_input_tokens, u.output_tokens, u.reasoning_tokens) == (
        8,
        3,
        9,
        4,
    )


# --- retry loop + rate limiter ----------------------------------------------------


class _NoSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


def test_chat_retries_then_succeeds(fake_registry: ModelRegistry) -> None:
    sleep = _NoSleep()
    p = build_provider("fake-ar", fake_registry, sleep=sleep, rng=random.Random(0))
    r = asyncio.run(p.chat(req("fake-ar", provider_extras={"fake_fail": [503, 429]})))
    assert r.retries == 2
    assert len(sleep.calls) == 2


def test_chat_gives_up_after_max_attempts(fake_registry: ModelRegistry) -> None:
    p = build_provider(
        "fake-ar", fake_registry, sleep=_NoSleep(), retry=RetryPolicy(max_attempts=3)
    )
    with pytest.raises(ProviderError) as e:
        asyncio.run(p.chat(req("fake-ar", provider_extras={"fake_fail": [503] * 5})))
    assert e.value.kind is ErrorKind.CALL_FATAL
    assert e.value.attempts == 3


def test_chat_does_not_retry_billing(fake_registry: ModelRegistry) -> None:
    sleep = _NoSleep()
    p = build_provider("fake-ar", fake_registry, sleep=sleep)
    with pytest.raises(ProviderError) as e:
        asyncio.run(p.chat(req("fake-ar", provider_extras={"fake_fail": ["quota"]})))
    assert e.value.kind is ErrorKind.RUN_FATAL
    assert sleep.calls == []


def test_rate_limiter_blocks_until_window_drains() -> None:
    from dllm_eval.config import RateLimits

    now = [0.0]
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    lim = RateLimiter(
        RateLimits(requests_per_min=2, output_tokens_per_min=100), clock=lambda: now[0], sleep=sleep
    )

    async def go() -> None:
        await lim.acquire()
        await lim.acquire()
        await lim.acquire()  # 3rd request in the same minute must wait ~60 s

    asyncio.run(go())
    assert slept and sum(slept) == pytest.approx(60.0)

    now[0] = 1000.0
    lim.record(0, 150)  # over the output budget

    async def go2() -> None:
        await lim.acquire()

    slept.clear()
    asyncio.run(go2())
    assert sum(slept) == pytest.approx(60.0)
