"""Monotonic timing around a streamed call (F2.1). The clock is injectable for tests."""

from __future__ import annotations

import time
from collections.abc import Callable

from dllm_eval.schemas import Timing

Clock = Callable[[], float]


class Stopwatch:
    """Records the timing marks for one request. All marks are relative to start()."""

    def __init__(self, clock: Clock = time.perf_counter) -> None:
        self._clock = clock
        self._t0: float | None = None
        self.first_byte: float | None = None
        self.first_content: float | None = None
        self.last_content: float | None = None

    def start(self) -> None:
        self._t0 = self._clock()

    def now(self) -> float:
        if self._t0 is None:
            raise RuntimeError("Stopwatch.start() was not called")
        return self._clock() - self._t0

    def mark_first_byte(self) -> None:
        if self.first_byte is None:
            self.first_byte = self.now()

    def mark_content(self, t: float | None = None) -> float:
        """Call for every chunk that carries non-empty content (t defaults to now)."""
        t = self.now() if t is None else t
        if self.first_content is None:
            self.first_content = t
        self.last_content = t
        return t

    def finish(self) -> Timing:
        return Timing(
            t_first_byte=self.first_byte,
            t_first_content=self.first_content,
            t_last_content=self.last_content,
            t_done=self.now(),
        )
