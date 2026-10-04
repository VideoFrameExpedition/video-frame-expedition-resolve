"""Local-first request security (SECURITY.md).

* Host allow-list (DNS rebinding) is enforced by ``TrustedHostMiddleware``.
* State-changing requests must carry ``X-VFE-Client`` — a custom header cannot be sent
  cross-origin without a CORS preflight, which this server never grants — and, when present,
  an ``Origin`` from the allow-list (required with the session cookie). This blocks CSRF from
  any web page the user visits.
* Off this computer the token is required (``api.access``): Bearer header, ``access_token``
  query parameter (EventSource/<img>/<video> cannot set headers), or the login page's session
  cookie. The login endpoints themselves stay open so that the login page can work.
* ``/mcp`` off this computer accepts the Bearer header only; the MCP transport performs its own
  Host/Origin validation.
* No response may be shown inside another site's frame (``FrameGuardMiddleware``): a hostile
  page cannot overlay the interface's switches to steer the user's clicks.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable

import anyio
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vfe_vision.api.access import FAILURE_DELAY_S, SESSION_COOKIE, AccessGuard, Credential

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CLIENT_HEADER = b"x-vfe-client"
OPEN_PATHS = frozenset({"/api/v1/access/session", "/api/v1/access/login", "/api/v1/access/logout"})
CLEAR_SESSION = f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict".encode()


def allowed_origins(port: int, extra: Iterable[str] = ()) -> set[str]:
    hosts = ("127.0.0.1", "localhost")
    origins = {f"http://{h}:{p}" for h in hosts for p in (port, 5173)}
    return origins | set(extra)


class LocalSecurityMiddleware:
    def __init__(self, app: ASGIApp, *, origins: set[str], guard: AccessGuard) -> None:
        self.app = app
        self.origins = origins
        self.guard = guard

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        is_mcp = path == "/mcp" or path.startswith("/mcp/")
        if not is_mcp and not path.startswith("/api/"):  # the web interface's files: public
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        via: Credential | None = None
        if self.guard.required(scope) and path not in OPEN_PATHS:
            denied, via = await self._authenticate(scope, headers, send, mcp=is_mcp)
            if denied:
                return
        if not is_mcp and scope["method"].upper() not in SAFE_METHODS:
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin and origin not in self.origins:
                await _deny(send, 403, f"Origine refusée : {origin}", "forbidden_origin")
                return
            if not origin and via == "cookie":
                await _deny(send, 403, "En-tête Origin requis avec une session.", "missing_origin")
                return
            if not headers.get(CLIENT_HEADER):
                await _deny(send, 403, "En-tête X-VFE-Client requis.", "missing_client_header")
                return
        await self.app(scope, receive, send)

    async def _authenticate(
        self, scope: Scope, headers: dict[bytes, bytes], send: Send, *, mcp: bool
    ) -> tuple[bool, Credential | None]:
        """Check the token of a request from another device; True when it has been refused."""
        client = self.guard.client(scope)
        wait = self.guard.attempts.blocked_for(client)
        if wait > 0:
            seconds = math.ceil(wait)
            await _deny(
                send, 429, f"Trop d'essais avec un mauvais jeton : réessayez dans {seconds} s.",
                "too_many_attempts", extra=[(b"retry-after", str(seconds).encode())],
            )  # fmt: skip
            return True, None
        via, ok = self.guard.credential(scope, headers, bearer_only=mcp)
        if ok:
            return False, via
        extra: list[tuple[bytes, bytes]] = []
        if mcp:
            error = ', error="invalid_token"' if via else ""
            extra.append((b"www-authenticate", f'Bearer realm="vfe-vision"{error}'.encode()))
        if via is None:
            detail = "Jeton d'API requis depuis un autre appareil."
        elif via == "cookie":  # token changed or session expired: not a guess
            detail = "Session expirée : reconnectez-vous avec le jeton."
            extra.append((b"set-cookie", CLEAR_SESSION))
        else:
            self.guard.attempts.failure(client)
            await anyio.sleep(FAILURE_DELAY_S)
            detail = "Jeton d'API invalide."
        await _deny(send, 401, detail, "unauthorized", extra=extra)
        return True, via


FRAME_HEADERS = (
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
)


class FrameGuardMiddleware:
    """Adds the anti-framing headers to every HTTP response (clickjacking)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_guarded(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in FRAME_HEADERS if name not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_guarded)


async def _deny(
    send: Send, status: int, detail: str, code: str, *, extra: Iterable[tuple[bytes, bytes]] = ()
) -> None:
    body = json.dumps(
        {"type": f"https://vfe-vision.local/problems/{code}", "title": "Accès refusé",
         "status": status, "detail": detail, "code": code},
        ensure_ascii=False,
    ).encode("utf-8")  # fmt: skip
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
