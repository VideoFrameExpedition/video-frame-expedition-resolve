"""Read the project open in DaVinci Resolve through its external scripting API.

Resolve's scripting library (``fusionscript.dll``) is loaded only in a short-lived child
process (``timeline_reader.py``): the application's own processes never host it, a crash or
a hang of Resolve's side ends with the child. Reading only; calls are serialised (Resolve
answers one script at a time) and bounded in time: the child ends itself at its deadline, and
its Job Object is closed after each call, so no reader outlives its request.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from vfe_vision.core.config import Settings
from vfe_vision.core.errors import (
    ConflictError,
    ExternalToolError,
    NotFoundError,
    ResolveUnavailableError,
)
from vfe_vision.core.procs import KillOnCloseJob, run_process
from vfe_vision.domain.resolve_timeline import (
    ClipKind,
    ResolveDatabase,
    ResolveProjectInfo,
    ResolveProjectRef,
    TimelineClip,
    TimelineContent,
    TimelineInfo,
    timeline_from_json,
    use_from_json,
)

DEFAULT_LIBRARY = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
CHILD_SCRIPT = Path(__file__).with_name("timeline_reader.py")
PROJECT_TIMEOUT_S = 30.0
TIMELINE_TIMEOUT_S = 120.0
LOCK_WAIT_S = 5.0
KILL_GRACE_S = 5.0  # the child's own deadline comes first; the parent kills it after this
TOOL = "DaVinci Resolve"

NOT_INSTALLED = (
    "DaVinci Resolve n'est pas installé sur cet ordinateur (bibliothèque de scripts introuvable)."
)
NOT_RUNNING = "DaVinci Resolve n'est pas lancé. Ouvrez votre projet dans Resolve, puis réessayez."
SCRIPTING_OFF = (
    "DaVinci Resolve est lancé mais refuse la connexion. Dans Resolve Studio : Préférences › "
    "Système › Général › « Script externe » (External scripting using) sur « Local ». La version "
    "gratuite de Resolve n'accepte pas les scripts externes. Si Resolve est lancé en "
    "administrateur, relancez-le normalement."
)
STARTING = (
    "DaVinci Resolve est en train de démarrer ou de charger un projet. Réessayez dans un instant."
)
NOT_RESPONDING = (
    "DaVinci Resolve ne répond pas : une fenêtre est peut-être ouverte dans Resolve, ou un projet "
    "se charge. Réessayez dans un instant."
)
BUSY = "Une lecture de DaVinci Resolve est déjà en cours, réessayez dans un instant."
READING = "La lecture de DaVinci Resolve"
# Resolve on another computer: its scripting server listens there on this port.
SCRIPT_PORT = 1144
REACH_TIMEOUT_S = 2.0
# Its firewall must let in the scripting server (fuscript, port 1144) and Resolve itself (a port
# it picks at each start); on Windows, the rules Resolve installs hold for private networks only.
NOT_REACHABLE = (
    "DaVinci Resolve est réglé sur l'ordinateur « {host} », qui ne répond pas : vérifiez qu'il "
    "est allumé et joignable (même réseau ou Tailscale), que Resolve y est lancé et que son "
    "pare-feu laisse passer le port 1144 (sous Windows, les règles que Resolve installe ne "
    "valent que pour un réseau « privé »)."
)
REMOTE_REFUSED = (
    "DaVinci Resolve sur « {host} » refuse la connexion : dans Resolve Studio sur cet "
    "ordinateur, Préférences › Système › Général › « Script externe » (External scripting "
    "using) sur « Réseau » (Network), puis relancez Resolve. Si c'est déjà le cas, son pare-feu "
    "doit laisser entrer aussi DaVinci Resolve lui-même, pas seulement le port 1144 (sous "
    "Windows, les règles que Resolve installe ne valent que pour un réseau « privé »)."
)
NOT_INSTALLED_HERE = (
    "Pour lire le DaVinci Resolve de « {host} », cet ordinateur a besoin de la bibliothèque de "
    "scripts de Resolve : gardez DaVinci Resolve installé ici (il n'a pas besoin d'être lancé)."
)
NO_PROJECT = "Aucun projet n'est ouvert dans DaVinci Resolve."
NO_TIMELINE = "Ce projet n'a pas de timeline ouverte dans DaVinci Resolve."
TIMELINE_GONE = "Cette timeline n'est plus dans le projet ouvert dans DaVinci Resolve."
CHANGED = "La timeline a changé pendant sa lecture dans DaVinci Resolve. Réessayez."

_calls = threading.Lock()  # one conversation with Resolve at a time


def library_path(settings: Settings) -> Path:
    """Where Resolve's scripting library is: the setting, then Resolve's own variable, then the
    default installation."""
    return Path(
        settings.resolve_script_lib or os.environ.get("RESOLVE_SCRIPT_LIB") or DEFAULT_LIBRARY
    )


class ResolveReader:
    """The :class:`~vfe_vision.ports.resolve.ResolveSource` of the running Resolve."""

    def __init__(self, settings: Settings, host: Callable[[], str | None] | None = None) -> None:
        self._settings = settings
        self._host = host  # the computer Resolve runs on; None or "": this one

    def project(self) -> ResolveProjectInfo:
        data = self._run(["project"], timeout_s=PROJECT_TIMEOUT_S)
        return project_from_json(data)

    def timeline(self, timeline_id: str | None) -> TimelineContent:
        data = self._run(["timeline", timeline_id or "-"], timeout_s=TIMELINE_TIMEOUT_S)
        return timeline_content_from_json(data)

    def _run(self, command: list[str], *, timeout_s: float) -> dict[str, Any]:
        return run_child(self._settings, self._host, CHILD_SCRIPT, command, timeout_s=timeout_s)


def run_child(
    settings: Settings,
    host_of: Callable[[], str | None] | None,
    script: Path,
    command: list[str],
    *,
    timeout_s: float,
    input_bytes: bytes | None = None,
    doing: str = READING,
    errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One conversation with Resolve through a child ``script`` (the reader's, or the timeline
    builder's): its answer, or the error it stands for (``errors``: messages of the
    script's own error codes, ``{host}`` filled in; ``doing`` names the work in the others)."""
    host = ((host_of() if host_of else None) or "").strip()
    library = library_path(settings)
    if not library.is_file():
        message = NOT_INSTALLED_HERE.format(host=host) if host else NOT_INSTALLED
        raise ResolveUnavailableError(message, reason="not_installed")
    if host and not reachable(host):
        raise ResolveUnavailableError(NOT_REACHABLE.format(host=host), reason="not_reachable")
    if not _calls.acquire(timeout=LOCK_WAIT_S):
        raise ResolveUnavailableError(BUSY, reason="busy")
    job = KillOnCloseJob()  # closed below: the whole child tree ends with the request
    try:
        result = run_process(
            [sys.executable, "-I", str(script), str(library), *command],
            timeout_s=timeout_s + KILL_GRACE_S,
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "VFE_RESOLVE_DEADLINE_S": str(timeout_s),
                "VFE_RESOLVE_HOST": host,
            },
            input_bytes=input_bytes,
            check=False,
            tool_name=TOOL,
            job=job,
        )
    except ExternalToolError as exc:  # killed at the parent's deadline
        raise ResolveUnavailableError(NOT_RESPONDING, reason="not_responding") from exc
    finally:
        job.close()
        _calls.release()
    if result.returncode != 0 and not result.stdout.strip():
        if "Timeout" in result.stderr_text:  # the child's watchdog ended it
            raise ResolveUnavailableError(NOT_RESPONDING, reason="not_responding")
        tail = " | ".join(result.stderr_text.strip().splitlines()[-4:])
        raise ExternalToolError(
            f"{doing} s'est arrêtée (code {result.returncode}) : {tail}", tool=TOOL
        )
    return answer_of(
        result.stdout_text, resolve_running=resolve_running, host=host, doing=doing, errors=errors
    )


def answer_of(
    stdout: str,
    *,
    resolve_running: Any,
    host: str = "",
    doing: str = READING,
    errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The child's answer, or the error it stands for. ``resolve_running`` (a callable → bool |
    None) tells a Resolve that is not running from one that refuses scripts; ``host``: Resolve
    on that computer; ``errors``: messages of the script's own error codes."""
    try:
        data = json.loads(stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise ExternalToolError(
            f"Réponse illisible de DaVinci Resolve : {stdout.strip()[:200]}", tool=TOOL
        ) from exc
    if not isinstance(data, dict):
        raise ExternalToolError("Réponse inattendue de DaVinci Resolve.", tool=TOOL)
    if data.get("ok"):
        return data
    error = str(data.get("error") or "")
    message = str(data.get("message") or "")
    unavailable = {
        "not_installed": NOT_INSTALLED,
        "starting": STARTING,
        "no_project": NO_PROJECT,
        "no_timeline": NO_TIMELINE,
    }
    if error in unavailable:
        raise ResolveUnavailableError(unavailable[error], reason=error)
    if error == "no_connection":
        if host:
            raise ResolveUnavailableError(REMOTE_REFUSED.format(host=host), reason="scripting_off")
        if resolve_running() is False:
            raise ResolveUnavailableError(NOT_RUNNING, reason="not_running")
        raise ResolveUnavailableError(SCRIPTING_OFF, reason="scripting_off")
    if error == "timeline_not_found":
        raise NotFoundError(TIMELINE_GONE)
    if error == "changed_during_read":
        raise ConflictError(CHANGED)
    if errors and error in errors:  # the script's own words kept for the log
        raise ExternalToolError(errors[error].format(host=host), tool=TOOL, cause=message[:500])
    raise ExternalToolError(f"{doing} a échoué : {message[:300] or error}", tool=TOOL)


def reachable(host: str) -> bool:
    """Whether Resolve's scripting server answers on ``host`` (a quick check: an absent computer
    would hold the scripting library for about 40 s)."""
    try:
        with socket.create_connection((host.strip("[]"), SCRIPT_PORT), timeout=REACH_TIMEOUT_S):
            return True
    except OSError:
        return False


def resolve_running() -> bool | None:
    """Whether a Resolve process runs on this computer (None when it cannot be told)."""
    if sys.platform != "win32":
        return None
    try:
        result = run_process(
            ["tasklist", "/FI", "IMAGENAME eq Resolve.exe", "/FO", "CSV", "/NH"],
            timeout_s=10,
            check=False,
            tool_name="tasklist",
        )
    except ExternalToolError:
        return None
    return "resolve.exe" in result.stdout_text.casefold()


# ---------------------------------------------------------------- read once, import after
class TimelineSnapshots:
    """Timelines read for a preview, kept a little while so that importing right after reuses
    the read instead of asking Resolve again (thread-safe, a few entries)."""

    def __init__(self, *, ttl_s: float = 120.0, size: int = 4) -> None:
        self._ttl_s = ttl_s
        self._size = size
        self._items: OrderedDict[str, tuple[float, TimelineContent]] = OrderedDict()
        self._lock = threading.Lock()

    def remember(self, content: TimelineContent) -> str:
        snapshot_id = secrets.token_urlsafe(12)
        with self._lock:
            self._items[snapshot_id] = (time.monotonic(), content)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return snapshot_id

    def recall(self, snapshot_id: str) -> TimelineContent | None:
        with self._lock:
            found = self._items.get(snapshot_id)
            if found is None:
                return None
            stored_at, content = found
            if time.monotonic() - stored_at > self._ttl_s:
                del self._items[snapshot_id]
                return None
            return content


# ---------------------------------------------------------------- the child's JSON
_KINDS = {ClipKind.FILE, ClipKind.GRAPHICS, ClipKind.CONTAINER}


def _database(data: dict[str, Any]) -> ResolveDatabase:
    raw = data.get("database") or {}
    return ResolveDatabase(type=str(raw.get("type") or ""), name=str(raw.get("name") or ""))


def _project(data: dict[str, Any]) -> ResolveProjectRef:
    raw = data.get("project") or {}
    return ResolveProjectRef(id=str(raw.get("id") or ""), name=str(raw.get("name") or ""))


def _timeline(raw: dict[str, Any]) -> TimelineInfo:
    info = timeline_from_json(raw)
    if not info.id:
        raise ExternalToolError("DaVinci Resolve a donné une timeline sans identifiant.", tool=TOOL)
    return replace(info, is_current=bool(raw.get("is_current")))


def _clip(raw: dict[str, Any]) -> TimelineClip:
    kind = str(raw.get("kind") or ClipKind.FILE)
    return TimelineClip(
        file_path=str(raw.get("file_path") or "") or None,
        clip_type=str(raw.get("clip_type") or "") or None,
        use=use_from_json(raw.get("use") or {}),
        kind=kind if kind in _KINDS else ClipKind.FILE,
        vfe_video_id=str(raw.get("vfe_video_id") or "") or None,
    )


def project_from_json(data: dict[str, Any]) -> ResolveProjectInfo:
    project = _project(data)
    if not project.id:
        raise ExternalToolError("DaVinci Resolve a donné un projet sans identifiant.", tool=TOOL)
    return ResolveProjectInfo(
        product=str(data.get("product") or "DaVinci Resolve"),
        version=str(data.get("version") or ""),
        database=_database(data),
        project=project,
        current_timeline_id=str(data.get("current_timeline_id") or "") or None,
        timelines=tuple(_timeline(t) for t in data.get("timelines") or [] if isinstance(t, dict)),
        studio=bool(data.get("studio", True)),
    )


def timeline_content_from_json(data: dict[str, Any]) -> TimelineContent:
    project = _project(data)
    raw_timeline = data.get("timeline")
    if not project.id or not isinstance(raw_timeline, dict):
        raise ExternalToolError("Réponse incomplète de la lecture de DaVinci Resolve.", tool=TOOL)
    return TimelineContent(
        product=str(data.get("product") or "DaVinci Resolve"),
        version=str(data.get("version") or ""),
        database=_database(data),
        project=project,
        timeline=_timeline(raw_timeline),
        clips=tuple(_clip(raw) for raw in data.get("clips") or [] if isinstance(raw, dict)),
        studio=bool(data.get("studio", True)),
        errors=int(data.get("errors") or 0),
    )
