"""Relink offline videos to their files at a new place (job ``relink_videos``), as DaVinci
Resolve's « Relink Clips »: the chosen folder, sub-folders included, is searched for a file of
the same size and the same content (fingerprint), the same name first; each video found moves
there with all its analyses. A file in no folder of the library joins it alone, through a root of
chosen files. Nothing is written in the folder searched.

The library's scans already relink an offline video whose file shows up in one of its folders;
this is for files moved elsewhere (another disk, a folder not declared).
"""

from __future__ import annotations

import os
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import NotFoundError, PathNotAllowedError
from vfe_vision.core.logging import get_logger
from vfe_vision.core.paths import is_video_file, path_key
from vfe_vision.db.models import Keyframe, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.roots import ensure_files_root, unlist
from vfe_vision.jobs.scan import fingerprint
from vfe_vision.pipeline.runner import EventSink
from vfe_vision.pipeline.stage import ProgressFn

log = get_logger(__name__)

MAX_FILES = 50_000  # files looked at in the folder, at most (a whole disk is not searched)
FOLDER_GONE = "Le dossier choisi n'existe plus ou n'est pas accessible : {folder}"


@dataclass(slots=True)
class RelinkReport:
    linked: int = 0
    missing: int = 0  # no file of the same content in the folder
    conflicts: int = 0  # the file found is already a video of the library, with its analyses
    skipped: int = 0  # no longer offline (relinked meanwhile, or taken out of the library)
    truncated: bool = False  # the folder holds more than MAX_FILES files

    @property
    def message(self) -> str:
        parts = [
            f"{self.linked} {'vidéo reliée' if self.linked <= 1 else 'vidéos reliées'}",
            f"{self.missing} {'introuvable' if self.missing <= 1 else 'introuvables'}",
        ]
        if self.conflicts:
            parts.append(f"{self.conflicts} déjà dans la bibliothèque sous ce chemin")
        if self.truncated:
            parts.append(f"recherche limitée aux {MAX_FILES} premiers fichiers")
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class _Wanted:
    id: str
    filename: str
    size: int
    fingerprint: str


def relink_videos(
    db: Database,
    payload: Mapping[str, Any],
    *,
    data_dir: Path,
    events: EventSink,
    job_id: str | None = None,
    cancel: CancelToken | None = None,
    progress: ProgressFn | None = None,
) -> RelinkReport:
    """Look for each offline video of ``payload["video_ids"]`` in ``payload["folder"]``."""
    folder = Path(str(payload.get("folder") or ""))
    if not folder.is_dir():
        raise NotFoundError(FOLDER_GONE.format(folder=folder))
    ids: list[str] = list(dict.fromkeys(payload.get("video_ids") or ()))
    with db.read() as session:
        wanted = [
            _Wanted(row.id, row.filename, row.size_bytes, row.fingerprint)
            for row in session.execute(
                sa.select(Video.id, Video.filename, Video.size_bytes, Video.fingerprint).where(
                    Video.id.in_(ids), Video.status == VideoStatus.OFFLINE
                )
            )
        ]
    report = RelinkReport(skipped=len(ids) - len(wanted))
    if not wanted:
        return report
    if progress is not None:
        progress(0.0, f"Recherche dans {folder}")
    by_size, report.truncated = _index(folder, {w.size for w in wanted if w.size > 0}, cancel)
    digests: dict[Path, str] = {}
    taken: set[str] = set()
    for index, video in enumerate(wanted):
        if cancel is not None:
            cancel.raise_if_cancelled()
        if progress is not None:
            progress(index / len(wanted), f"{index}/{len(wanted)} vidéos cherchées")
        found = _find(video, by_size.get(video.size, ()), digests, taken)
        if found is None:
            report.missing += 1
            continue
        outcome = _move(db, video.id, found, data_dir)
        if outcome == "linked":
            taken.add(path_key(found))
            report.linked += 1
        elif outcome == "conflict":
            report.conflicts += 1
        else:
            report.skipped += 1
    if report.linked:
        events.emit("library.scanned", job_id=job_id, data={"relinked": report.linked})
    if progress is not None:
        progress(1.0, report.message)
    return report


def _index(
    folder: Path, sizes: Collection[int], cancel: CancelToken | None
) -> tuple[dict[int, list[Path]], bool]:
    """The video files of the folder whose size is one of ``sizes``, by size (and whether the
    folder held more files than were looked at)."""
    by_size: dict[int, list[Path]] = {}
    for count, path in enumerate(_video_files(folder)):
        if count >= MAX_FILES:
            return by_size, True
        if cancel is not None and count % 500 == 0:
            cancel.raise_if_cancelled()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size in sizes:
            by_size.setdefault(size, []).append(path)
    return by_size, False


def _video_files(folder: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(folder):
        # Hidden and system folders are left aside, as by the scans (".thumbnails", "$RECYCLE.BIN").
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
        for name in filenames:
            path = Path(dirpath) / name
            if is_video_file(path):
                yield path


def _find(
    video: _Wanted, candidates: Collection[Path], digests: dict[Path, str], taken: set[str]
) -> Path | None:
    """The candidate with the video's content, the one of the same name tried first."""
    name = video.filename.casefold()
    for path in sorted(candidates, key=lambda p: (p.name.casefold() != name, str(p))):
        if path_key(path) in taken:
            continue
        digest = digests.get(path)
        if digest is None:
            try:
                digest = digests[path] = fingerprint(path, video.size)
            except OSError:
                continue
        if digest == video.fingerprint:
            return path
    return None


def _move(
    db: Database, video_id: str, path: Path, data_dir: Path
) -> Literal["linked", "conflict", "skipped"]:
    """Move the video to its file found at ``path``, in the root covering it (or a root of
    chosen files). A video registered there anew by a scan, never analysed, gives way."""
    try:
        stat = path.stat()
    except OSError:
        return "skipped"
    with db.write() as session:
        video = session.get(Video, video_id)
        if video is None or video.status != VideoStatus.OFFLINE:
            return "skipped"
        key = path_key(path)
        other = session.execute(sa.select(Video).where(Video.path_key == key)).scalar_one_or_none()
        if other is not None and other.id != video.id:
            if _analysed(session, other):
                return "conflict"
            session.delete(other)
            session.flush()
        try:
            root = ensure_files_root(
                session, path.parent, path.name, auto_analyze=False, analysis_focus=None,
                data_dir=data_dir,
            )  # fmt: skip
        except PathNotAllowedError:
            return "conflict"
        session.flush()
        old_root, old_name = video.root_id, video.filename
        video.root_id = root.id
        video.path = str(path)
        video.path_key = key
        video.rel_path = _relative(path, Path(root.path))
        video.filename = path.name
        video.mtime = stat.st_mtime
        queue.restore_status(session, [video])
        session.flush()
        if old_root != root.id:
            unlist(session, old_root, old_name)
    log.info("video relinked", video_id=video_id, path=str(path))
    return "linked"


def _analysed(session: Session, video: Video) -> bool:
    if video.last_analyzed_at is not None:
        return True
    frames = session.scalar(sa.select(sa.func.count()).where(Keyframe.video_id == video.id))
    return bool(frames)


def _relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:  # the same folder spelled otherwise (letter case)
        return Path(os.path.relpath(path, base)).as_posix()
