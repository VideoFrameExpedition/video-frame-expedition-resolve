"""Who may use the app from where.

* A request is **local** when it reaches the loopback socket *and* names a loopback host: it
  needs no token, exactly as before. A proxy on this computer (``tailscale serve``…) forwards
  remote requests under another ``Host``: those are not local.
* Any other request needs the token: ``Authorization: Bearer``, the ``access_token`` query
  parameter (``<video>``, ``<img>``, EventSource), or the session cookie set by the login page
  (HttpOnly, SameSite=Strict, signed with the token: changing the token ends every session).
* Wrong tokens are slowed down and, after a few, refused for a while (per client address).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import parse_qs

from starlette.requests import cookie_parser
from starlette.types import Scope

from vfe_vision.core.config import Settings, is_loopback
from vfe_vision.core.errors import VfeError
from vfe_vision.core.tokens import TokenSource

SESSION_COOKIE = "vfe_session"
SESSION_TTL_S = 30 * 24 * 3600
FAILURE_DELAY_S = 0.5  # every wrong token waits this long before its answer
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})

Credential = Literal["bearer", "query", "cookie"]


class UnauthorizedError(VfeError):
    code = "unauthorized"
    status = 401
    title = "Accès refusé"


class TooManyAttemptsError(VfeError):
    code = "too_many_attempts"
    status = 429
    title = "Trop d'essais"


def url_host(address: str) -> str:
    """An address as written in a URL or a ``Host`` header (IPv6 in brackets)."""
    return f"[{address}]" if ":" in address else address


@dataclass(frozen=True, slots=True)
class AccessConfig:
    """Where the app listens and which token remote devices must present."""

    port: int
    token: str | None = None
    token_source: TokenSource | None = None
    token_path: Path | None = None
    remote_hosts: tuple[str, ...] = ()  # non-loopback addresses listened on
    dns_names: tuple[str, ...] = ()  # names of this machine on the tailnet
    remote_requested: bool = False
    notices: tuple[str, ...] = ()  # why remote access is off or partial (French)

    @classmethod
    def local(cls, settings: Settings) -> AccessConfig:
        """Loopback only, as ``create_app`` builds it by default (tests, ``--reload``)."""
        token = settings.api_token.get_secret_value() if settings.api_token else None
        remote = () if is_loopback(settings.host) else (settings.host,)
        return cls(
            port=settings.port,
            token=token,
            token_source="env" if token else None,
            remote_hosts=remote,
            remote_requested=settings.remote_requested,
        )

    @property
    def remote_enabled(self) -> bool:
        return bool(self.remote_hosts)

    def trusted_hosts(self) -> list[str]:
        """``Host`` header values accepted (DNS rebinding): loopback, then the remote ones."""
        remote = [url_host(h) for h in self.remote_hosts] + list(self.dns_names)
        return sorted({"127.0.0.1", "localhost", "[::1]", *remote})

    def remote_origins(self) -> set[str]:
        """Pages allowed to write: the remote addresses, and the MagicDNS name over HTTPS
        (``tailscale serve`` in front of the app)."""
        direct = {f"http://{host}:{self.port}" for host in self.url_hosts()}
        return direct | {f"https://{name}" for name in self.dns_names if "." in name}

    def url_hosts(self) -> list[str]:
        """Remote addresses as written in a URL: IPs first, then names; 0.0.0.0 left out."""
        ips = [url_host(h) for h in self.remote_hosts if not _unspecified(h)]
        return ips + list(self.dns_names)

    def remote_urls(self) -> list[str]:
        return [f"http://{host}:{self.port}" for host in self.url_hosts()]


def _unspecified(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:  # a name (VFE_HOST)
        return False


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _client_ip(scope: Scope) -> str | None:
    client = scope.get("client")
    return str(client[0]) if client else None


def is_local_request(scope: Scope) -> bool:
    """Loopback socket and loopback ``Host``: this computer, not a forwarded request."""
    client = _client_ip(scope)
    try:
        if client is None or not ipaddress.ip_address(client).is_loopback:
            return False
    except ValueError:
        return False
    headers = dict(scope.get("headers") or [])
    host = _hostname(headers.get(b"host", b"").decode("latin-1"))
    return host in LOCAL_HOSTNAMES or (bool(host) and is_loopback(host))


def _equal(supplied: str, expected: str) -> bool:
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _signature(token: str, expires: int) -> str:
    digest = hmac.new(token.encode(), f"vfe-session:{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_session(token: str, now: float) -> str:
    expires = int(now) + SESSION_TTL_S
    return f"{expires}.{_signature(token, expires)}"


def verify_session(token: str, value: str, now: float) -> bool:
    expires_text, _, signature = value.partition(".")
    if not expires_text.isdigit() or not signature:
        return False
    expires = int(expires_text)
    return expires > now and _equal(signature, _signature(token, expires))


@dataclass
class FailedAttempts:
    """Wrong tokens per client address: after ``limit`` within ``window_s``, every attempt from
    that address is refused for ``lockout_s`` (the right token included)."""

    limit: int = 10
    window_s: float = 300.0
    lockout_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    max_clients: ClassVar[int] = 1024
    _failures: OrderedDict[str, list[float]] = field(default_factory=OrderedDict)
    _blocked: dict[str, float] = field(default_factory=dict)

    def blocked_for(self, client: str) -> float:
        until = self._blocked.get(client)
        if until is None:
            return 0.0
        left = until - self.clock()
        if left <= 0:
            del self._blocked[client]
            return 0.0
        return left

    def failure(self, client: str) -> None:
        now = self.clock()
        recent = [t for t in self._failures.pop(client, []) if now - t < self.window_s]
        recent.append(now)
        if len(recent) >= self.limit:
            self._blocked[client] = now + self.lockout_s
            recent = []
        self._failures[client] = recent
        while len(self._failures) > self.max_clients:
            self._failures.popitem(last=False)

    def success(self, client: str) -> None:
        self._failures.pop(client, None)


@dataclass
class AccessGuard:
    """The token checks shared by the security middleware and the login endpoints."""

    config: AccessConfig
    attempts: FailedAttempts = field(default_factory=FailedAttempts)
    clock: Callable[[], float] = time.time

    @property
    def token(self) -> str | None:
        return self.config.token

    def required(self, scope: Scope) -> bool:
        return self.token is not None and not is_local_request(scope)

    def token_matches(self, supplied: str) -> bool:
        return self.token is not None and bool(supplied) and _equal(supplied, self.token)

    def new_session(self) -> str:
        if self.token is None:
            raise UnauthorizedError("Aucun jeton : l'accès à distance n'est pas activé.")
        return issue_session(self.token, self.clock())

    def session_valid(self, value: str | None) -> bool:
        if self.token is None or not value:
            return False
        return verify_session(self.token, value, self.clock())

    def credential(
        self, scope: Scope, headers: Mapping[bytes, bytes], *, bearer_only: bool = False
    ) -> tuple[Credential | None, bool]:
        """The credential presented and whether it is right (``None``: nothing presented).

        A stale session cookie (token changed, expired) is wrong but is not counted as a guess.
        """
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if auth[:7].lower() == "bearer ":
            return "bearer", self.token_matches(auth[7:].strip())
        if bearer_only:
            return None, False
        supplied = _query_token(scope)
        if supplied is not None:
            return "query", self.token_matches(supplied)
        cookie = cookie_parser(headers.get(b"cookie", b"").decode("latin-1")).get(SESSION_COOKIE)
        if cookie is not None:
            return "cookie", self.session_valid(cookie)
        return None, False

    def client(self, scope: Scope) -> str:
        return _client_ip(scope) or "?"


def _query_token(scope: Scope) -> str | None:
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    values = query.get("access_token")
    return values[0] if values else None
