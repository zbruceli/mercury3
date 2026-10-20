"""Provider-agnostic request/response schemas (F1.1). Use cases only ever see these."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(_Model):
    id: str
    name: str
    arguments: str  # raw JSON string as the model produced it; may be invalid
    extra: dict[str, Any] = Field(default_factory=dict)  # opaque provider fields to echo back

    def parsed(self) -> dict[str, Any] | None:
        """Arguments as a dict, or None if they are not a JSON object."""
        try:
            value = json.loads(self.arguments)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None


class Message(_Model):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # role == "tool"
    # Provider-native assistant turn (Anthropic content blocks, Gemini thought signatures).
    # Replayed verbatim when the same provider gets the history back.
    raw: Any = None
    raw_provider: str | None = None


class ToolSpec(_Model):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    strict: bool = False


class JsonSchemaFormat(_Model):
    name: str
    schema_: dict[str, Any] = Field(alias="schema")
    strict: bool = True

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ChatRequest(_Model):
    model_key: str  # key into models.yaml, never a raw vendor id
    messages: list[Message]
    max_completion_tokens: int = Field(gt=0)
    temperature: float | None = None
    reasoning_effort: str | None = None  # vendor value; validated against models.yaml
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: Literal["auto", "none", "required"] | None = None
    response_format: JsonSchemaFormat | None = None
    stream: bool = True
    # Vendor-only knobs, e.g. {"diffusing": True}, {"realtime": True},
    # {"thinking": {"type": "disabled"}}. Each adapter documents what it accepts.
    provider_extras: dict[str, Any] = Field(default_factory=dict)


class Usage(_Model):
    """Server-reported token counts. None means the provider did not report it (not zero)."""

    input_tokens: int | None = None  # includes cached
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None  # includes reasoning
    reasoning_tokens: int | None = None


class Timing(_Model):
    """Seconds relative to the moment the request was sent (t_sent == 0)."""

    t_first_byte: float | None = None  # response headers received
    t_first_content: float | None = None  # first non-empty text or tool-call delta
    t_last_content: float | None = None
    t_done: float

    @property
    def ttft(self) -> float | None:
        return self.t_first_content

    @property
    def decode_s(self) -> float | None:
        if self.t_first_content is None or self.t_last_content is None:
            return None
        return self.t_last_content - self.t_first_content

    @property
    def total_s(self) -> float:
        return self.t_done


class Frame(_Model):
    """One diffusing-mode snapshot: the full current text at time t."""

    t: float
    text: str


class ChatResult(_Model):
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage
    timing: Timing
    server_model: str | None = None
    warnings: list[str] = Field(default_factory=list)
    retries: int = 0
    n_chunks: int = 0
    frames: list[Frame] | None = None  # diffusing only
    raw_assistant: Any = None  # provider-native assistant turn, see Message.raw
    raw_response: Any = None  # every raw chunk/event (only when record_raw is on)

    def as_message(self, provider: str) -> Message:
        """The assistant turn to append to the history for the next request."""
        return Message(
            role="assistant",
            content=self.content or None,
            tool_calls=self.tool_calls,
            raw=self.raw_assistant,
            raw_provider=provider if self.raw_assistant is not None else None,
        )
