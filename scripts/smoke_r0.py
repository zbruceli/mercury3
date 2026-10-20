"""R0 live smoke checks (TEST_PLAN A3 / B5): one small call per capability per model.

SPENDS REAL TOKENS (< 50k total). Run only with explicit approval:

    uv run --env-file .env python scripts/smoke_r0.py

Each check's request params and response (non-stream: the completion; stream: every
chunk/event) are saved to tests/fixtures/recordings/r0/<model>/<check>.json as contract
fixtures. Requests never contain credentials and response headers are not recorded; the
script also scans every recording for any configured API key before exiting.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
import openai

from dllm_eval.config import Model, Provider, ProviderKind, load_registry

ROOT = Path(__file__).parents[1]
OUT = ROOT / "tests" / "fixtures" / "recordings" / "r0"
PROMPT = "Reply with the single word: ready"
TOOL_PROMPT = "What is the weather in Paris right now? Use the get_weather tool."
# Same prompt style as the original article's Mercury Coder animation (BLOG_ALIGNMENT C2).
DIFFUSION_PROMPT = "Write a Python function that splits an image into left and right halves."
SCHEMA_PROMPT = "What is the capital of France? Answer in the requested JSON format."

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["answer", "confidence"],
    "additionalProperties": False,
}

# Lowest reasoning setting per model (keeps smoke calls cheap) and token-budget field name.
LOWEST_EFFORT = {
    "mercury-3": "instant",
    "mercury-2.5": "instant",
    "gpt-6-luna": "none",
    "gemini-3.5-flash-lite": "minimal",
    "gpt-oss-120b-cerebras": "low",
}
BUDGET_FIELD = {"gemini-3.5-flash-lite": "max_tokens"}  # others: max_completion_tokens
MAX_TOKENS = 1024


def dump(obj: Any) -> Any:
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


class OpenAICompat:
    def __init__(self, key: str, model: Model, provider: Provider) -> None:
        self.key = key
        self.model_id = model.model_id
        self.is_inception = model.provider == "inception"
        self.client = openai.OpenAI(
            api_key=os.environ[provider.api_key_env],
            base_url=provider.base_url,
            timeout=120,
            max_retries=2,
        )

    def params(self, effort: str | None = None, **extra: Any) -> dict[str, Any]:
        p: dict[str, Any] = {
            "model": self.model_id,
            BUDGET_FIELD.get(self.key, "max_completion_tokens"): MAX_TOKENS,
        }
        body: dict[str, Any] = dict(extra.pop("extra_body", {}))
        effort = effort or LOWEST_EFFORT[self.key]
        if self.is_inception:
            body["reasoning_effort"] = effort  # Mercury-only knobs go through extra_body
        else:
            p["reasoning_effort"] = effort
        if body:
            p["extra_body"] = body
        p.update(extra)
        return p

    def call(self, params: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        if not params.get("stream"):
            resp = self.client.chat.completions.create(**params)
            return {"elapsed_s": time.perf_counter() - t0, "response": dump(resp)}
        chunks, ttft = [], None
        for chunk in self.client.chat.completions.create(**params):
            d = dump(chunk)
            chunks.append({"t": time.perf_counter() - t0, "chunk": d})
            choices = d.get("choices") or []
            if ttft is None and choices and (choices[0].get("delta") or {}).get("content"):
                ttft = time.perf_counter() - t0
        return {"elapsed_s": time.perf_counter() - t0, "ttft_s": ttft, "chunks": chunks}

    def checks(self) -> dict[str, Callable[[], dict[str, Any]]]:
        messages = [{"role": "user", "content": PROMPT}]
        tool = {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city.",
                "parameters": WEATHER_SCHEMA,
            },
        }
        schema = {
            "type": "json_schema",
            "json_schema": {"name": "Answer", "strict": True, "schema": ANSWER_SCHEMA},
        }
        c: dict[str, dict[str, Any]] = {
            "plain": self.params(messages=messages),
            "stream": self.params(
                messages=messages, stream=True, stream_options={"include_usage": True}
            ),
        }
        if self.key != "mercury-2.5":
            c["tool"] = self.params(
                messages=[{"role": "user", "content": TOOL_PROMPT}],
                tools=[tool],
                tool_choice="auto",
            )
            c["schema"] = self.params(
                messages=[{"role": "user", "content": SCHEMA_PROMPT}], response_format=schema
            )
        if self.key == "mercury-3":
            for effort in ("instant", "low", "medium", "high"):
                c[f"effort_{effort}"] = self.params(
                    effort,
                    messages=[
                        {"role": "user", "content": "What is 17 * 23? Answer with the number only."}
                    ],
                    max_completion_tokens=4096,
                )
            c["diffusing"] = self.params(
                messages=[
                    {
                        "role": "user",
                        "content": DIFFUSION_PROMPT,
                    }
                ],
                stream=True,
                stream_options={"include_usage": True},
                extra_body={"diffusing": True},
            )
            c["temperature_out_of_range"] = self.params(messages=messages, temperature=0.2)
            c["realtime"] = self.params(
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                extra_body={"realtime": True},
            )
        return {name: (lambda p=p: {"request": p, **self.call(p)}) for name, p in c.items()}


class AnthropicNative:
    def __init__(self, key: str, model: Model, provider: Provider) -> None:
        self.key = key
        self.model_id = model.model_id
        self.client = anthropic.Anthropic(
            api_key=os.environ[provider.api_key_env], timeout=120, max_retries=2
        )

    def params(self, **extra: Any) -> dict[str, Any]:
        p: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": MAX_TOKENS,
            "thinking": {"type": "disabled"},
            "output_config": {"effort": "low"},
        }
        p.update(extra)
        return p

    def call(self, params: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        if not params.get("stream"):
            resp = self.client.messages.create(**params)
            return {"elapsed_s": time.perf_counter() - t0, "response": dump(resp)}
        events, ttft = [], None
        for ev in self.client.messages.create(**params):
            d = dump(ev)
            events.append({"t": time.perf_counter() - t0, "event": d})
            if (
                ttft is None
                and d.get("type") == "content_block_delta"
                and (d.get("delta") or {}).get("text")
            ):
                ttft = time.perf_counter() - t0
        return {"elapsed_s": time.perf_counter() - t0, "ttft_s": ttft, "chunks": events}

    def checks(self) -> dict[str, Callable[[], dict[str, Any]]]:
        messages = [{"role": "user", "content": PROMPT}]
        tool = {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "strict": True,
            "input_schema": WEATHER_SCHEMA,
        }
        c: dict[str, dict[str, Any]] = {
            "plain": self.params(messages=messages),
            "stream": self.params(messages=messages, stream=True),
            "tool": self.params(
                messages=[{"role": "user", "content": TOOL_PROMPT}],
                tools=[tool],
                tool_choice={"type": "auto"},
            ),
            "schema": self.params(
                messages=[{"role": "user", "content": SCHEMA_PROMPT}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                },
            ),
            "effort_medium_adaptive": {
                "model": self.model_id,
                "max_tokens": 4096,
                "output_config": {"effort": "medium"},
                "messages": [
                    {"role": "user", "content": "What is 17 * 23? Answer with the number only."}
                ],
            },
        }
        return {name: (lambda p=p: {"request": p, **self.call(p)}) for name, p in c.items()}


def summarize(key: str, check: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields we care about out of a recording, provider-agnostically."""
    s: dict[str, Any] = {
        "model": key,
        "check": check,
        "ok": "error" not in rec,
        "elapsed_s": round(rec.get("elapsed_s", 0), 3),
    }
    if "error" in rec:
        s["error"] = rec["error"][:300]
        return s
    if rec.get("ttft_s") is not None:
        s["ttft_s"] = round(rec["ttft_s"], 3)
    if "chunks" in rec:
        s["n_chunks"] = len(rec["chunks"])
        payloads = [c.get("chunk") or c.get("event") or {} for c in rec["chunks"]]
        usage = next((p.get("usage") for p in reversed(payloads) if p.get("usage")), None)
        if usage is None:  # anthropic: usage is on message_delta / message_start
            usage = next(
                (p["usage"] for p in reversed(payloads) if p.get("type") == "message_delta"), None
            )
        s["usage"] = usage
        s["server_model"] = next(
            (
                p.get("model") or (p.get("message") or {}).get("model")
                for p in payloads
                if p.get("model") or p.get("message")
            ),
            None,
        )
        texts = [
            ((p.get("choices") or [{}])[0].get("delta") or {}).get("content")
            for p in payloads
            if p.get("choices")
        ]
        texts = [t for t in texts if t]
        if check == "diffusing" and texts:
            s["frames"] = len(texts)
            s["first_frame"] = texts[0][:80]
            s["last_frame_len"] = len(texts[-1])
    else:
        r = rec["response"]
        s["usage"] = r.get("usage")
        s["server_model"] = r.get("model")
        if "choices" in r:
            msg = r["choices"][0]["message"]
            s["content"] = (msg.get("content") or "")[:120]
            s["tool_calls"] = [tc["function"] for tc in (msg.get("tool_calls") or [])]
            s["finish_reason"] = r["choices"][0].get("finish_reason")
        else:
            s["content"] = " ".join(
                b.get("text", "") for b in r.get("content", []) if b.get("type") == "text"
            )[:120]
            s["tool_calls"] = [
                {"name": b["name"], "input": b["input"]}
                for b in r.get("content", [])
                if b.get("type") == "tool_use"
            ]
            s["finish_reason"] = r.get("stop_reason")
        if r.get("warning"):
            s["warning"] = r["warning"]
    if check == "schema" and s.get("content"):
        try:
            s["schema_valid"] = set(
                json.loads(
                    rec["response"]["choices"][0]["message"]["content"]
                    if "choices" in rec["response"]
                    else s["content"]
                )
            ) == {"answer", "confidence"}
        except (ValueError, KeyError, TypeError):
            s["schema_valid"] = False
    return s


def scan_for_secrets(keys: list[str]) -> list[Path]:
    secrets = [os.environ[k] for k in keys if os.environ.get(k)]
    return [p for p in OUT.rglob("*.json") if any(sec in p.read_text() for sec in secrets)]


def main() -> int:
    registry = load_registry(ROOT / "configs" / "models.yaml")
    only = set(sys.argv[1:])
    summaries = []
    for key, model in registry.enabled_models().items():
        if only and key not in only:
            continue
        provider = registry.providers[model.provider]
        if not os.environ.get(provider.api_key_env):
            print(f"skip {key}: {provider.api_key_env} not set")
            continue
        runner = AnthropicNative if provider.kind is ProviderKind.ANTHROPIC else OpenAICompat
        for check, fn in runner(key, model, provider).checks().items():
            try:
                rec = fn()
            except Exception as e:  # record failures as findings, keep going
                rec = {"error": f"{type(e).__name__}: {e}"}
            path = OUT / key / f"{check}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rec, indent=2, default=str))
            s = summarize(key, check, rec)
            summaries.append(s)
            print(json.dumps(s, default=str))
    summary_path = OUT / "summary.json"
    if only and summary_path.exists():  # filtered re-run: replace only those models' rows
        rerun = {s["model"] for s in summaries}
        kept = [s for s in json.loads(summary_path.read_text()) if s["model"] not in rerun]
        summaries = kept + summaries
    summary_path.write_text(json.dumps(summaries, indent=2, default=str))

    leaked = scan_for_secrets([p.api_key_env for p in registry.providers.values()])
    if leaked:
        print(f"SECRET FOUND in recordings, deleting: {leaked}", file=sys.stderr)
        for p in leaked:
            p.unlink()
        return 2
    print(f"\nno secrets in {len(list(OUT.rglob('*.json')))} recordings")
    return 0 if all(s["ok"] for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
