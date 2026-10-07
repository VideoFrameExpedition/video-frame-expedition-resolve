"""Access from other devices (Tailscale) and how to connect MCP clients.

The models live here rather than in ``api.schemas``: they only describe this router.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from vfe_vision.api.access import (
    FAILURE_DELAY_S,
    SESSION_COOKIE,
    SESSION_TTL_S,
    AccessGuard,
    TooManyAttemptsError,
    UnauthorizedError,
    is_local_request,
    url_host,
)
from vfe_vision.api.deps import Container
from vfe_vision.api.schemas import ApiModel
from vfe_vision.core.config import Platform, is_loopback, platform_name
from vfe_vision.core.errors import ConflictError

router = APIRouter(prefix="/access", tags=["access"])

CLAUDE_STORE_PACKAGE = "Claude_pzs8sxrjxfjjc"  # Claude Desktop from the Microsoft Store (MSIX)
RESOLVE_MCP = Path("Blackmagic Design") / "DaVinci Resolve" / "ResolveMCP.exe"
# Where DaVinci Resolve Studio 21 puts its MCP server on a Mac (the first one found is shown;
# with none, the first one is shown as missing).
RESOLVE_MCP_MACOS = (
    Path("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP"),
)
RESOLVE_MCP_LINUX = (Path("/opt/resolve/bin/ResolveMCP"),)


class SessionOut(ApiModel):
    local: bool = Field(description="Browser open on the application's computer.")
    required: bool = Field(description="A token is required from this device.")
    authenticated: bool = Field(description="The interface can be used (no token needed or "
                                "session open).")  # fmt: skip
    remote: bool = Field(description="The application also listens beyond this computer.")


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=512, description="The API token.")


class RemoteOut(ApiModel):
    requested: bool = Field(description="Remote access requested (VFE_TAILSCALE, "
                            "VFE_EXTRA_HOSTS or VFE_HOST).")  # fmt: skip
    enabled: bool = Field(description="The application listens beyond this computer.")
    urls: list[str] = Field(description="Addresses of the interface from the other devices.")
    notices: list[str] = Field(description="Why remote access is off or partial.")


class TokenOut(ApiModel):
    available: bool = Field(description="A token exists (required from the other devices).")
    source: Literal["env", "file"] | None = Field(
        description="env: VFE_API_TOKEN; file: token generated in the data folder."
    )
    value: str | None = Field(description="The token, only for a browser open on this computer.")
    path: str | None = Field(description="Token file (only on this computer).")


class StdioCommandOut(ApiModel):
    command: str = Field(description="Program launched by the MCP client (uv, else Python).")
    args: list[str] = Field(description="Its arguments, without --url.")


class ConfigFileOut(ApiModel):
    kind: Literal["classic", "store"] = Field(
        description="classic : installateur de claude.ai (le seul sur macOS et Linux) ; "
        "store : Microsoft Store (MSIX)."
    )
    path: str
    installed: bool = Field(description="This installation of Claude Desktop is present.")
    exists: bool = Field(description="The configuration file already exists.")


class ResolveMcpOut(ApiModel):
    path: str
    installed: bool


class ConnectionsOut(ApiModel):
    viewer_local: bool = Field(description="Page viewed from the application's computer.")
    platform: Platform = Field(
        description="System of the application's computer: how its paths and command lines are "
        "written (quoting, configuration files of the clients)."
    )
    port: int
    local_url: str
    mcp_path: str = "/mcp"
    remote: RemoteOut
    token: TokenOut
    stdio: StdioCommandOut
    claude_desktop: list[ConfigFileOut]
    resolve_mcp: ResolveMcpOut


def _guard(request: Request) -> AccessGuard:
    guard: AccessGuard = request.app.state.access_guard
    return guard


@router.get("/session")
def session(request: Request) -> SessionOut:
    """What the interface must do: open, or ask for the token (remote device)."""
    guard = _guard(request)
    required = guard.required(request.scope)
    # The session cookie only: this open endpoint must not tell whether a guessed token is right.
    authenticated = not required or guard.session_valid(request.cookies.get(SESSION_COOKIE))
    return SessionOut(
        local=is_local_request(request.scope),
        required=required,
        authenticated=authenticated,
        remote=guard.config.remote_enabled,
    )


@router.post(
    "/login",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"description": "Wrong token"}, 429: {"description": "Too many attempts"}},
)
async def login(body: LoginIn, request: Request) -> Response:
    """Open a session (HttpOnly cookie, SameSite=Strict, 30 days) with the API token."""
    guard = _guard(request)
    client = guard.client(request.scope)
    wait = guard.attempts.blocked_for(client)
    if wait > 0:
        seconds = math.ceil(wait)
        raise TooManyAttemptsError(
            f"Trop d'essais avec un mauvais jeton : réessayez dans {seconds} s.",
            retry_after_s=seconds,
        )
    if guard.token is None:
        raise ConflictError("L'accès à distance n'est pas activé : aucun jeton à vérifier.")
    if not guard.token_matches(body.token.strip()):
        guard.attempts.failure(client)
        await anyio.sleep(FAILURE_DELAY_S)
        raise UnauthorizedError("Jeton incorrect.")
    guard.attempts.success(client)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        SESSION_COOKIE, guard.new_session(), max_age=SESSION_TTL_S, path="/",
        secure=request.url.scheme == "https", httponly=True, samesite="strict",
    )  # fmt: skip
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    """Close this browser's session."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=request.url.scheme == "https", httponly=True,
        samesite="strict",
    )  # fmt: skip
    return response


@router.get("/connections")
def connections(request: Request, c: Container) -> ConnectionsOut:
    """Addresses, token (only on this computer) and commands to connect an MCP client."""
    config = _guard(request).config
    local = is_local_request(request.scope)
    host = c.settings.host if is_loopback(c.settings.host) and c.settings.host else "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    token = TokenOut(
        available=config.token is not None,
        source=config.token_source,
        value=config.token if local else None,
        path=str(config.token_path) if local and config.token_path else None,
    )
    return ConnectionsOut(
        viewer_local=local,
        platform=platform_name(),
        port=config.port,
        local_url=f"http://{url_host(host)}:{config.port}",
        remote=RemoteOut(
            requested=config.remote_requested,
            enabled=config.remote_enabled,
            urls=config.remote_urls(),
            notices=list(config.notices),
        ),
        token=token,
        stdio=stdio_command(),
        claude_desktop=claude_desktop_configs(),
        resolve_mcp=resolve_mcp(),
    )


def stdio_command() -> StdioCommandOut:
    """``vfe mcp-stdio`` as a client launches it: the interpreter running this app (absolute
    path, so that neither the client's PATH nor its working folder matter)."""
    return StdioCommandOut(command=sys.executable, args=["-m", "vfe_vision", "mcp-stdio"])


def claude_desktop_configs() -> list[ConfigFileOut]:
    """Where Claude Desktop keeps its configuration on this system: (kind, a folder whose
    presence means that installation exists, the configuration folder)."""
    places: list[tuple[Literal["classic", "store"], Path, Path]]
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        package = local / "Packages" / CLAUDE_STORE_PACKAGE
        places = [
            ("classic", appdata / "Claude", appdata / "Claude"),
            ("store", package, package / "LocalCache" / "Roaming" / "Claude"),
        ]
    elif sys.platform == "darwin":
        folder = Path.home() / "Library" / "Application Support" / "Claude"
        app = Path("/Applications/Claude.app")
        places = [("classic", app if app.is_dir() else folder, folder)]
    else:
        folder = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude"
        places = [("classic", folder, folder)]
    return [
        ConfigFileOut(
            kind=kind,
            path=str(folder / "claude_desktop_config.json"),
            installed=root.is_dir(),
            exists=(folder / "claude_desktop_config.json").is_file(),
        )
        for kind, root, folder in places
    ]


def resolve_mcp() -> ResolveMcpOut:
    """DaVinci Resolve Studio's own MCP server on this computer."""
    if sys.platform == "win32":
        places = (Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / RESOLVE_MCP,)
    elif sys.platform == "darwin":
        places = RESOLVE_MCP_MACOS
    else:
        places = RESOLVE_MCP_LINUX
    path = next((place for place in places if place.is_file()), places[0])
    return ResolveMcpOut(path=str(path), installed=path.is_file())
