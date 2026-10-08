"""uvicorn's server for ``vfe serve``, which takes one Ctrl+C as one Ctrl+C."""

from __future__ import annotations

import signal
import time
from types import FrameType

import uvicorn

ECHO_S = 1.0  # a second SIGINT this soon after the first is the same Ctrl+C


class Server(uvicorn.Server):
    """The same Ctrl+C can reach the server twice: the Terminal sends it to the whole foreground
    process group, and the launcher (``uv run``) may relay it too. uvicorn takes a second SIGINT
    as "force quit": it skips the application's shutdown (worker, open event streams), which
    then ends in tracebacks. The echo is ignored; Ctrl+C pressed again later still forces it."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self._first_sigint: float | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if sig == signal.SIGINT:
            now = time.monotonic()
            if self._first_sigint is None:
                self._first_sigint = now
            elif now - self._first_sigint < ECHO_S:
                return
        super().handle_exit(sig, frame)
