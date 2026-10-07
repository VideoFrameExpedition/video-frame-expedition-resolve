"""Library scan: discover videos in a root folder (read-only), relink moved files, mark offline.

A root of chosen files is scanned for its listed files only."""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import sqlalchemy as sa
import xxhash

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import ConflictError, NotFoundError
from vfe_vision.core.logging import get_logger
from vfe_vision.core.paths import is_cloud_placeholder, is_video_file, path_key, volume_serial
from vfe_vision.db.models import Keyframe, LibraryRoot, StageRun, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, RootKind, VideoStatus
from vfe_vision.jobs import queue

log = get_logger(__name__)

CHUNK = 4 * 1024 * 1024
RECENT_WINDOW_S = 30.0  # a file created/modified this recently may still be copying
SETTLE_S = 1.0

# One scan of a root at a time in this process (« Rescan » and the automatic pass).
_root_locks: dict[str, threading.Lock] = {}
_root_locks_guard = threading.Lock()


@dataclass(slots=True)
class ScanReport:
    found: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    moved: int = 0
    offline: int = 0
    skipped_cloud: int = 0
    skipped_in_progress: int = 0
    skipped_empty: int = 0  # 0 bytes: a file being created, or left empty by a camera app
    queued: int = 0
    errors: list[str] = field(default_factory=list)
    other_volume: bool = False  # automatic pass: another disk at the root's path, left alone

    @property
    def changes(self) -> bool:
        """Something the library shows changed (new, modified, moved or gone)."""
        return bool(self.new or self.changed or self.moved or self.offline)


def fingerprint(path: Path, size: int) -> str:
    """xxh3-128 of the size and three 4 MiB samples (start, middle, end): fast and stable."""
    digest = xxhash.xxh3_128()
    digest.update(size.to_bytes(8, "little"))
    with path.open("rb") as handle:
        for offset in sorted({0, max(0, size // 2 - CHUNK // 2), max(0, size - CHUNK)}):
            handle.seek(offset)
            digest.update(handle.read(CHUNK))
    return digest.hexdigest()


def still_copying(path: Path, stat: os.stat_result, *, now: float) -> bool:
    """True if the file looks like it is still being written.

    A copy keeps the source's modification time but gets a fresh creation time (``st_ctime``
    on Windows, ``st_birthtime`` on macOS); recent files are re-checked after a short pause for
    a stable size.
    """
    created = float(getattr(stat, "st_birthtime", stat.st_ctime))
    if now - max(stat.st_mtime, stat.st_ctime, created) > RECENT_WINDOW_S:
        return False
    time.sleep(SETTLE_S)
    try:
        again = path.stat()
    except OSError:
        return True
    return (again.st_size, again.st_mtime) != (stat.st_size, stat.st_mtime)


def _iter_videos(base: Path, *, recursive: bool, exclude: list[str]) -> Iterator[Path]:
    walker = os.walk(base) if recursive else [(str(base), [], [p.name for p in base.iterdir()])]
    for dirpath, dirnames, filenames in walker:
        # Skip hidden and system folders (".thumbnails", "$RECYCLE.BIN"…).
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            if not is_video_file(path) or any(fnmatch.fnmatch(rel, g) for g in exclude):
                continue
            yield path


def _listed_videos(base: Path, names: list[str]) -> Iterator[Path]:
    """The listed files of a root of chosen files, spelled as on the disk."""
    listed = {name.casefold() for name in names}
    for path in base.iterdir():
        if path.name.casefold() in listed and is_video_file(path) and path.is_file():
            yield path


def _videos_of(root: _Root) -> list[Path]:
    if root.files is not None:
        return list(_listed_videos(root.base, root.files))
    return list(_iter_videos(root.base, recursive=root.recursive, exclude=root.exclude))


def scan_root(
    db: Database,
    root_id: str,
    *,
    cancel: CancelToken | None = None,
    progress: Callable[[float, str | None], None] | None = None,
    automatic: bool = False,
) -> ScanReport:
    """Look at the root's files (never writes there). ``automatic``: the worker's periodic pass,
    which gives way to a scan already running and leaves another disk alone."""
    with _root_locks_guard:
        lock = _root_locks.setdefault(root_id, threading.Lock())
    if not lock.acquire(blocking=not automatic):
        raise ConflictError("Ce dossier est déjà en cours de scan.")
    try:
        return _scan(db, root_id, cancel=cancel, progress=progress, automatic=automatic)
    finally:
        lock.release()


@dataclass(frozen=True, slots=True)
class _Known:
    video_id: str
    size: int
    mtime: float
    status: VideoStatus
    rel_path: str


@dataclass(frozen=True, slots=True)
class _Root:
    base: Path
    recursive: bool
    exclude: list[str]
    auto: bool
    serial: str | None
    known: dict[str, _Known]
    files: list[str] | None = None  # a root of chosen files: the names it lists


def _read_root(db: Database, root_id: str) -> _Root:
    with db.read() as session:
        root = session.get(LibraryRoot, root_id)
        if root is None:
            raise NotFoundError(f"Dossier de bibliothèque introuvable : {root_id}")
        videos = session.execute(sa.select(Video).where(Video.root_id == root_id)).scalars()
        return _Root(
            base=Path(root.path),
            recursive=root.recursive,
            exclude=list(root.exclude_globs),
            auto=root.auto_analyze,
            serial=root.volume_serial,
            known={
                v.path_key: _Known(v.id, v.size_bytes, v.mtime, v.status, v.rel_path)
                for v in videos
            },
            files=list(root.files or ()) if root.kind == RootKind.FILES else None,
        )


def _same_volume(db: Database, root_id: str, root: _Root, *, automatic: bool) -> bool:
    """False when another disk sits at the root's path, for an automatic pass: another card
    under the same letter (DCIM is on every camera card) is not this root, and this root's
    videos are not gone. « Rescan », asked for this disk, makes it the root's."""
    current = volume_serial(root.base)
    if not root.serial or not current or current == root.serial:
        return True
    if automatic:
        log.warning("automatic scan skipped: other volume", root_id=root_id, path=str(root.base))
        return False
    with db.write() as session:
        if (row := session.get(LibraryRoot, root_id)) is not None:
            row.volume_serial = current
    return True


def _scan(
    db: Database,
    root_id: str,
    *,
    cancel: CancelToken | None,
    progress: Callable[[float, str | None], None] | None,
    automatic: bool,
) -> ScanReport:
    root = _read_root(db, root_id)
    if not root.base.is_dir():
        raise NotFoundError(f"Le dossier n'existe plus ou n'est pas accessible : {root.base}")
    report = ScanReport()
    if not _same_volume(db, root_id, root, automatic=automatic):
        report.other_volume = True
        return report

    seen: set[str] = set()
    to_analyse: list[str] = []
    unknown: list[tuple[Path, os.stat_result]] = []
    now = time.time()
    files = _videos_of(root)
    for index, path in enumerate(files):
        if cancel is not None:
            cancel.raise_if_cancelled()
        if progress is not None and index % 10 == 0:
            progress(index / max(1, len(files)), f"{index}/{len(files)} fichiers examinés")
        report.found += 1
        key = path_key(path)
        seen.add(key)
        try:
            stat = path.stat()
            if _set_aside(path, stat, now=now, report=report):
                continue
            if (previous := root.known.get(key)) is None:
                unknown.append((path, stat))  # new, or moved here: decided once gone ones are known
            elif _look_again(db, root.base, path, stat, previous):
                report.changed += 1
                to_analyse.append(previous.video_id)
            else:
                report.unchanged += 1
        except OSError as exc:
            report.errors.append(f"{path.name} : {exc.strerror or exc}")

    # Files that left their place first: a clip moved to another bin of this root is then
    # relinked with its analyses, instead of being analysed again as a new one.
    gone = _mark_missing(db, root_id, seen)
    to_analyse += _place(
        db, root_id, root.base, unknown=unknown, gone=gone, report=report, cancel=cancel
    )
    report.offline = len(gone)
    with db.write() as session:
        row = session.get(LibraryRoot, root_id)
        if row is None:  # removed during the scan: its videos went with it
            return report
        row.last_scan_at = datetime.now(UTC)
    if root.auto:
        for video_id in to_analyse:
            queue.enqueue(db, JobKind.ANALYZE_VIDEO, video_id=video_id, payload={})
            report.queued += 1
    if progress is not None:
        progress(1.0, f"{report.found} vidéos trouvées")
    return report


def _set_aside(path: Path, stat: os.stat_result, *, now: float, report: ScanReport) -> bool:
    """Files left for a later scan: only in the cloud, empty, or still being copied."""
    if is_cloud_placeholder(path):
        report.skipped_cloud += 1
    elif stat.st_size == 0:
        report.skipped_empty += 1
    elif still_copying(path, stat, now=now):
        report.skipped_in_progress += 1
    else:
        return False
    return True


def _look_again(
    db: Database, base: Path, path: Path, stat: os.stat_result, previous: _Known
) -> bool:
    """Whether a known file changed (and is to be analysed again)."""
    if (
        previous.size == stat.st_size
        and previous.mtime == stat.st_mtime
        and previous.status != VideoStatus.OFFLINE
    ):
        if previous.rel_path != path.relative_to(base).as_posix():  # renamed by its case only
            _respell(db, previous.video_id, base, path)
        return False
    _update_existing(db, previous.video_id, path, stat, fingerprint(path, stat.st_size), base=base)
    return True


def _place(
    db: Database,
    root_id: str,
    base: Path,
    *,
    unknown: list[tuple[Path, os.stat_result]],
    gone: set[str],
    report: ScanReport,
    cancel: CancelToken | None,
) -> list[str]:
    """Relink or add the files not known at their path; return the new ones to analyse."""
    new: list[str] = []
    for path, stat in unknown:
        if cancel is not None:
            cancel.raise_if_cancelled()
        try:
            digest = fingerprint(path, stat.st_size)
        except OSError as exc:
            report.errors.append(f"{path.name} : {exc.strerror or exc}")
            continue
        video_id, outcome = _insert_or_relink(
            db, root_id=root_id, base=base, path=path, stat=stat, digest=digest
        )
        if outcome == "moved":
            report.moved += 1
            gone.discard(video_id)
        elif outcome == "new":
            report.new += 1
            new.append(video_id)
        else:  # registered meanwhile (MCP watch_video)
            report.unchanged += 1
    return new


def _respell(db: Database, video_id: str, base: Path, path: Path) -> None:
    """Keep the stored spelling of a path equal to the disk's: bins compare it."""
    with db.write() as session:
        video = session.get_one(Video, video_id)
        video.path = str(path)
        video.rel_path = path.relative_to(base).as_posix()
        video.filename = path.name


def _update_existing(
    db: Database, video_id: str, path: Path, stat: os.stat_result, digest: str, *, base: Path
) -> None:
    with db.write() as session:
        video = session.get_one(Video, video_id)
        video.path = str(path)
        video.rel_path = path.relative_to(base).as_posix()
        video.filename = path.name
        video.size_bytes = stat.st_size
        video.mtime = stat.st_mtime
        if video.fingerprint != digest:  # new content: no earlier result describes it
            video.fingerprint = digest
            video.status = VideoStatus.NEW
            session.execute(
                sa.update(StageRun).where(StageRun.video_id == video_id).values(cache_key="")
            )
        elif video.status == VideoStatus.OFFLINE:
            video.status = VideoStatus.READY if video.last_analyzed_at else VideoStatus.NEW


def _insert_or_relink(
    db: Database, *, root_id: str, base: Path, path: Path, stat: os.stat_result, digest: str
) -> tuple[str, Literal["new", "moved", "known"]]:
    """Create a video row, or relink an offline one with the same content (moved/renamed file).
    "known": another writer (MCP ``watch_video``) registered the file meanwhile."""
    try:
        return _write_insert_or_relink(
            db, root_id=root_id, base=base, path=path, stat=stat, digest=digest
        )
    except sa.exc.IntegrityError:
        with db.read() as session:
            existing = session.execute(
                sa.select(Video.id).where(Video.path_key == path_key(path))
            ).scalar_one_or_none()
        if existing is None:  # the root itself was removed meanwhile
            raise NotFoundError(
                f"Dossier de bibliothèque retiré pendant le scan : {root_id}"
            ) from None
        return existing, "known"


def _write_insert_or_relink(
    db: Database, *, root_id: str, base: Path, path: Path, stat: os.stat_result, digest: str
) -> tuple[str, Literal["new", "moved", "known"]]:
    with db.write() as session:
        orphan = session.execute(
            sa.select(Video)
            .where(Video.fingerprint == digest, Video.status == VideoStatus.OFFLINE)
            .limit(1)
        ).scalar_one_or_none()
        target = orphan or Video(fingerprint=digest, status=VideoStatus.NEW)
        target.root_id = root_id
        target.path = str(path)
        target.path_key = path_key(path)
        target.rel_path = path.relative_to(base).as_posix()
        target.filename = path.name
        target.size_bytes = stat.st_size
        target.mtime = stat.st_mtime
        if orphan is not None:
            has_frames = session.execute(
                sa.select(sa.func.count()).where(Keyframe.video_id == orphan.id)
            ).scalar_one()
            analysed = has_frames or orphan.last_analyzed_at is not None
            orphan.status = VideoStatus.READY if analysed else VideoStatus.NEW
        else:
            session.add(target)
        session.flush()
        return target.id, "moved" if orphan is not None else "new"


def _mark_missing(db: Database, root_id: str, seen: set[str]) -> set[str]:
    """Mark the root's videos whose file is gone as offline; return their ids."""
    with db.write() as session:
        missing = [
            v
            for v in session.execute(
                sa.select(Video).where(
                    Video.root_id == root_id, Video.status != VideoStatus.OFFLINE
                )
            ).scalars()
            if v.path_key not in seen and not Path(v.path).exists()
        ]
        for video in missing:
            video.status = VideoStatus.OFFLINE
        return {video.id for video in missing}


def register_file(db: Database, root_id: str, path: Path) -> str:
    """Make sure one file under a root is known (used by MCP ``watch_video``); return its id."""
    with db.read() as session:
        root = session.get(LibraryRoot, root_id)
        if root is None:
            raise NotFoundError(f"Dossier de bibliothèque introuvable : {root_id}")
        base = Path(root.path)
        existing = session.execute(
            sa.select(Video).where(Video.path_key == path_key(path))
        ).scalar_one_or_none()
    stat = path.stat()
    if existing is not None:
        unchanged = existing.size_bytes == stat.st_size and existing.mtime == stat.st_mtime
        if unchanged and existing.status != VideoStatus.OFFLINE:
            return existing.id
        _update_existing(db, existing.id, path, stat, fingerprint(path, stat.st_size), base=base)
        return existing.id
    video_id, _moved = _insert_or_relink(
        db,
        root_id=root_id,
        base=base,
        path=path,
        stat=stat,
        digest=fingerprint(path, stat.st_size),
    )
    return video_id
