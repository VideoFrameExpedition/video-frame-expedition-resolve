"""Where the model server runs: LM Studio on this computer by default, or another server chosen
in the interface (LM Studio elsewhere, or a server compatible with OpenAI's API such as vLLM),
with the addresses used before kept at hand. An address is always held in full
(``http://192.168.1.20:1234``, ``http://gpu-box:8000/v1``); the user may type much less."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PORT = 1234  # LM Studio's own default
OPENAI_PATH = "/v1"  # where an OpenAI-compatible server answers, unless told otherwise
DEFAULT_PARALLEL = 4  # requests sent at once to an OpenAI-compatible server
MAX_PARALLEL = 32
MAX_PAST = 8
MAX_ADDRESS = 300
MAX_TOKEN = 500

_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?$")
# What LM Studio shows next to its address, pasted along with it.
_API_PATHS = frozenset({"", "/v1", "/api/v0", "/api/v1"})
# The path of an OpenAI-compatible server behind a proxy: plain segments only.
_PATH = re.compile(r"^(?:/[A-Za-z0-9._~\-]+)+$")


class ServerKind(StrEnum):
    """The kind of model server. None (where a kind is optional) means: found out, LM Studio
    first, then an OpenAI-compatible server when LM Studio's own API is not there."""

    LMSTUDIO = "lmstudio"
    OPENAI = "openai"  # an OpenAI-compatible server: vLLM, llama.cpp's server…


@dataclass(frozen=True, slots=True)
class LmStudioTarget:
    """The model server to talk to, the API token it asks for (if any), and how to use it.

    ``parallel`` and ``vision`` only matter for an OpenAI-compatible server, which does not
    say them: how many requests to send at once, and whether its models see images.
    """

    url: str
    token: str | None = None
    kind: ServerKind | None = None
    parallel: int | None = None
    vision: bool = True


class PastConnection(BaseModel):
    """A model server the application was connected to."""

    model_config = ConfigDict(extra="ignore")

    url: str
    last_used_at: datetime
    token: str | None = None  # sent to this address only
    kind: ServerKind | None = None
    parallel: int | None = Field(default=None, ge=1, le=MAX_PARALLEL)
    vision: bool = True


class LmStudioLink(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None  # None: the installation's address (``VFE_LMSTUDIO_URL``)
    past: list[PastConnection] = Field(default_factory=list)


def address_of(text: str, kind: ServerKind | None = None) -> str:
    """The address typed by a user, in full.

    For LM Studio (or a kind to find out), ``192.168.1.20`` gives ``http://192.168.1.20:1234``:
    the path LM Studio shows next to its address is dropped. For an OpenAI-compatible server
    the port is the scheme's own unless typed, and the path is kept (``/v1`` when there is
    none): ``gpu-box:8000`` gives ``http://gpu-box:8000/v1``.

    Raises ``ValueError`` (a sentence for the user) when it is not the address of a server.
    """
    typed = text.strip()
    if not typed:
        raise ValueError("Donnez l'adresse de l'ordinateur où tourne le serveur de modèles.")
    if len(typed) > MAX_ADDRESS:
        raise ValueError("Cette adresse est trop longue.")
    wrong = ValueError(
        f"« {typed} » n'est pas une adresse : écrivez par exemple 192.168.1.20 ou "
        + ("192.168.1.20:8000." if kind is ServerKind.OPENAI else "192.168.1.20:1234.")
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
    path = parts.path.rstrip("/")
    if kind is ServerKind.OPENAI:
        if path and not _PATH.match(path):
            raise ValueError(
                "Le chemin de l'adresse ne peut contenir que des lettres, des chiffres, « - », "
                "« _ », « . » et « / » (par exemple /v1)."
            )
        path = path or OPENAI_PATH
    elif path in _API_PATHS:
        path = ""
    else:
        raise ValueError(
            "Donnez seulement l'ordinateur et le port (192.168.1.20:1234), sans chemin. Un "
            "serveur compatible OpenAI derrière un chemin se choisit comme tel."
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
    if port is None and parts.scheme == "http" and kind is not ServerKind.OPENAI:
        port = DEFAULT_PORT
    return f"{parts.scheme}://{host}" + ("" if port is None else f":{port}") + path


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


def target_of(link: LmStudioLink, default: LmStudioTarget) -> LmStudioTarget:
    """The model server in use. The installation's token goes with the installation's address,
    a remembered one with its own: a token is never sent to another computer than its own."""
    if link.url is None:
        return LmStudioTarget(
            default.url.rstrip("/"), default.token, default.kind, default.parallel, default.vision
        )
    past = past_of(link, link.url)
    if past is None:
        return LmStudioTarget(link.url)
    return LmStudioTarget(link.url, past.token, past.kind, past.parallel, past.vision)


def past_of(link: LmStudioLink, url: str) -> PastConnection | None:
    return next((past for past in link.past if past.url == url), None)


def token_of(link: LmStudioLink, url: str) -> str | None:
    past = past_of(link, url)
    return past.token if past else None


def remembered(
    past: list[PastConnection], target: LmStudioTarget, now: datetime
) -> list[PastConnection]:
    """The past connections with this one first (the oldest ones beyond ``MAX_PAST`` dropped)."""
    others = [connection for connection in past if connection.url != target.url]
    this = PastConnection(
        url=target.url,
        last_used_at=now,
        token=target.token,
        kind=target.kind,
        parallel=target.parallel,
        vision=target.vision,
    )
    return [this, *others][:MAX_PAST]
