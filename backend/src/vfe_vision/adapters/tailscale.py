"""This machine on the tailnet, read from the ``tailscale`` command line (read-only calls).

``tailscale status --json`` gives the state, the addresses and the MagicDNS name in one call;
``tailscale ip -4`` is the fallback when the JSON cannot be read. Nothing is ever changed.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.procs import run_process

TIMEOUT_S = 5
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")  # CGNAT range Tailscale assigns from
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


@dataclass(frozen=True, slots=True)
class TailscaleSelf:
    """What this machine is on the tailnet (``running`` False: Tailscale is stopped or logged
    out, its addresses do not answer)."""

    running: bool
    state: str
    ipv4: tuple[str, ...] = ()
    dns_name: str | None = None  # MagicDNS name, without the trailing dot

    @property
    def names(self) -> tuple[str, ...]:
        """The full MagicDNS name and its short form (both resolve on the tailnet)."""
        if not self.dns_name:
            return ()
        short = self.dns_name.split(".", 1)[0]
        return (self.dns_name, short) if short != self.dns_name else (self.dns_name,)


def in_tailnet(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip in (TAILNET_V4 if ip.version == 4 else TAILNET_V6)


def find_cli(configured: str = "tailscale") -> str | None:
    """The ``tailscale`` executable: configured path, PATH, then the default install folder."""
    found = shutil.which(configured)
    if found:
        return found
    default = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tailscale"
    candidate = default / "tailscale.exe"
    return str(candidate) if candidate.is_file() else None


def parse_status(payload: dict[str, Any]) -> TailscaleSelf:
    """``tailscale status --json`` → this machine (pure, for tests)."""
    state = str(payload.get("BackendState") or "Unknown")
    me: dict[str, Any] = payload.get("Self") or {}
    ips = tuple(ip for ip in (str(x) for x in me.get("TailscaleIPs") or []) if ":" not in ip and ip)
    tailnet: dict[str, Any] = payload.get("CurrentTailnet") or {}
    magic = tailnet.get("MagicDNSEnabled", True)
    name = str(me.get("DNSName") or "").rstrip(".").lower() or None
    return TailscaleSelf(
        running=state == "Running" and bool(ips),
        state=state,
        ipv4=ips,
        dns_name=name if magic else None,
    )


def read_self(cli: str) -> TailscaleSelf:
    """Ask the local Tailscale service; raises ``ExternalToolError`` when it does not answer."""
    result = run_process(
        [cli, "status", "--json"], timeout_s=TIMEOUT_S, check=False, tool_name="tailscale"
    )
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout_text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            return parse_status(payload)
    fallback = run_process(
        [cli, "ip", "-4"], timeout_s=TIMEOUT_S, check=False, tool_name="tailscale"
    )
    ips = tuple(
        line.strip() for line in fallback.stdout_text.splitlines() if in_tailnet(line.strip())
    )
    if fallback.returncode == 0 and ips:
        return TailscaleSelf(running=True, state="Running", ipv4=ips)
    detail = (result.stderr_text or fallback.stderr_text).strip().splitlines()
    raise ExternalToolError(
        f"Tailscale ne répond pas : {detail[-1] if detail else 'aucune adresse'}", tool="tailscale"
    )
