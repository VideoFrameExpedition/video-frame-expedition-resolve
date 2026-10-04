"""Cooperative cancellation shared between the scheduler and long-running work."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio

from vfe_vision.core.errors import CancelledError

CANCEL_POLL_S = 0.25  # the token is a threading.Event set by another thread: polled


class CancelToken:
    """Thread-safe cancellation flag checked by stages between units of work."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = ""

    def cancel(self, reason: str = "Annulé par l'utilisateur") -> None:
        self.reason = reason
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError(self.reason or "Annulé")

    def wait(self, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds; return True as soon as cancellation is requested."""
        return self._event.wait(timeout)


@asynccontextmanager
async def stopped_by(token: CancelToken | None) -> AsyncIterator[None]:
    """Abandon the awaits inside as soon as ``token`` is cancelled, then raise CancelledError
    (requests in flight are dropped, not waited for)."""
    if token is None:
        yield
        return
    error: Exception | None = None
    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as group:

            async def watch() -> None:
                while not token.cancelled:  # noqa: ASYNC110
                    await anyio.sleep(CANCEL_POLL_S)
                scope.cancel()

            group.start_soon(watch)
            try:
                yield
            except Exception as exc:  # noqa: BLE001 - re-raised below, not as an ExceptionGroup
                error = exc
            group.cancel_scope.cancel()
    if error is not None:
        raise error
    token.raise_if_cancelled()
