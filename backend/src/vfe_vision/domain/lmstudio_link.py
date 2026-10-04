"""Where LM Studio runs: on this computer by default, or on another one chosen in the
interface, with the addresses used before kept at hand. An address is always held in full
(``http://192.168.1.20:1234``); the user may type much less (``192.168.1.20``)."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PORT = 1234  # LM Studio's own default
MAX_PAST = 8
MAX_ADDRESS = 300
MAX_TOKEN = 500

_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?$")
# What LM Studio shows next to its address, pasted along with it.
_API_PATHS = frozenset({"", "/v1", "/api/v0", "/api/v1"})


@dataclass(frozen=True, slots=True)
class LmStudioTarget:
    """The LM Studio to talk to, and the API token it asks for (if any)."""

    url: str
    token: str | None = None


class PastConnection(BaseModel):
    """An LM Studio the application was connected to."""

    model_config = ConfigDict(extra="ignore")

    url: str
    last_used_at: datetime
    token: str | None = None  # sent to this address only


class LmStudioLink(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None  # None: the installation's address (``VFE_LMSTUDIO_URL``)
    past: list[PastConnection] = Field(default_factory=list)


def address_of(text: str) -> str:
    """The address typed by a user, in full: ``192.168.1.20`` gives ``http://192.168.1.20:1234``.

    Raises ``ValueError`` (a sentence for the user) when it is not the address of a server.
    """
    typed = text.strip()
    if not typed:
        raise ValueError("Donnez l'adresse de l'ordinateur où tourne LM Studio.")
    if len(typed) > MAX_ADDRESS:
        raise ValueError("Cette adresse est trop longue.")
    wrong = ValueError(
        f"« {typed} » n'est pas une adresse : écrivez par exemple 192.168.1.20 ou "
        "192.168.1.20:1234."
    )
    try:
        parts = urlsplit(typed if "://" in typed else f"http://{typed}")
        port = parts.port
    except ValueError:
        raise wrong from None
    if parts.scheme not in {"http", "https"}:
        raise ValueError("L'adresse commence par http:// ou https:// (ou par rien du tout).")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise wrong
    if parts.path.rstrip("/") not in _API_PATHS:
        raise ValueError(
            "Donnez seulement l'ordinateur et le port (192.168.1.20:1234), sans chemin."
        )
    host = (parts.hostname or "").lower()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not _NAME.match(host) or ".." in host:
            raise wrong from None
    else:
        if ip.is_unspecified or ip.is_multicast:
            raise ValueError(
                f"« {host} » ne désigne aucun ordinateur : donnez son adresse précise."
            )
        if ip.version == 6:
            host = f"[{ip.compressed}]"
    if port is None and parts.scheme == "http":
        port = DEFAULT_PORT
    return f"{parts.scheme}://{host}" + ("" if port is None else f":{port}")


def host_of(url: str) -> str:
    """The computer an address names (no scheme, no port, no brackets)."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_local(url: str) -> bool:
    """Whether an address is this very computer (loopback): images sent there never leave it."""
    host = host_of(url)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def target_of(
    link: LmStudioLink, default_url: str, default_token: str | None = None
) -> LmStudioTarget:
    """The LM Studio in use. The installation's token goes with the installation's address, a
    remembered one with its own: a token is never sent to another computer than its own."""
    if link.url is None:
        return LmStudioTarget(default_url.rstrip("/"), default_token)
    return LmStudioTarget(link.url, token_of(link, link.url))


def token_of(link: LmStudioLink, url: str) -> str | None:
    return next((past.token for past in link.past if past.url == url), None)


def remembered(
    past: list[PastConnection], url: str, now: datetime, token: str | None
) -> list[PastConnection]:
    """The past connections with this one first (the oldest ones beyond ``MAX_PAST`` dropped)."""
    others = [connection for connection in past if connection.url != url]
    return [PastConnection(url=url, last_used_at=now, token=token), *others][:MAX_PAST]
