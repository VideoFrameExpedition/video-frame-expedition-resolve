"""Minimum interval between calls to an online service, shared by the threads of a process."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class MinInterval:
    """Blocks until ``interval_s`` has elapsed since the previous call (Nominatim: 1 req/s)."""

    def __init__(
        self,
        interval_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval_s = interval_s
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._clock()
            self._next = now + self.interval_s
