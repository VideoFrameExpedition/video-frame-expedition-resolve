"""uvicorn's server for ``vfe serve``, which takes one Ctrl+C as one Ctrl+C."""

from __future__ import annotations

import signal
import socket
import time
from collections.abc import Callable
from types import FrameType

import uvicorn

ECHO_S = 1.0  # a second SIGINT this soon after the first is the same Ctrl+C
# ``vfe serve``'s exit code when the interface asks for a restart (EX_TEMPFAIL): run.bat and
# run.command start it again in the same window.
RESTART_EXIT = 75


class Server(uvicorn.Server):
    """The same Ctrl+C can reach the server twice: the Terminal sends it to the whole foreground
    process group, and the launcher (``uv run``) may relay it too. uvicorn takes a second SIGINT
    as "force quit": it skips the application's shutdown (worker, open event streams), which
    then ends in tracebacks. The echo is ignored; Ctrl+C pressed again later still forces it."""

    def __init__(
        self, config: uvicorn.Config, *, on_stop: Callable[[], None] | None = None
    ) -> None:
        super().__init__(config)
        self._first_sigint: float | None = None
        self._on_stop = on_stop
        self.restart_requested = False

    def restart(self) -> None:
        """Stop as for Ctrl+C (the worker too, its analyses back in the queue); ``vfe serve``
        then exits with ``RESTART_EXIT``."""
        self.restart_requested = True
        self.should_exit = True

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """``on_stop`` first: what would never end by itself (an assistant's open MCP stream)
        ends now, instead of being waited for, then cancelled, by uvicorn."""
        if self._on_stop is not None:
            self._on_stop()
        await super().shutdown(sockets)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if sig == signal.SIGINT:
            now = time.monotonic()
            if self._first_sigint is None:
                self._first_sigint = now
            elif now - self._first_sigint < ECHO_S:
                return
        super().handle_exit(sig, frame)
