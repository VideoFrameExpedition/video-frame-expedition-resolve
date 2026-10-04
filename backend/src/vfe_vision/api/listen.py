"""Where ``vfe serve`` listens: the loopback, plus this machine's Tailscale address.

Each address gets its own socket (never 0.0.0.0: nothing is exposed on the local network). An
extra address that cannot be listened on — Tailscale stopped at start-up — is skipped with a
notice: the app still starts on the loopback.
"""

from __future__ import annotations

import errno
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from vfe_vision.adapters import tailscale
from vfe_vision.adapters.tailscale import TailscaleSelf, in_tailnet
from vfe_vision.api.access import AccessConfig, url_host
from vfe_vision.core.config import Settings, is_loopback
from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.tokens import current_token

OFF = "accès depuis les autres appareils coupé jusqu'au prochain démarrage"


@dataclass(frozen=True, slots=True)
class ListenPlan:
    primary: str
    extra: tuple[str, ...] = ()
    dns_names: tuple[str, ...] = ()  # this machine's MagicDNS names, with its Tailscale IPs
    notices: tuple[str, ...] = ()


@dataclass
class Listeners:
    sockets: list[socket.socket]
    bound: list[str] = field(default_factory=list)  # extra addresses actually listened on
    notices: list[str] = field(default_factory=list)

    def close(self) -> None:
        for sock in self.sockets:
            sock.close()


def plan_listen(
    settings: Settings,
    *,
    find_cli: Callable[[str], str | None] = tailscale.find_cli,
    read_self: Callable[[str], TailscaleSelf] = tailscale.read_self,
) -> ListenPlan:
    """The addresses to listen on; Tailscale is asked (read-only) only when it is concerned."""
    extra = [h for h in settings.extra_hosts if h != settings.host]
    notices: list[str] = []
    names: tuple[str, ...] = ()
    if settings.tailscale or any(in_tailnet(h) for h in extra):
        me = _discover(settings, notices, find_cli, read_self)
        if me is not None:
            if settings.tailscale:
                extra += [ip for ip in me.ipv4 if ip not in extra]
            if any(ip in me.ipv4 for ip in extra):
                names = me.names
    return ListenPlan(settings.host, tuple(extra), names, tuple(notices))


def _discover(
    settings: Settings,
    notices: list[str],
    find_cli: Callable[[str], str | None],
    read_self: Callable[[str], TailscaleSelf],
) -> TailscaleSelf | None:
    cli = find_cli(settings.tailscale_path)
    if cli is None:
        if settings.tailscale:
            notices.append(f"Tailscale est introuvable (tailscale.exe) : {OFF}.")
        return None
    try:
        me = read_self(cli)
    except (ExternalToolError, OSError) as exc:
        if settings.tailscale:
            detail = exc.detail if isinstance(exc, ExternalToolError) else str(exc)
            notices.append(f"{detail.rstrip('.')} : {OFF}.")
        return None
    if not me.running:
        if settings.tailscale:
            notices.append(f"Tailscale n'est pas connecté (état : {me.state}) : {OFF}.")
        return None
    return me


def bind_socket(host: str, port: int) -> socket.socket:
    """A socket bound to one address, as uvicorn would listen on it."""
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":  # Windows: default rules, as asyncio (no port sharing)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def _unavailable(exc: OSError) -> str:
    if getattr(exc, "winerror", None) == 10049 or exc.errno == errno.EADDRNOTAVAIL:
        return "adresse absente de cet ordinateur"
    if getattr(exc, "winerror", None) == 10048 or exc.errno == errno.EADDRINUSE:
        return "port déjà utilisé"
    return exc.strerror or str(exc)


def open_listeners(plan: ListenPlan, port: int) -> Listeners:
    """Bind every address; the primary one must succeed (``OSError`` otherwise)."""
    listeners = Listeners([bind_socket(plan.primary, port)])
    for address in plan.extra:
        try:
            listeners.sockets.append(bind_socket(address, port))
        except OSError as exc:
            listeners.notices.append(
                f"Écoute impossible sur {url_host(address)}:{port} ({_unavailable(exc)}) : {OFF}."
            )
        else:
            listeners.bound.append(address)
    return listeners


def access_config(settings: Settings, plan: ListenPlan, listeners: Listeners) -> AccessConfig:
    """What the app needs to know about where it listens; creates the token the first time
    remote access is asked for (``VFE_API_TOKEN`` wins when it is set)."""
    token, source = current_token(settings, create=settings.remote_requested)
    remote = ([] if is_loopback(settings.host) else [settings.host]) + listeners.bound
    on_tailnet = any(in_tailnet(h) for h in listeners.bound)
    return AccessConfig(
        port=settings.port,
        token=token,
        token_source=source,
        token_path=settings.api_token_path if source == "file" else None,
        remote_hosts=tuple(remote),
        dns_names=plan.dns_names if on_tailnet else (),
        remote_requested=settings.remote_requested,
        notices=(*plan.notices, *listeners.notices),
    )
