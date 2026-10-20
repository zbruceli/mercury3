"""TEST_PLAN A2: replay real R0 responses through the production parsers.

If a provider changes its response shape, re-record with scripts/smoke_r0.py and these fail.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from dllm_eval.providers import anthropic as anth
from dllm_eval.providers import openai_compat as oai
from dllm_eval.providers.base import ErrorKind, classify, error_details
from tests.conftest import RECORDINGS

R0 = RECORDINGS / "r0"
OPENAI_COMPAT_MODELS = ["mercury-3", "mercury-2.5", "gpt-6-luna", "gemini-3.5-flash-lite", "gpt-oss-120b-cerebras"]  # fmt: skip


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def replay_openai(rec: dict[str, Any], *, diffusing: bool = False) -> oai.OpenAIStreamAssembler:
    asm = oai.OpenAIStreamAssembler(diffusing=diffusing)
    for c in rec["chunks"]:
        asm.feed(c["t"], c["chunk"])
    return asm


@pytest.mark.parametrize("model", OPENAI_COMPAT_MODELS)
def test_openai_compat_stream(model: str) -> None:
    asm = replay_openai(load(R0 / model / "stream.json"))
    assert asm.content.strip().lower().rstrip(".") == "ready"
    assert asm.finish_reason == "stop"
    assert asm.usage.input_tokens and asm.usage.output_tokens
    assert asm.server_model


@pytest.mark.parametrize("model", OPENAI_COMPAT_MODELS)
def test_openai_compat_plain(model: str) -> None:
    f = oai.parse_response(load(R0 / model / "plain.json")["response"])
    assert f["content"].strip().lower().rstrip(".") == "ready"
    assert f["usage"].output_tokens


@pytest.mark.parametrize("model", [m for m in OPENAI_COMPAT_MODELS if m != "mercury-2.5"])
def test_openai_compat_tool_call(model: str) -> None:
    f = oai.parse_response(load(R0 / model / "tool.json")["response"])
    assert f["finish_reason"] == "tool_calls"
    [call] = f["tool_calls"]
    assert call.name == "get_weather"
    assert call.parsed() == {"city": "Paris"}


@pytest.mark.parametrize("model", [m for m in OPENAI_COMPAT_MODELS if m != "mercury-2.5"])
def test_openai_compat_schema(model: str) -> None:
    f = oai.parse_response(load(R0 / model / "schema.json")["response"])
    assert set(json.loads(f["content"])) == {"answer", "confidence"}


def test_gemini_usage_details_are_unknown_not_zero() -> None:
    """M0 F3: the Gemini compat layer omits token details."""
    f = oai.parse_response(load(R0 / "gemini-3.5-flash-lite" / "plain.json")["response"])
    assert f["usage"].reasoning_tokens is None
    assert f["usage"].cached_input_tokens is None


def test_gemini_thought_signature_is_kept_for_replay() -> None:
    asm = replay_openai(load(R0 / "gemini-3.5-flash-lite" / "stream.json"))
    assert "google" in asm.message_extra["extra_content"]


def test_mercury_reports_reasoning_and_cache() -> None:
    f = oai.parse_response(load(R0 / "mercury-3" / "effort_low.json")["response"])
    assert f["usage"].reasoning_tokens == 56
    assert f["usage"].cached_input_tokens == 0


def test_mercury_temperature_warning_is_captured() -> None:
    """M0 F2 / PRD R2: the silent reset must surface as a warning."""
    f = oai.parse_response(load(R0 / "mercury-3" / "temperature_out_of_range.json")["response"])
    assert f["warnings"] == [
        "Warning: Requested temperature 0.2 is not within [0.5, 1]. Temperature has been set to 1."
    ]


def test_mercury_diffusing_replaces_text_and_keeps_frames() -> None:
    rec = load(R0 / "mercury-3" / "diffusing.json")
    asm = replay_openai(rec, diffusing=True)
    assert len(asm.frames) == 10
    assert asm.content == asm.frames[-1].text
    assert asm.content.startswith("Here's a Python function")
    assert len(asm.content) == 3127  # not the concatenation of all frames
    assert asm.usage.output_tokens == 755


def test_mercury_tool_call_keeps_preamble_text() -> None:
    f = oai.parse_response(load(R0 / "mercury-3" / "tool.json")["response"])
    assert f["content"].startswith("I'll get the current weather")
    assert f["tool_calls"]


def test_anthropic_stream() -> None:
    asm = anth.AnthropicStreamAssembler()
    for c in load(R0 / "claude-haiku-5-5" / "stream.json")["chunks"]:
        asm.feed(c["t"], c["event"])
    text, calls = anth._blocks_to_fields(asm.final_blocks())
    usage = anth.map_usage(asm.usage_raw)
    assert text == "Ready"
    assert calls == []
    assert asm.stop_reason == "end_turn"
    assert usage.input_tokens == 16
    assert usage.output_tokens == 5
    assert usage.reasoning_tokens == 0  # M0 F5: thinking_tokens mapped
    assert asm.server_model == "claude-haiku-5-5"


def test_anthropic_tool_call() -> None:
    f = anth.parse_response(load(R0 / "claude-haiku-5-5" / "tool.json")["response"])
    [call] = f["tool_calls"]
    assert call.name == "get_weather"
    assert call.parsed() == {"city": "Paris"}
    assert f["raw_assistant"][0]["type"] == "tool_use"
    assert "caller" not in f["raw_assistant"][0]  # only replayable fields are kept


def test_anthropic_schema() -> None:
    f = anth.parse_response(load(R0 / "claude-haiku-5-5" / "schema.json")["response"])
    assert set(json.loads(f["content"])) == {"answer", "confidence"}


def test_openai_insufficient_quota_is_run_fatal() -> None:
    """M0 F1: a 429 that is really an empty balance must not be retried."""
    body = load(RECORDINGS / "errors" / "openai_429_insufficient_quota.json")["body"]

    class FakeError(Exception):
        status_code = 429

    for shaped in (body, body["error"]):  # SDKs expose either the full or the inner body
        e = FakeError()
        e.body = shaped  # type: ignore[attr-defined]
        status, etype = error_details(e)
        assert classify(status, etype) is ErrorKind.RUN_FATAL
