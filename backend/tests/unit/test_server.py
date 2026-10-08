"""``vfe serve``'s server: one Ctrl+C, even delivered twice, is one Ctrl+C."""

from __future__ import annotations

import signal
import time
from typing import Any

import pytest
import uvicorn
from sse_starlette.sse import AppStatus

from vfe_vision.api.server import ECHO_S, Server


async def _app(scope: Any, receive: Any, send: Any) -> None:
    """Never called: only the signal handling is tried."""


def test_the_echo_of_a_ctrl_c_does_not_force_the_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(AppStatus, "should_exit", False)  # sse-starlette's handler sets it
    server = Server(uvicorn.Config(app=_app))

    server.handle_exit(signal.SIGINT, None)
    now[0] += ECHO_S / 10  # the same Ctrl+C, relayed by the launcher
    server.handle_exit(signal.SIGINT, None)
    assert server.should_exit
    assert not server.force_exit  # the application's own shutdown still runs

    now[0] += ECHO_S  # Ctrl+C pressed again: uvicorn's "force quit", as before
    server.handle_exit(signal.SIGINT, None)
    assert server.force_exit
