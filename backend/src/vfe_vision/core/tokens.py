"""The API token asked of devices that are not this computer.

``VFE_API_TOKEN`` wins when it is set. Otherwise a random token is generated the first time
remote access is enabled and kept in the data directory, readable by the current user only.
"""

from __future__ import annotations

import getpass
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Literal

from vfe_vision.core.atomic_io import atomic_write_text
from vfe_vision.core.config import Settings
from vfe_vision.core.logging import get_logger
from vfe_vision.core.procs import CREATE_NO_WINDOW

log = get_logger(__name__)
TOKEN_BYTES = 32  # 256 bits: out of reach of any guessing
TokenSource = Literal["env", "file"]


def read_token_file(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return token or None


def write_new_token(path: Path) -> str:
    """Write a fresh token to ``path`` (replacing any previous one) and restrict the file."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    atomic_write_text(path, token + "\n")
    restrict_to_owner(path)
    return token


def restrict_to_owner(path: Path) -> None:
    """Best effort: only the current user may read the file (the data directory already is
    per-user on Windows; this also drops the inherited Administrators and SYSTEM entries)."""
    if sys.platform == "win32":
        user = getpass.getuser()
        domain = os.environ.get("USERDOMAIN")
        account = f"{domain}\\{user}" if domain else user
        icacls = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "icacls.exe"
        try:
            subprocess.run(
                [str(icacls), str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
                check=True,
                capture_output=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("token file permissions left as inherited", path=str(path), error=str(exc))
    else:
        path.chmod(0o600)


def current_token(settings: Settings, *, create: bool) -> tuple[str | None, TokenSource | None]:
    """The token in force and where it comes from; ``create`` generates one if there is none."""
    if settings.api_token is not None:
        return settings.api_token.get_secret_value(), "env"
    token = read_token_file(settings.api_token_path)
    if token is None and create:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        token = write_new_token(settings.api_token_path)
        log.info("api token created", path=str(settings.api_token_path))
    return token, ("file" if token is not None else None)
