"""Infrastructure configuration (environment variables prefixed with ``VFE_`` or a ``.env`` file).

User preferences (models per task, prompts, pipeline stages, online services…) are not here:
they live in the database and are edited from the web UI (see ``services.settings``).
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from platformdirs import user_data_dir
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

APP_NAME = "vfe-vision"
# Where Homebrew and uv put their programs on a Mac: a Terminal has them in its PATH, an
# application started from the Finder or at login may not.
MACOS_TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin")
# Homebrew's ffmpeg-full: the build with zscale, which the HDR tone mapping needs. It is keg-only
# (never linked into the PATH), so its own folder is searched first for FFmpeg's programs.
FFMPEG_FULL_DIRS = ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin")
FFMPEG_PROGRAMS = frozenset({"ffmpeg", "ffprobe"})
TOOL_SETTINGS = ("ffmpeg_path", "ffprobe_path", "exiftool_path")


Platform = Literal["windows", "macos", "linux"]


def platform_name() -> Platform:
    """The system the application runs on, as the interface names it."""
    if sys.platform == "win32":
        return "windows"
    elif sys.platform == "darwin":
        return "macos"
    else:
        return "linux"


def default_data_dir() -> Path:
    """Per-user data directory: ``%LOCALAPPDATA%\\vfe-vision`` on Windows,
    ``~/Library/Application Support/vfe-vision`` on macOS."""
    return Path(user_data_dir(APP_NAME, appauthor=False, roaming=False))


def find_tool(
    name: str,
    *,
    places: tuple[str, ...] = MACOS_TOOL_DIRS,
    ffmpeg_places: tuple[str, ...] = FFMPEG_FULL_DIRS,
) -> str:
    """``name`` as given when it is a path, or a program found in the PATH. On macOS, FFmpeg's
    full build comes first (``FFMPEG_FULL_DIRS``), and a program missing from the PATH is looked
    for in its usual folders (else the name, for a clear error later)."""
    if sys.platform == "darwin":
        if os.sep in name:
            return name
        if name in FFMPEG_PROGRAMS:
            full = _executable_in(name, ffmpeg_places)
            if full is not None:
                return full
        if shutil.which(name):
            return name
        return _executable_in(name, places) or name
    else:
        return name


def _executable_in(name: str, places: tuple[str, ...]) -> str | None:
    for place in places:
        candidate = Path(place).expanduser() / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def is_loopback(host: str) -> bool:
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def listen_address(value: str) -> str:
    """An IP address the server may listen on (never a name, never 0.0.0.0 or ::)."""
    text = value.strip().removeprefix("[").removesuffix("]")
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        raise ValueError(f"« {value} » n'est pas une adresse IP.") from None
    if address.is_unspecified or address.is_multicast:
        raise ValueError(f"« {value} » écouterait sur tout un réseau : donnez une adresse précise.")
    return str(address)


class Settings(BaseSettings):
    """Infrastructure settings, read once at start-up."""

    model_config = SettingsConfigDict(
        env_prefix="VFE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    api_token: SecretStr | None = None
    data_dir: Path = Field(default_factory=default_data_dir)
    # Remote access: extra addresses listened on next to ``host`` (each its own
    # socket, never 0.0.0.0), e.g. ``VFE_EXTRA_HOSTS=100.64.12.34``; ``VFE_TAILSCALE=true``
    # adds this machine's Tailscale IPv4 found at start-up. A token is then required off loopback.
    extra_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    tailscale: bool = False
    tailscale_path: str = "tailscale"

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    exiftool_path: str = "exiftool"
    # Resolve's scripting library; else RESOLVE_SCRIPT_LIB, else the default install.
    resolve_script_lib: str | None = None

    lmstudio_url: str = "http://127.0.0.1:1234"
    lmstudio_token: SecretStr | None = None
    lmstudio_timeout_s: float = Field(default=300.0, gt=0)
    # The kind of model server at that address: found out (LM Studio, else a server compatible
    # with OpenAI's API), or said: ``lmstudio``, or ``openai`` (vLLM…: the address then keeps its
    # path, e.g. http://gpu-box:8000/v1). The two others only matter for an OpenAI-compatible
    # server, which does not tell them: requests sent at once, and whether its models see images.
    model_server: Literal["auto", "lmstudio", "openai"] = "auto"
    model_server_parallel: int = Field(default=4, ge=1, le=32)
    model_server_vision: bool = True

    # Online services: replaceable by a self-hosted or paid provider.
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    open_meteo_historical_url: str = "https://historical-forecast-api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_api_key: SecretStr | None = None

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    worker_enabled: bool = True
    cpu_workers: int = Field(default_factory=lambda: max(2, (os.cpu_count() or 4) // 3), ge=1)
    max_concurrent_videos: int = Field(default=2, ge=1)
    # Speech recognition threads (one transcription at a time, below-normal priority).
    asr_threads: int = Field(
        default_factory=lambda: max(2, min(8, (os.cpu_count() or 4) - 2)), ge=1
    )
    # GPU lending: VRAM kept free for the vision model on top of a task's own need.
    gpu_vram_margin_mib: int = Field(default=1024, ge=256)
    nvidia_smi_path: str = "nvidia-smi"

    @field_validator("extra_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            value = [part for part in value.replace(";", ",").replace(" ", ",").split(",") if part]
        if isinstance(value, list | tuple):
            return list(dict.fromkeys(listen_address(str(item)) for item in value))
        return value

    @model_validator(mode="after")
    def _find_tools(self) -> Settings:
        for name in TOOL_SETTINGS:
            setattr(self, name, find_tool(getattr(self, name)))
        return self

    @model_validator(mode="after")
    def _require_token_off_loopback(self) -> Settings:
        if not is_loopback(self.host) and self.api_token is None:
            raise ValueError(
                "VFE_API_TOKEN est obligatoire quand VFE_HOST n'est pas une adresse locale."
            )
        return self

    # ------------------------------------------------------------------ derived paths
    @property
    def db_path(self) -> Path:
        return self.data_dir / "vfe.sqlite3"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def geonames_dir(self) -> Path:
        """Offline gazetteer built by ``vfe models geonames``."""
        return self.models_dir / "geonames"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def worker_lock_path(self) -> Path:
        return self.data_dir / "worker.lock"

    @property
    def api_token_path(self) -> Path:
        """Token generated for remote access when ``VFE_API_TOKEN`` is not set."""
        return self.data_dir / "api-token.txt"

    @property
    def remote_requested(self) -> bool:
        """Listening beyond the loopback was asked for (Tailscale, extra hosts or ``host``)."""
        return self.tailscale or bool(self.extra_hosts) or not is_loopback(self.host)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.artifacts_dir,
            self.models_dir,
            self.logs_dir,
            self.backups_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings (tests build their own ``Settings`` instead)."""
    return Settings()
