"""Requesting analyses (the worker executes them).

An analysis request never redoes finished work unless asked to: the default
``complete`` mode only runs what is missing, failed, degraded, or whose video data changed.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa

from vfe_vision.core.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PathNotAllowedError,
    VfeError,
)
from vfe_vision.core.paths import EXAMPLE_FOLDER, is_video_file
from vfe_vision.db.models import (
    FrameAnalysis,
    Job,
    Keyframe,
    LibraryRoot,
    StageRun,
    Video,
    VideoSynthesis,
)
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.enums import AnalysisMode, JobKind, JobStatus, VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.queue import NEEDS_WORK
from vfe_vision.jobs.roots import files_root_at, files_roots_under, folder_root
from vfe_vision.jobs.scan import register_file
from vfe_vision.pipeline.stage import Stage
from vfe_vision.pipeline.stages import default_registry
from vfe_vision.services import videos
from vfe_vision.services.container import AppContainer
from vfe_vision.services.library import add_root, root_for_path

# Folder settings that are inputs of the analysis (capture time, default position): changing
# them makes the stages that read them run again at the next ordinary analysis.
ROOT_ANALYSIS_INPUTS = frozenset(
    {"clock_offset_s", "default_timezone", "default_latitude", "default_longitude"}
)
# Stages computed from those inputs: their results are dropped when the inputs change, so no
# earlier result can be kept by mistake (e.g. one recorded before input keys existed).
ROOT_INPUT_STAGES = ("metadata", "place", "weather", "sun")
# The translation of the analyses already made waits for the analyses asked for.
TRANSLATION_PRIORITY = 150


def list_stages() -> list[Stage]:
    """Every analysis stage, in execution order."""
    return default_registry().plan()


def root_inputs_differ(before: object, patch: dict[str, object]) -> bool:
    """Whether a folder update changes an analysis input (no offset and 0 s are the same)."""
    for name in ROOT_ANALYSIS_INPUTS & patch.keys():
        old, new = getattr(before, name), patch[name]
        if name == "clock_offset_s":
            old, new = old or 0, new or 0
        if old != new:
            return True
    return False


def request_analysis(
    c: AppContainer,
    video_id: str,
    *,
    stages: list[str] | None = None,
    mode: AnalysisMode = AnalysisMode.COMPLETE,
    focus: str | None = None,
    priority: int = 100,
) -> Job:
    """Queue an analysis of ``stages`` (default: all) in ``mode``.

    With stages named, ``update`` and ``full`` only concern those stages; their dependencies
    are completed. When the job runs, the stages reading their results are brought up to date
    too (see ``PipelineRunner.run``). A focus is a request to redo the stages that use it
    (when it changed).
    """
    with c.db.read() as session:
        if session.get(Video, video_id) is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
    registry = default_registry()
    for name in stages or []:
        registry.get(name)
    scope: queue.Scope = sorted(set(stages)) if stages else True
    refresh: queue.Scope = scope if mode == AnalysisMode.UPDATE else False
    focus = (focus or "").strip()
    if focus and focus != _described_with_focus(c, video_id):
        uses_focus = [stage.name for stage in registry.plan(stages) if stage.uses_focus]
        refresh = queue.union_scope(refresh, uses_focus)
    payload: dict[str, object] = {
        "mode": mode.value,
        "force": scope if mode == AnalysisMode.FULL else False,
        "refresh": refresh,
    }
    if stages:
        payload["stages"] = scope
    if focus:
        payload["focus"] = focus
    return queue.enqueue(
        c.db, JobKind.ANALYZE_VIDEO, video_id=video_id, payload=payload, priority=priority
    )


def _described_with_focus(c: AppContainer, video_id: str) -> str | None:
    """The focus the current frame descriptions were made with (None: none, or no description)."""
    with c.db.read() as session:
        return session.execute(
            sa.select(FrameAnalysis.focus)
            .join(Keyframe, FrameAnalysis.keyframe_id == Keyframe.id)
            .where(Keyframe.video_id == video_id)
            .order_by(FrameAnalysis.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()


def analyze_root(
    c: AppContainer, root_id: str, *, mode: AnalysisMode = AnalysisMode.COMPLETE
) -> int:
    """Queue the folder's videos; returns how many were queued.

    ``complete`` only queues videos with something to do (never analysed, failed, incomplete,
    stages added since their analysis) or a skipped stage that may now run (e.g. online
    services back on); the other modes queue every video found.
    """
    queued = 0
    every_stage = set(default_registry().names)
    for video_id, status in _root_videos(c, root_id):
        if mode == AnalysisMode.COMPLETE and not _has_work(c, video_id, status, every_stage):
            continue
        request_analysis(c, video_id, mode=mode)
        queued += 1
    return queued


@dataclass(frozen=True, slots=True)
class BatchAnalysis:
    queued: int = 0
    up_to_date: int = 0  # « complete »: nothing to do for the chosen stages
    offline: int = 0  # file out of reach: not queued
    unknown: tuple[str, ...] = ()  # ids of videos removed since they were picked


def analyze_videos(
    c: AppContainer,
    video_ids: list[str],
    *,
    stages: list[str] | None = None,
    mode: AnalysisMode = AnalysisMode.COMPLETE,
    priority: int = 100,
) -> BatchAnalysis:
    """Queue the chosen videos for the chosen stages (default: all), as ``request_analysis``.

    ``complete`` leaves out the videos with nothing to do among those stages and their
    dependencies; offline videos are never queued, and ids of removed videos are reported.
    Unknown stages reject the whole request.
    """
    planned = {stage.name for stage in default_registry().plan(stages)}  # checks the names
    wanted = list(dict.fromkeys(video_ids))
    with c.db.read() as session:
        statuses: dict[str, VideoStatus] = {
            row.id: row.status
            for row in session.execute(
                sa.select(Video.id, Video.status).where(Video.id.in_(wanted))
            )
        }
    queued = up_to_date = offline = 0
    for video_id in wanted:
        status = statuses.get(video_id)
        if status is None:
            continue
        if status == VideoStatus.OFFLINE:
            offline += 1
        elif mode == AnalysisMode.COMPLETE and not _has_work(c, video_id, status, planned):
            up_to_date += 1
        else:
            request_analysis(c, video_id, stages=stages, mode=mode, priority=priority)
            queued += 1
    unknown = tuple(video_id for video_id in wanted if video_id not in statuses)
    return BatchAnalysis(queued=queued, up_to_date=up_to_date, offline=offline, unknown=unknown)


def translate_library(c: AppContainer) -> BatchAnalysis:
    """Queue the translation of every video whose analyses hold texts; those already
    translated are left out, as in « Complete »."""
    with c.db.read() as session:
        described = (
            sa.select(Keyframe.video_id)
            .join(FrameAnalysis, FrameAnalysis.keyframe_id == Keyframe.id)
            .distinct()
        )
        ids = set(session.execute(described).scalars())
        ids |= set(session.execute(sa.select(VideoSynthesis.video_id)).scalars())
        ordered = list(
            session.execute(
                sa.select(Video.id).where(Video.id.in_(ids)).order_by(Video.created_at)
            ).scalars()
        )
    return analyze_videos(c, ordered, stages=["translation"], priority=TRANSLATION_PRIORITY)


def _has_work(c: AppContainer, video_id: str, status: VideoStatus, planned: set[str]) -> bool:
    """Whether a « complete » analysis of ``planned`` stages would do anything: one is missing,
    failed or skipped (it may run now, e.g. online services back on), or the video itself
    was never fully analysed (whole analyses only)."""
    gaps = videos.gaps_for(c, video_id)
    if planned & {*gaps.missing, *gaps.skipped}:
        return True
    return planned == set(default_registry().names) and status in NEEDS_WORK


TRANSCRIPT_CHOICES = ("transcript_mode", "transcript_language")


def transcript_choice_changed(c: AppContainer, video_id: str) -> Job:
    """The user changed how this video is transcribed: its transcription is redone now (and
    erased for « never »), as an explicit correction of the video's data."""
    with c.db.write() as session:
        session.execute(
            sa.update(StageRun)
            .where(StageRun.video_id == video_id, StageRun.stage == "transcript")
            .values(cache_key="")
        )
    return request_analysis(c, video_id, stages=["transcript"])


def root_inputs_changed(c: AppContainer, root_id: str) -> int:
    """After a change of the folder's analysis inputs, bring its analysed videos up to date
    (only the stages reading those inputs run again). Returns how many were queued."""
    analysed = [
        video_id
        for video_id, status in _root_videos(c, root_id)
        if status != VideoStatus.NEW  # never analysed: left to the user or to the scan
    ]
    with c.db.write() as session:
        session.execute(
            sa.update(StageRun)
            .where(StageRun.video_id.in_(analysed), StageRun.stage.in_(ROOT_INPUT_STAGES))
            .values(cache_key="")
        )
    for video_id in analysed:
        request_analysis(c, video_id)
    return len(analysed)


def _root_videos(c: AppContainer, root_id: str) -> list[tuple[str, VideoStatus]]:
    with c.db.read() as session:
        if session.get(LibraryRoot, root_id) is None:
            raise NotFoundError(f"Dossier introuvable : {root_id}")
        rows = session.execute(
            sa.select(Video.id, Video.status)
            .where(Video.root_id == root_id, Video.status != VideoStatus.OFFLINE)
            .order_by(Video.rel_path)
        ).all()
    return [(row.id, row.status) for row in rows]


def analyze_path(
    c: AppContainer, path: str, *, focus: str | None = None, priority: int = 50
) -> tuple[str, Job | None]:
    """Register a file located in a declared folder and make sure it is analysed.

    Returns the active or newly queued job, or ``None`` when nothing is left to do (MCP entry
    point: an analysed video is answered immediately; stages added since are completed).
    """
    file = Path(path).expanduser()
    root = root_for_path(c, file)  # on the text first: a path outside the library is never read
    if root is None:
        raise PathNotAllowedError(
            "Ce fichier n'est dans aucun dossier de la bibliothèque. Ajoutez d'abord son dossier "
            "dans l'application (Bibliothèque → Ajouter un dossier)."
        )
    if not file.is_file() or not is_video_file(file):
        raise InvalidInputError(f"Ce n'est pas un fichier vidéo accessible : {file}")
    video_id = register_file(c.db, root.id, file)
    with c.db.read() as session:
        status = session.get_one(Video, video_id).status
        active = session.execute(
            sa.select(Job)
            .where(Job.video_id == video_id, Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)))
            .limit(1)
        ).scalar_one_or_none()
    if active is not None:
        return video_id, active
    if status in NEEDS_WORK or focus or videos.gaps_for(c, video_id).missing:
        return video_id, request_analysis(c, video_id, focus=focus, priority=priority)
    return video_id, None


MAX_FOLDER_VIDEOS = 500
NOT_IN_LIBRARY = (
    "Ce dossier n'est dans aucun dossier de la bibliothèque. Ajoutez-le dans l'application "
    "(Bibliothèque → Ajouter un dossier), ou autorisez Claude à ajouter des dossiers (page "
    "Système, « Accès de Claude »)."
)
HOLDS_TIMELINE_FILES = (
    "Ce dossier contient des vidéos ajoutées depuis une timeline : ajoutez-le depuis "
    "l'application (Ajouter un dossier)."
)


@dataclass(frozen=True, slots=True)
class FolderAnalysis:
    folder: str
    root_id: str
    root_added: bool = False  # a new library folder (the preference allowed it)
    found: int = 0  # video files under the folder (at most MAX_FOLDER_VIDEOS looked at)
    queued: int = 0
    up_to_date: int = 0
    truncated: bool = False
    job_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scan_job_id: str | None = None


def analyze_folder(
    c: AppContainer, path: str, *, recursive: bool = True, focus: str | None = None
) -> FolderAnalysis:
    """Analyse the videos under a folder of the library (MCP ``analyze_folder``).

    Each file is registered and analysed like ``watch_video`` does (only what is missing). A
    folder outside the library is refused, unless the user allowed Claude to add folders
    (``mcp_add_folders``): it then becomes a library folder, scanned and analysed by the worker.
    The folder of files chosen from a Resolve timeline analyses those files only; a folder
    outside the library that holds some is refused (it is added from the app).
    """
    folder = Path(path).expanduser()
    if not folder.is_absolute():
        raise InvalidInputError(f"Indiquez un chemin absolu, par exemple {EXAMPLE_FOLDER}.")
    missing = f"Ce dossier n'existe pas ou n'est pas accessible : {folder}"
    with c.db.read() as session:
        chosen = files_root_at(session, folder)
        root = chosen or folder_root(session.execute(sa.select(LibraryRoot)).scalars(), folder)
        inner = files_roots_under(session, folder)
    if root is None:
        if inner:  # adding it would take those videos in: the user decides, in the app
            raise ConflictError(HOLDS_TIMELINE_FILES)
        if not load_preferences(c.db).mcp_add_folders:
            raise PathNotAllowedError(NOT_IN_LIBRARY)  # decided before the folder is looked at
        if not folder.is_dir():
            raise InvalidInputError(missing)
        added, scan = add_root(c, str(folder), recursive=recursive, analysis_focus=focus)
        return FolderAnalysis(str(folder), added.id, root_added=True, scan_job_id=scan.id)
    if not folder.is_dir():
        raise InvalidInputError(missing)
    if chosen is not None:  # a folder of chosen files: those files only
        files = sorted(f for f in (folder / name for name in chosen.files or ()) if f.is_file())
    else:
        files = _folder_videos(
            folder, Path(root.path), recursive=recursive, exclude=root.exclude_globs
        )
    queued = up_to_date = 0
    job_ids: list[str] = []
    errors: list[str] = []
    for file in files[:MAX_FOLDER_VIDEOS]:
        try:
            _video_id, job = analyze_path(c, str(file), focus=focus)
        except (VfeError, OSError) as exc:
            errors.append(f"{file.name} : {getattr(exc, 'detail', None) or exc}")
            continue
        if job is None:
            up_to_date += 1
        else:
            queued += 1
            job_ids.append(job.id)
    return FolderAnalysis(
        str(folder), root.id, found=len(files), queued=queued, up_to_date=up_to_date,
        truncated=len(files) > MAX_FOLDER_VIDEOS, job_ids=job_ids, errors=errors,
    )  # fmt: skip


def _folder_videos(folder: Path, base: Path, *, recursive: bool, exclude: list[str]) -> list[Path]:
    """Video files under ``folder`` (hidden and system folders skipped, the root's exclusions
    applied to paths relative to the root), in name order."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "$")))
        for name in sorted(filenames):
            file = Path(dirpath) / name
            try:
                rel = file.relative_to(base).as_posix()
            except ValueError:
                rel = name
            if is_video_file(file) and not any(fnmatch.fnmatch(rel, g) for g in exclude):
                found.append(file)
        if not recursive:
            break
    return found
