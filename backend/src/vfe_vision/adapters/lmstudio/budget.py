"""Weighted token budget for concurrent LM Studio requests.

LM Studio shares one KV cache across its parallel slots: the *sum* of in-flight prompt and
completion tokens must fit the loaded context, otherwise requests fail with
``failed to process mtmd chunk``. Each request therefore reserves its estimated token count,
on top of a limit on the number of simultaneous requests.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

IMAGE_PATCH_PX = 32
IMAGE_OVERHEAD_TOKENS = 60
UNKNOWN_CONTEXT = 8192  # an instance that does not say its context (as in the stages)


def slot_tokens(context_length: int | None, parallel: int | None) -> int:
    """One slot's share of the loaded context (context ÷ parallel requests): what a request can
    hold while every other slot is busy, since the slots share one KV cache."""
    return (context_length or UNKNOWN_CONTEXT) // max(1, parallel or 1)


def estimate_image_tokens(width: int, height: int) -> int:
    return math.ceil(width / IMAGE_PATCH_PX) * math.ceil(height / IMAGE_PATCH_PX) + (
        IMAGE_OVERHEAD_TOKENS
    )


def estimate_text_tokens(text: str) -> int:
    # ~3 characters per token for French/English prose with some margin.
    return math.ceil(len(text) / 3) + 8


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


class UsageCalibration:
    """Learns the real cost of a request type from the usage the server reports.

    The static estimates are deliberately pessimistic (3 characters per token, the full
    ``max_tokens`` for the answer). Reserving that much let only two of the four slots of the
    reference setup work at once. After a few answers, a reservation becomes: the estimated
    prompt × the observed prompt ratio (90th percentile, +5 %) and the 95th percentile of the
    answer lengths +25 %, never above ``max_tokens``. An answer longer than its reservation is
    still allowed by the server; the margins keep the shared KV cache within the loaded context.
    """

    MIN_PROMPT_SAMPLES = 4
    MIN_COMPLETION_SAMPLES = 8
    MIN_COMPLETION_RESERVE = 256

    def __init__(self, window: int = 64) -> None:
        self._prompt_ratios: deque[float] = deque(maxlen=window)
        self._completions: deque[float] = deque(maxlen=window)

    def observe(
        self, estimated_prompt: int, prompt_tokens: int | None, completion_tokens: int | None
    ) -> None:
        if prompt_tokens and estimated_prompt > 0:
            self._prompt_ratios.append(prompt_tokens / estimated_prompt)
        if completion_tokens:
            self._completions.append(float(completion_tokens))

    def prompt(self, estimated: int) -> int:
        if len(self._prompt_ratios) < self.MIN_PROMPT_SAMPLES:
            return estimated
        ratio = min(1.5, max(0.5, _quantile(list(self._prompt_ratios), 0.9) * 1.05))
        return math.ceil(estimated * ratio)

    def completion(self, max_tokens: int) -> int:
        if len(self._completions) < self.MIN_COMPLETION_SAMPLES:
            return max_tokens
        typical = math.ceil(_quantile(list(self._completions), 0.95) * 1.25)
        return min(max_tokens, max(self.MIN_COMPLETION_RESERVE, typical))


class TokenBudget:
    def __init__(self, capacity: int, max_concurrency: int) -> None:
        if capacity <= 0 or max_concurrency <= 0:
            raise ValueError("capacité et concurrence doivent être positives")
        self._capacity = capacity
        self._max_concurrency = max_concurrency
        self._used = 0
        self._active = 0
        self._condition = asyncio.Condition()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @classmethod
    def for_context(
        cls, context_length: int, parallel: int, *, fill_ratio: float = 0.9
    ) -> TokenBudget:
        return cls(max(1024, int(context_length * fill_ratio)), max(1, parallel))

    async def resize(self, capacity: int, max_concurrency: int) -> None:
        async with self._condition:
            self._capacity = max(1, capacity)
            self._max_concurrency = max(1, max_concurrency)
            self._condition.notify_all()

    @asynccontextmanager
    async def reserve(self, tokens: int) -> AsyncIterator[None]:
        """Wait until ``tokens`` fit (a request larger than the budget runs alone)."""
        async with self._condition:
            while True:
                needed = min(tokens, self._capacity)
                fits = self._used + needed <= self._capacity or self._active == 0
                if fits and self._active < self._max_concurrency:
                    break
                await self._condition.wait()
            self._used += needed
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._used -= needed
                self._active -= 1
                self._condition.notify_all()
