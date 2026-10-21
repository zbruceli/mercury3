"""Shared provider machinery: error classes, retry policy, rate limiting (F1.1, F1.6, F1.7)."""

from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from dllm_eval.config import Model, Provider, RateLimits
from dllm_eval.schemas import ChatRequest, ChatResult
from dllm_eval.timing import Clock

Sleep = Callable[[float], Awaitable[None]]

RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
RUN_FATAL_STATUS = frozenset({401, 402, 403, 404})
# Error types/codes that look retryable by status but are really billing problems
# (M0_SMOKE_RESULTS F1: OpenAI sends an empty balance as 429 insufficient_quota).
BILLING_ERROR_MARKERS = frozenset({"insufficient_quota", "credit_balance_exhausted"})


class ErrorKind(StrEnum):
    RETRY = "retry"  # transient: back off and try again
    CALL_FATAL = "call_fatal"  # this request is bad (e.g. 400): record it and move on
    RUN_FATAL = "run_fatal"  # auth/billing/model access: stop the whole run


class ProviderError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        status: int | None = None,
        error_type: str | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.error_type = error_type
        self.attempts = attempts

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "status": self.status,
            "error_type": self.error_type,
            "attempts": self.attempts,
            "message": str(self)[:500],
        }


def classify(status: int | None, error_type: str | None) -> ErrorKind:
    """Map an HTTP status (None = connection/timeout problem) to what the runner should do."""
    if error_type in BILLING_ERROR_MARKERS:
        return ErrorKind.RUN_FATAL
    if status is None or status in RETRYABLE_STATUS:
        return ErrorKind.RETRY
    if status in RUN_FATAL_STATUS:
        return ErrorKind.RUN_FATAL
    return ErrorKind.CALL_FATAL


def error_details(exc: BaseException) -> tuple[int | None, str | None]:
    """(status, error type/code) from an openai or anthropic SDK exception."""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error_type = None
    if isinstance(body, dict):
        nested = body.get("error")
        inner: dict[str, Any] = nested if isinstance(nested, dict) else body
        for field in ("code", "type"):
            value = inner.get(field)
            if isinstance(value, str) and value in BILLING_ERROR_MARKERS:
                return status, value
        error_type = inner.get("type") if isinstance(inner.get("type"), str) else None
    return status, error_type


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Full-jitter exponential backoff for the given retry number (1-based)."""
        cap = min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1))
        return rng.uniform(0, cap)


class RateLimiter:
    """Sliding 60 s window over requests and server-reported tokens, shared per provider.

    Token limits can only be enforced after the fact (we learn a call's tokens when it
    finishes), so a burst can briefly overshoot; the window then blocks until it drains.
    """

    WINDOW_S = 60.0

    def __init__(
        self,
        limits: RateLimits | None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.limits = limits
        self._clock = clock
        self._sleep = sleep
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int, int]] = deque()  # (t, input, output)
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        while self._requests and now - self._requests[0] >= self.WINDOW_S:
            self._requests.popleft()
        while self._tokens and now - self._tokens[0][0] >= self.WINDOW_S:
            self._tokens.popleft()

    def _wait_needed(self, now: float) -> float:
        lim = self.limits
        if lim is None:
            return 0.0
        waits = []
        if len(self._requests) >= lim.requests_per_min:
            waits.append(self._requests[0] + self.WINDOW_S - now)
        inp = sum(t[1] for t in self._tokens)
        out = sum(t[2] for t in self._tokens)
        over = (
            (lim.input_tokens_per_min is not None and inp >= lim.input_tokens_per_min)
            or (lim.output_tokens_per_min is not None and out >= lim.output_tokens_per_min)
            or (lim.total_tokens_per_min is not None and inp + out >= lim.total_tokens_per_min)
        )
        if over and self._tokens:
            waits.append(self._tokens[0][0] + self.WINDOW_S - now)
        return max(waits, default=0.0)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self._prune(now)
                wait = self._wait_needed(now)
                if wait <= 0:
                    self._requests.append(now)
                    return
                await self._sleep(max(wait, 0.05))

    def record(self, input_tokens: int | None, output_tokens: int | None) -> None:
        self._tokens.append((self._clock(), input_tokens or 0, output_tokens or 0))


class BaseProvider(ABC):
    """Validates, rate-limits, and retries; subclasses implement one raw call."""

    def __init__(
        self,
        model_key: str,
        model: Model,
        provider_key: str,
        provider: Provider,
        *,
        limiter: RateLimiter | None = None,
        retry: RetryPolicy | None = None,
        clock: Clock = time.perf_counter,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        record_raw: bool = False,
    ) -> None:
        self.model_key = model_key
        self.model = model
        self.provider_key = provider_key
        self.provider = provider
        self.limiter = limiter or RateLimiter(provider.rate_limits)
        self.retry = retry or RetryPolicy()
        self.clock = clock
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.record_raw = record_raw

    def validate(self, req: ChatRequest) -> None:
        """Refuse settings the model doesn't support instead of letting the API silently
        reset them (Mercury resets out-of-range temperatures; PRD R2)."""
        if req.model_key != self.model_key:
            raise ValueError(f"request for {req.model_key!r} sent to {self.model_key!r}")
        m = self.model
        if req.reasoning_effort is not None and req.reasoning_effort not in m.reasoning_efforts:
            raise ValueError(
                f"{self.model_key}: unsupported reasoning_effort {req.reasoning_effort!r}"
            )
        if req.temperature is not None and m.temperature_range is not None:
            lo, hi = m.temperature_range
            if not lo <= req.temperature <= hi:
                raise ValueError(
                    f"{self.model_key}: temperature {req.temperature} not in [{lo}, {hi}]"
                )
        if m.max_output_tokens is not None and req.max_completion_tokens > m.max_output_tokens:
            raise ValueError(f"{self.model_key}: max_completion_tokens above {m.max_output_tokens}")
        if req.tools and not m.supports.tools:
            raise ValueError(f"{self.model_key}: tools not supported")
        if req.response_format and not m.supports.structured_outputs:
            raise ValueError(f"{self.model_key}: structured outputs not supported")
        allowed = m.supports.tools_reasoning_efforts
        if req.tools and allowed is not None and req.reasoning_effort not in allowed:
            raise ValueError(
                f"{self.model_key}: tools need reasoning_effort in {allowed}, "
                f"not {req.reasoning_effort!r}"
            )
        if req.tools and req.response_format and not m.supports.tools_with_structured_outputs:
            raise ValueError(f"{self.model_key}: tools with response_format not supported")

    def to_provider_error(self, exc: BaseException) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        status, error_type = error_details(exc)
        return ProviderError(
            classify(status, error_type),
            f"{type(exc).__name__}: {exc}",
            status=status,
            error_type=error_type,
        )

    async def chat(self, req: ChatRequest) -> ChatResult:
        self.validate(req)
        retries = 0
        while True:
            await self.limiter.acquire()
            try:
                result = await self._chat_once(req)
            except Exception as exc:
                err = self.to_provider_error(exc)
                err.attempts = retries + 1
                if err.kind is not ErrorKind.RETRY:
                    raise err from exc
                if retries + 1 >= self.retry.max_attempts:
                    err.kind = ErrorKind.CALL_FATAL
                    raise err from exc
                retries += 1
                await self.sleep(self.retry.delay(retries, self.rng))
                continue
            self.limiter.record(result.usage.input_tokens, result.usage.output_tokens)
            return result.model_copy(update={"retries": retries})

    @abstractmethod
    async def _chat_once(self, req: ChatRequest) -> ChatResult:
        """One raw API call. Raise SDK exceptions as-is; chat() classifies them."""
