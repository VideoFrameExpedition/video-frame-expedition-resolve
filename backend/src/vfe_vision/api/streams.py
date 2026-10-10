"""MCP requests still open when the server stops: ended at once.

An assistant keeps a request open on ``/mcp`` to hear from the server (a stream of
notifications), and it never ends by itself. uvicorn would wait for it, then cancel it after its
graceful timeout, and the MCP library writes each cancellation out as a traceback. Ended here
first, it closes like any finished response, and the client reconnects when the server is back.
"""

from __future__ import annotations

import anyio
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MCP_PATH = "/mcp"


class OpenStreams:
    """The MCP requests being served, each in its own cancel scope."""

    def __init__(self) -> None:
        self._scopes: set[anyio.CancelScope] = set()

    def __len__(self) -> int:
        return len(self._scopes)

    def add(self, scope: anyio.CancelScope) -> None:
        self._scopes.add(scope)

    def discard(self, scope: anyio.CancelScope) -> None:
        self._scopes.discard(scope)

    def close(self) -> None:
        """End them all now: the server is stopping."""
        for scope in list(self._scopes):
            scope.cancel()


class OpenStreamsMiddleware:
    """Serves each MCP request in a cancel scope that ``OpenStreams.close`` ends; a response cut
    short this way is completed (or, not started yet, answered 503: the server is going away)."""

    def __init__(self, app: ASGIApp, streams: OpenStreams) -> None:
        self.app = app
        self.streams = streams

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(MCP_PATH):
            await self.app(scope, receive, send)
            return
        started = ended = False

        async def watched(message: Message) -> None:
            nonlocal started, ended
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                ended = True
            await send(message)

        with anyio.CancelScope() as cancel:
            self.streams.add(cancel)
            try:
                await self.app(scope, receive, watched)
            finally:
                self.streams.discard(cancel)
        if not cancel.cancelled_caught or ended:
            return
        if started:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await Response(status_code=503)(scope, receive, send)
