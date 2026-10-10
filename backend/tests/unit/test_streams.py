"""MCP requests still open when the server stops are ended at once, their response completed."""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from starlette.types import Message, Scope

from vfe_vision.api.streams import OpenStreams, OpenStreamsMiddleware

pytestmark = pytest.mark.anyio


def _request(path: str) -> Scope:
    return {"type": "http", "path": path, "method": "POST", "headers": []}


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _stopped_while_served(answers: bool) -> tuple[list[Message], OpenStreams]:
    """An MCP request still being served (its response started or not) when the server stops:
    what it sent, and the streams left open."""
    streams = OpenStreams()
    inside = anyio.Event()
    sent: list[Message] = []

    async def listening(scope: Any, receive: Any, send: Any) -> None:
        if answers:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b": ping\r\n\r\n", "more_body": True})
        inside.set()
        await anyio.sleep_forever()  # an assistant listening to the server

    async def send(message: Message) -> None:
        sent.append(message)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                OpenStreamsMiddleware(listening, streams), _request("/mcp"), _receive, send
            )
            await inside.wait()
            streams.close()
    return sent, streams


async def test_an_open_stream_ends_when_the_server_stops() -> None:
    sent, streams = await _stopped_while_served(answers=True)
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
    assert len(streams) == 0


async def test_a_request_not_answered_yet_gets_a_503() -> None:
    sent, _ = await _stopped_while_served(answers=False)
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 503


async def test_other_requests_are_left_alone() -> None:
    async def page(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    streams = OpenStreams()
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await OpenStreamsMiddleware(page, streams)(_request("/api/v1/system/health"), _receive, send)
    assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]
    assert len(streams) == 0
