"""``vfe mcp-stdio``: what Claude Desktop launches, against the real HTTP endpoint."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest
import uvicorn
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from fastapi import FastAPI
from mcp import Client, StdioServerParameters
from mcp.shared.message import SessionMessage
from mcp_types import jsonrpc_message_adapter

from vfe_vision.api.access import AccessConfig
from vfe_vision.api.app import create_app
from vfe_vision.core.config import Settings
from vfe_vision.mcp.stdio_bridge import Bridge

TOKEN = "jeton-du-pont-0123456789"  # noqa: S105 - a test token
IP = "100.101.102.103"
INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "claude-desktop-like", "version": "1"},
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
def live_url(settings: Settings) -> Iterator[str]:
    """The whole application on a real loopback port (no worker)."""
    port = _free_port()
    app = create_app(settings.model_copy(update={"port": port}), start_worker=False)
    runner = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not runner.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner.started
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        runner.should_exit = True
        thread.join(timeout=10)


def _bridge(url: str, *extra: str) -> StdioServerParameters:
    # As a client launches it: absolute interpreter, the client's reduced environment.
    return StdioServerParameters(
        command=sys.executable, args=["-m", "vfe_vision", "mcp-stdio", "--url", url, *extra]
    )


@pytest.mark.anyio
async def test_a_stdio_client_uses_the_app_through_the_bridge(live_url: str) -> None:
    async with Client(_bridge(live_url)) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        listed = await client.call_tool("list_watched", {})
        missing = await client.call_tool("get_job", {"job_id": "absent"})
    assert {"watch_video", "search_memory", "get_frames"} <= tools
    assert not listed.is_error
    assert listed.structured_content == {"result": []}
    assert missing.is_error
    assert "introuvable" in missing.content[0].text  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_a_clear_error_when_the_app_is_not_running() -> None:
    url = f"http://127.0.0.1:{_free_port()}/mcp"  # nothing listens there
    with pytest.raises(Exception) as caught:  # noqa: PT011 - the text is what matters
        async with Client(_bridge(url)) as client:
            await client.list_tools()
    text = " | ".join(_messages(caught.value))
    assert f"Video Frame Expedition ne répond pas à {url}" in text
    assert "run.command" in text


def _messages(error: BaseException) -> list[str]:
    if isinstance(error, BaseExceptionGroup):
        return [text for inner in error.exceptions for text in _messages(inner)]
    return [str(error)]


def test_stdout_carries_protocol_messages_only(live_url: str) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "vfe_vision", "mcp-stdio", "--url", live_url],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    assert process.stdin is not None
    assert process.stdout is not None
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT_PARAMS},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ]
    try:
        for message in messages:
            process.stdin.write(json.dumps(message).encode() + b"\n")
        process.stdin.flush()
        process.stdin.close()  # the client leaves: pending answers are still written
        lines = process.stdout.read().decode("utf-8").splitlines()
        assert process.wait(timeout=30) == 0
    finally:
        process.kill()
    answers = [json.loads(line) for line in lines]
    assert all(answer["jsonrpc"] == "2.0" for answer in answers)
    assert sorted(answer["id"] for answer in answers) == [1, 2, 3]
    by_id = {answer["id"]: answer for answer in answers}
    assert by_id[1]["result"]["serverInfo"]["name"] == "vfe-vision"
    assert by_id[2]["result"]["tools"]


# ---------------------------------------------------------------- in-process (remote client)
class Stdio:
    """The bridge's stdin and stdout, in memory."""

    def __init__(self) -> None:
        self.stdin: MemoryObjectSendStream[SessionMessage | Exception]
        self.incoming: MemoryObjectReceiveStream[SessionMessage | Exception]
        self.stdin, self.incoming = anyio.create_memory_object_stream(16)
        self.outgoing: MemoryObjectSendStream[SessionMessage]
        self.stdout: MemoryObjectReceiveStream[SessionMessage]
        self.outgoing, self.stdout = anyio.create_memory_object_stream(16)

    async def ask(self, message: dict[str, Any]) -> dict[str, Any]:
        await self.stdin.send(SessionMessage(jsonrpc_message_adapter.validate_python(message)))
        if "id" not in message:
            return {}
        with anyio.fail_after(20):
            answer = await self.stdout.receive()
        dumped: dict[str, Any] = answer.message.model_dump(by_alias=True, exclude_unset=True)
        return dumped

    async def handshake(self) -> dict[str, Any]:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT_PARAMS}
        answer = await self.ask(init)
        await self.ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return answer


class Switch(httpx.AsyncBaseTransport):
    """An ASGI transport whose app can be replaced (the app restarting)."""

    def __init__(self, app: FastAPI, client: tuple[str, int]) -> None:
        self.client = client
        self.target = httpx.ASGITransport(app=app, client=client)

    def restart(self, app: FastAPI) -> None:
        self.target = httpx.ASGITransport(app=app, client=self.client)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.target.handle_async_request(request)


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[FastAPI]:
    async with app.router.lifespan_context(app):
        yield app


def _remote_app(settings: Settings) -> FastAPI:
    access = AccessConfig(port=8765, token=TOKEN, remote_hosts=(IP,), remote_requested=True)
    return create_app(settings, start_worker=False, access=access)


@asynccontextmanager
async def bridged(transport: httpx.AsyncBaseTransport, token: str | None) -> AsyncIterator[Stdio]:
    stdio = Stdio()
    async with (
        httpx.AsyncClient(transport=transport) as http,
        anyio.create_task_group() as group,
    ):
        bridge = Bridge(f"http://{IP}:8765/mcp", http, stdio.outgoing.send, token)
        group.start_soon(bridge.run, stdio.incoming)
        try:
            yield stdio
        finally:
            await stdio.stdin.aclose()  # stdin closed: the bridge ends its session and stops


@pytest.mark.anyio
async def test_the_token_reaches_the_app_from_another_device(settings: Settings) -> None:
    async with running(_remote_app(settings)) as app:
        transport = Switch(app, ("100.64.0.9", 50000))
        async with bridged(transport, TOKEN) as stdio:
            init = await stdio.handshake()
            assert init["result"]["serverInfo"]["name"] == "vfe-vision"
            tools = await stdio.ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert tools["result"]["tools"]
        async with bridged(transport, None) as stdio:
            refused = await stdio.handshake()
            assert "demande le jeton" in refused["error"]["message"]
        async with bridged(transport, "faux") as stdio:
            wrong = await stdio.handshake()
            assert "Jeton refusé" in wrong["error"]["message"]


@pytest.mark.anyio
async def test_the_session_is_reopened_after_the_app_restarts(settings: Settings) -> None:
    async with running(_remote_app(settings)) as first, running(_remote_app(settings)) as second:
        transport = Switch(first, ("100.64.0.9", 50000))
        async with bridged(transport, TOKEN) as stdio:
            await stdio.handshake()
            transport.restart(second)  # the old session id means nothing there
            tools = await stdio.ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert tools["result"]["tools"], tools
            ping = await stdio.ask({"jsonrpc": "2.0", "id": 3, "method": "ping"})
            assert ping == {"jsonrpc": "2.0", "id": 3, "result": {}}
