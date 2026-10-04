"""stdio ↔ streamable HTTP bridge (``vfe mcp-stdio``).

Claude Desktop's configuration file only launches local commands speaking MCP on stdin/stdout.
This bridge relays their messages to the app's ``/mcp`` endpoint, as they are:

* each JSON-RPC message read on stdin is POSTed; every message of the answer (JSON, or
  Server-Sent Events: progress notifications, then the response) is written to stdout, one per
  line — stdout carries nothing else (logs go to stderr);
* the session id and the negotiated protocol version are sent back on each request (the
  per-request headers of the 2026-07-28 protocol are derived from the message itself);
* when the app has restarted (unknown session), the client's ``initialize`` is replayed once;
* a request the app cannot answer (not running, token refused…) gets an explicit JSON-RPC
  error: the bridge never starts the app.

Messages the server would send outside any request (the optional GET stream) are not relayed:
vfe-vision sends none.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx
from mcp.server.stdio import stdio_server
from mcp.shared.inbound import (
    MCP_METHOD_HEADER,
    MCP_NAME_HEADER,
    MCP_PROTOCOL_VERSION_HEADER,
    NAME_BEARING_METHODS,
    encode_header_value,
)
from mcp.shared.message import SessionMessage
from mcp_types import (
    CONNECTION_CLOSED,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    PARSE_ERROR,
    PROTOCOL_VERSION_META_KEY,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    RequestId,
    jsonrpc_message_adapter,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from vfe_vision import __version__

log = logging.getLogger(__name__)
APP = "Video Frame Expedition"  # how the messages below name the application
SESSION_HEADER = "mcp-session-id"
TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=None)  # tools may run long
Send = Callable[[SessionMessage], Awaitable[None]]


def app_down(url: str) -> str:
    return (
        f"{APP} ne répond pas à {url} : lancez l'application (run.bat) sur l'ordinateur "
        "qui l'héberge, puis réessayez."
    )


def _request_id(message: JSONRPCMessage) -> RequestId | None:
    return message.id if isinstance(message, JSONRPCRequest) else None


def _is_initialize(message: JSONRPCMessage) -> bool:
    return isinstance(message, JSONRPCRequest) and message.method == "initialize"


def _envelope_version(message: JSONRPCMessage) -> str | None:
    """The protocol version a 2026-07-28+ message carries in ``params._meta`` (none before)."""
    if not isinstance(message, JSONRPCRequest | JSONRPCNotification):
        return None
    meta = (message.params or {}).get("_meta")
    version = meta.get(PROTOCOL_VERSION_META_KEY) if isinstance(meta, dict) else None
    return version if isinstance(version, str) else None


def _dump(message: JSONRPCMessage) -> bytes:
    return message.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8")


async def _payloads(response: httpx.Response) -> AsyncIterator[str]:
    """The JSON-RPC payloads of an answer: its JSON body, or the data of each SSE message."""
    kind = response.headers.get("content-type", "").lower()
    if kind.startswith("application/json"):
        yield (await response.aread()).decode("utf-8")
        return
    if not kind.startswith("text/event-stream"):
        return
    event, data = "message", list[str]()
    async for line in response.aiter_lines():
        if not line:
            if data and event == "message":
                yield "\n".join(data)
            event, data = "message", []
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            event = value or "message"
        elif name == "data":
            data.append(value)
    if data and event == "message":
        yield "\n".join(data)


def _messages(payload: str) -> list[JSONRPCMessage]:
    parsed: Any = json.loads(payload)
    items = parsed if isinstance(parsed, list) else [parsed]
    return [jsonrpc_message_adapter.validate_python(item, by_name=False) for item in items]


@dataclass
class Bridge:
    """Relays the messages of one stdio client to the app over streamable HTTP."""

    url: str
    http: httpx.AsyncClient
    send: Send
    token: str | None = None
    session_id: str | None = None
    protocol_version: str | None = None
    _initialize: JSONRPCRequest | None = None
    _reopen: anyio.Lock = field(default_factory=anyio.Lock)
    _in_flight: dict[RequestId, anyio.CancelScope] = field(default_factory=dict)
    _cancelled: set[RequestId] = field(default_factory=set)

    async def run(self, incoming: AsyncIterable[SessionMessage | Exception]) -> None:
        """Until stdin closes and the requests still running are answered. Requests run side by
        side (a long tool call does not hold back a ping); ``initialize``, notifications and
        responses go in the order they came."""
        async with anyio.create_task_group() as group:
            async for item in incoming:
                if isinstance(item, Exception):
                    log.warning("unreadable message on stdin: %s", item)
                    await self._error(None, PARSE_ERROR, "Message JSON-RPC illisible.")
                    continue
                message = item.message
                if isinstance(message, JSONRPCRequest) and not _is_initialize(message):
                    scope = anyio.CancelScope()
                    self._in_flight[message.id] = scope
                    group.start_soon(self._request, message, scope)
                elif not self._abort_in_flight(message):
                    await self._forward(message)
        await self._close_session()

    async def _request(self, message: JSONRPCRequest, scope: anyio.CancelScope) -> None:
        try:
            with scope:
                await self._forward(message)
        finally:
            if self._in_flight.get(message.id) is scope:
                del self._in_flight[message.id]
            self._cancelled.discard(message.id)

    def _abort_in_flight(self, message: JSONRPCMessage) -> bool:
        """A cancellation: noted (no error for that request); at 2026-07-28 closing the request's
        stream *is* the cancellation, so nothing is sent."""
        if not (
            isinstance(message, JSONRPCNotification) and message.method == "notifications/cancelled"
        ):
            return False
        request_id = (message.params or {}).get("requestId")
        if not isinstance(request_id, int | str):
            return False
        scope = self._in_flight.get(request_id)
        if scope is not None:
            self._cancelled.add(request_id)
        modern = (_envelope_version(message) or self.protocol_version) in MODERN_PROTOCOL_VERSIONS
        if modern and scope is not None:
            scope.cancel()
            return True
        return modern

    async def _forward(self, message: JSONRPCMessage) -> None:
        if isinstance(message, JSONRPCRequest) and message.method == "initialize":
            self._initialize = message
            self.session_id = self.protocol_version = None
        for attempt in (1, 2):
            try:
                expired = await self._post(message)
            except httpx.TransportError as exc:
                await self._unreachable(message, exc)
                return
            if expired is None:
                return
            if attempt == 2 or not await self._reopen_session(expired):
                text = f"Session MCP perdue ({APP} a redémarré) : relancez le client MCP."
                request_id = _request_id(message)
                if request_id is None:  # a notification gets no answer, even an error
                    log.warning("%s (%s)", text, message)
                else:
                    await self._error(request_id, INVALID_REQUEST, text)
                return

    def _headers(self, message: JSONRPCMessage, *, session: str | None) -> dict[str, str]:
        headers = {"accept": "application/json, text/event-stream"}
        headers["content-type"] = "application/json"
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if session:
            headers[SESSION_HEADER] = session
        envelope = _envelope_version(message)
        if envelope is not None and isinstance(message, JSONRPCRequest | JSONRPCNotification):
            headers[MCP_PROTOCOL_VERSION_HEADER] = envelope
            headers[MCP_METHOD_HEADER] = message.method
            name = (message.params or {}).get(NAME_BEARING_METHODS.get(message.method, ""))
            if isinstance(name, str):
                headers[MCP_NAME_HEADER] = encode_header_value(name)
        elif self.protocol_version and not _is_initialize(message):
            headers[MCP_PROTOCOL_VERSION_HEADER] = self.protocol_version
        return headers

    async def _post(self, message: JSONRPCMessage) -> str | None:
        """POST one message and relay its answer; returns the session id the app no longer
        knows (restarted), without having relayed anything, else None."""
        request_id = _request_id(message)
        session = None if _is_initialize(message) else self.session_id
        headers = self._headers(message, session=session)
        async with self.http.stream("POST", self.url, content=_dump(message), headers=headers) as r:
            if r.status_code == 404 and session is not None:
                return session
            if request_id is None:  # notification or response: nothing comes back
                if r.status_code >= 400:
                    log.warning("message refused by the app (%s): %s", r.status_code, message)
                return None
            if r.status_code >= 400:
                await self._http_error(request_id, r)
                return None
            if r.status_code == 202:
                await self._error(request_id, INVALID_REQUEST, f"{APP} n'a pas répondu.")
                return None
            if _is_initialize(message):
                self.session_id = r.headers.get(SESSION_HEADER) or None
            async for payload in _payloads(r):
                if await self._relay(payload, request_id):
                    return None
            await self._error(
                request_id, CONNECTION_CLOSED, f"{APP} a coupé la réponse avant la fin."
            )
        return None

    async def _relay(self, payload: str, request_id: RequestId) -> bool:
        """Write the messages of one payload to stdout; True once the request is answered."""
        try:
            messages = _messages(payload)
        except ValueError as exc:
            log.warning("unreadable answer from the app: %s", exc)
            await self._error(request_id, PARSE_ERROR, f"Réponse illisible de {APP}.")
            return True
        answered = False
        for message in messages:
            if isinstance(message, JSONRPCResponse | JSONRPCError) and message.id == request_id:
                answered = True
                initialize = self._initialize
                if (
                    isinstance(message, JSONRPCResponse)
                    and initialize is not None
                    and initialize.id == request_id
                ):
                    version = message.result.get("protocolVersion")
                    self.protocol_version = version if isinstance(version, str) else None
            await self.send(SessionMessage(message))
        return answered

    async def _http_error(self, request_id: RequestId, response: httpx.Response) -> None:
        body = await response.aread()
        detail = ""
        try:
            parsed: Any = json.loads(body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            if "error" in parsed and "jsonrpc" in parsed:  # the app's own JSON-RPC error
                try:
                    error = JSONRPCError.model_validate({**parsed, "id": request_id})
                except ValueError:
                    pass
                else:
                    await self.send(SessionMessage(error))
                    return
            detail = str(parsed.get("detail") or "")
        detail = detail or body.decode("utf-8", errors="replace").strip()[:200]
        status = response.status_code
        if status == 401:
            text = (
                f"Jeton refusé par {APP} (401) : vérifiez --token ou VFE_MCP_TOKEN."
                if self.token
                else f"{APP} demande le jeton d'API depuis un autre appareil : "
                "donnez-le avec --token ou VFE_MCP_TOKEN."
            )
        elif status in {400, 421} and "host" in detail.lower():
            text = f"{APP} refuse l'adresse {self.url} : utilisez celle de la page Connexions."
        else:
            text = f"{APP} a répondu {status}" + (f" : {detail}" if detail else ".")
        await self._error(request_id, INTERNAL_ERROR, text)

    async def _unreachable(self, message: JSONRPCMessage, exc: httpx.TransportError) -> None:
        request_id = _request_id(message)
        if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
            text = app_down(self.url)
        else:
            text = f"Échange interrompu avec {APP} ({type(exc).__name__}) : réessayez."
        if request_id is None:
            log.warning("%s (%s)", text, message)
            return
        await self._error(request_id, CONNECTION_CLOSED, text)

    async def _reopen_session(self, expired: str) -> bool:
        """Replay the client's ``initialize`` on the restarted app (once for all requests)."""
        async with self._reopen:
            if self.session_id != expired:  # another request did it meanwhile
                return self.session_id is not None
            if self._initialize is None:
                return False
            replay = self._initialize.model_copy(update={"id": "vfe-bridge-reinitialize"})
            self.session_id = None
            try:
                headers = self._headers(replay, session=None)
                async with self.http.stream(
                    "POST", self.url, content=_dump(replay), headers=headers
                ) as r:
                    if r.status_code >= 400:
                        return False
                    session = r.headers.get(SESSION_HEADER) or None
                    async for payload in _payloads(r):
                        for message in _messages(payload):
                            if isinstance(message, JSONRPCResponse):
                                version = message.result.get("protocolVersion")
                                self.protocol_version = (
                                    version if isinstance(version, str) else None
                                )
                self.session_id = session
                done = JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized")
                await self.http.post(
                    self.url, content=_dump(done), headers=self._headers(done, session=session)
                )
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("could not reopen the MCP session: %s", exc)
                return False
            log.info("MCP session reopened after the app restarted")
            return session is not None

    async def _close_session(self) -> None:
        if self.session_id is None:
            return
        headers = {SESSION_HEADER: self.session_id}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        with anyio.move_on_after(2), contextlib.suppress(httpx.HTTPError):
            await self.http.delete(self.url, headers=headers)

    async def _error(self, request_id: RequestId | None, code: int, text: str) -> None:
        if request_id is not None and request_id in self._cancelled:
            return  # a cancelled request gets no answer
        error = JSONRPCError(jsonrpc="2.0", id=request_id, error=ErrorData(code=code, message=text))
        await self.send(SessionMessage(error))


def http_client() -> httpx.AsyncClient:
    """Direct connections only (no proxy from the environment), no redirects followed."""
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        trust_env=False,
        follow_redirects=False,
        headers={"user-agent": f"vfe-mcp-stdio/{__version__}"},
    )


async def serve_stdio(url: str, token: str | None = None) -> None:
    """Run the bridge on this process's stdin/stdout until the client closes stdin."""
    async with http_client() as http, stdio_server() as (incoming, outgoing), outgoing:
        await Bridge(url, http, outgoing.send, token).run(incoming)
