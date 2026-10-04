"""The update of a timeline bin (job ``sync_timeline``): the files of a DaVinci
Resolve timeline become videos of the library.

The service stored the timeline's files as items when it read Resolve; this job looks at each
of them on the disk, in timeline order, and links it to its video: the one at its path, the
same file under another path (checked by content), or a new one registered under the root that
covers it; a root of chosen files is made when none does and the request allows it. Then it
asks for the analyses still missing, and writes again the analysis files whose links changed.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, PathNotAllowedError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.core.paths import is_cloud_placeholder, path_key
from vfe_vision.db.models import TimelineBin, TimelineBinItem, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.session import Database
from vfe_vision.db.timeline_bins import bin_video_keys, links_digest, resolve_links
from vfe_vision.domain.enums import JobKind, TimelineItemState, VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.roots import covering_root, ensure_files_root
from vfe_vision.jobs.scan import fingerprint, register_file
from vfe_vision.jobs.sidecars import write_after
from vfe_vision.pipeline.runner import EventSink
from vfe_vision.pipeline.stage import ProgressFn

log = get_logger(__name__)

SCANNED_EVERY = 25  # files registered between two « library.scanned » (the page refreshes)
ANALYSIS_PRIORITY = 50  # as a video asked for through MCP
CLOUD_NOTE = "fichier OneDrive non présent sur ce PC"
OUTSIDE_NOTE = "hors bibliothèque"
GONE = "Timeline retirée de la bibliothèque"
# Windows errors of a network share out of reach (path or name not found, gone, unreachable):
# Python reports some of them as FileNotFoundError, but the file may well be there.
NETWORK_ERRORS = frozenset({53, 64, 67, 1231})

FolderFiles = dict[str, tuple[str, int]]  # name casefolded → (name on the disk, size)


def folder_files(folder: str) -> FolderFiles | None:
    """The files of a folder, None when the folder does not exist; OSError when it cannot be
    read (a network share out of reach)."""
    try:
        with os.scandir(folder) as entries:
            return {e.name.casefold(): (e.name, e.stat().st_size) for e in entries if e.is_file()}
    except (FileNotFoundError, NotADirectoryError) as exc:
        if getattr(exc, "winerror", None) in NETWORK_ERRORS:
            raise
        return None


@dataclass(slots=True)
class SyncReport:
    linked: int = 0
    added: int = 0  # videos new to the library
    missing: int = 0
    outside: int = 0
    errors: int = 0
    queued: int = 0  # analyses asked for
    gone: bool = False  # the bin was removed before the job ran

    @property
    def message(self) -> str:
        if self.gone:
            return GONE
        parts = [
            _counted(self.linked, "vidéo liée", "vidéos liées"),
            _counted(self.added, "ajoutée", "ajoutées"),
            _counted(self.missing, "introuvable", "introuvables"),
            f"{self.outside} hors bibliothèque",
            _counted(self.queued, "analyse demandée", "analyses demandées"),
        ]
        if self.errors:
            parts.append(f"{self.errors} en erreur")
        return " · ".join(parts)


def _counted(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


@dataclass(frozen=True, slots=True)
class _Item:
    path: str
    path_key: str
    video_key: str | None


@dataclass(frozen=True, slots=True)
class _Outcome:
    state: TimelineItemState
    note: str | None = None
    video_key: str | None = None  # the video's path key, when it is not the item's own
    video_id: str | None = None
    added: bool = False
    registered: bool = False  # a file looked at and written in the library


@dataclass(frozen=True, slots=True)
class _Request:
    allow_new_folders: bool
    analyze: bool
    data_dir: Path


class _Disk:
    """The folders the job looked at: each one is listed once."""

    def __init__(self) -> None:
        self._folders: dict[str, FolderFiles | OSError | None] = {}

    def find(self, path: Path) -> Path | None:
        """The file as spelled on the disk, None when it is not there (OSError: its folder
        cannot be read)."""
        key = path_key(path.parent)
        if key not in self._folders:
            try:
                self._folders[key] = folder_files(str(path.parent))
            except OSError as exc:
                self._folders[key] = exc
        files = self._folders[key]
        if isinstance(files, OSError):
            raise files
        found = files.get(path.name.casefold()) if files else None
        return path.with_name(found[0]) if found else None


def sync_timeline(
    db: Database,
    payload: Mapping[str, Any],
    *,
    data_dir: Path,
    events: EventSink,
    job_id: str | None = None,
    cancel: CancelToken | None = None,
    progress: ProgressFn | None = None,
) -> SyncReport:
    """Link each file of the bin to its video (see the module), then ask for the missing
    analyses and write again the analysis files whose links changed."""
    bin_id = str(payload.get("bin_id") or "")
    previous: list[str] = list(payload.get("videos") or ())  # the bin's videos before the request
    items = _items(db, bin_id)
    if items is None:  # removed: its videos' analysis files no longer name it
        _write_sidecars(db, None, previous, events, job_id=job_id)
        return SyncReport(gone=True)
    request = _Request(
        allow_new_folders=bool(payload.get("allow_new_folders")),
        analyze=bool(payload.get("analyze")),
        data_dir=data_dir,
    )
    hints: Mapping[str, str] = payload.get("hints") or {}
    report = SyncReport()
    disk = _Disk()
    linked: list[str] = []
    registered = 0
    for index, item in enumerate(items):
        if cancel is not None:
            cancel.raise_if_cancelled()
        if progress is not None:
            progress(index / max(1, len(items)), f"{index}/{len(items)} fichiers")
        outcome = _look_at(db, item, disk, request, hints.get(item.path_key))
        _count(report, outcome)
        _store(db, bin_id, item.path_key, outcome)
        if outcome.video_id is not None:
            linked.append(outcome.video_id)
        if outcome.registered:
            registered += 1
            if registered % SCANNED_EVERY == 0:
                events.emit("library.scanned", job_id=job_id, data={"bin_id": bin_id})
    if request.analyze:
        report.queued = _ask_analyses(db, linked)
    _write_sidecars(db, bin_id, previous, events, job_id=job_id)
    if registered:
        events.emit("library.scanned", job_id=job_id, data={"bin_id": bin_id})
    events.emit(
        "timeline.synced",
        job_id=job_id,
        data={
            "bin_id": bin_id, "linked": report.linked, "added": report.added,
            "missing": report.missing, "outside": report.outside, "errors": report.errors,
            "queued": report.queued,
        },
    )  # fmt: skip
    if progress is not None:
        progress(1.0, report.message)
    return report


def _items(db: Database, bin_id: str) -> list[_Item] | None:
    with db.read() as session:
        if session.get(TimelineBin, bin_id) is None:
            return None
        rows = session.execute(
            sa.select(TimelineBinItem.path, TimelineBinItem.path_key, TimelineBinItem.video_key)
            .where(TimelineBinItem.bin_id == bin_id)
            .order_by(TimelineBinItem.position)
        ).all()
    return [_Item(row.path, row.path_key, row.video_key) for row in rows]


def _look_at(
    db: Database, item: _Item, disk: _Disk, request: _Request, hint: str | None
) -> _Outcome:
    """One file: a failure is the file's state, never the job's (cancelling stops the job)."""
    try:
        return _link(db, item, disk, request, hint)
    except CancelledError:
        raise
    except PathNotAllowedError as exc:  # the application's own data folder
        return _Outcome(TimelineItemState.OUTSIDE, exc.detail)
    except VfeError as exc:
        return _Outcome(TimelineItemState.ERROR, exc.detail[:300])
    except OSError as exc:
        return _Outcome(TimelineItemState.ERROR, str(exc.strerror or exc)[:300])
    except Exception as exc:  # one odd file never stops the others: logged
        log.exception("timeline file not linked", path=item.path)
        return _Outcome(TimelineItemState.ERROR, f"{type(exc).__name__}: {exc}"[:300])


def _link(db: Database, item: _Item, disk: _Disk, request: _Request, hint: str | None) -> _Outcome:
    path = Path(item.path)
    known, other_path = _known(db, item)
    if known is not None:
        if other_path:
            return _Outcome(
                TimelineItemState.LINKED, f"même fichier que {known.path}", known.path_key,
                known.id,
            )  # fmt: skip
        if known.status == VideoStatus.OFFLINE:  # back at its place: known again
            present = disk.find(path)
            if present is not None and not is_cloud_placeholder(present):
                register_file(db, known.root_id, present)
                return _Outcome(TimelineItemState.LINKED, video_id=known.id, registered=True)
        return _Outcome(TimelineItemState.LINKED, video_id=known.id)
    present = disk.find(path)
    if present is None:
        return _Outcome(TimelineItemState.MISSING)
    if is_cloud_placeholder(present):  # reading it would download it
        return _Outcome(TimelineItemState.ERROR, CLOUD_NOTE)
    same = _same_file(db, present, hint)
    if same is not None:
        return _Outcome(
            TimelineItemState.LINKED, f"même fichier que {same.path}", same.path_key, same.id
        )
    root_id = _root_for(db, present, request)
    if root_id is None:
        return _Outcome(TimelineItemState.OUTSIDE, OUTSIDE_NOTE)
    video_id = register_file(db, root_id, present)
    return _Outcome(TimelineItemState.LINKED, video_id=video_id, added=True, registered=True)


def _known(db: Database, item: _Item) -> tuple[Video | None, bool]:
    """The video at the item's path, else the one it was found to be before under another
    path; and whether it is that other one."""
    with db.read() as session:
        video = session.execute(
            sa.select(Video).where(Video.path_key == item.path_key)
        ).scalar_one_or_none()
        if video is not None or item.video_key is None:
            return video, False
        other = session.execute(
            sa.select(Video).where(Video.path_key == item.video_key)
        ).scalar_one_or_none()
        return other, other is not None


def _same_file(db: Database, path: Path, hint: str | None) -> Video | None:
    """A video of the library that is this file under another path (a network share and its
    mapped drive, a copy): the one our Resolve script tagged, or one of the same name and size,
    confirmed by content. Offline videos are left to ``register_file``, which relinks them."""
    size = path.stat().st_size
    same = Video.size_bytes == size
    with db.read() as session:
        rows = session.execute(
            sa.select(Video)
            .where(Video.status != VideoStatus.OFFLINE)
            .where(sa.or_(same, Video.id == hint) if hint else same)
        ).scalars()
        name = path.name.casefold()
        candidates = [v for v in rows if v.id == hint or v.filename.casefold() == name]
    if not candidates:
        return None
    digest = fingerprint(path, size)
    return next((v for v in candidates if v.fingerprint == digest), None)


def _root_for(db: Database, path: Path, request: _Request) -> str | None:
    """The root the file goes to: the one covering it, else (when allowed) the root of chosen
    files of its folder, made or completed; None: outside the library."""
    with db.write() as session:
        root = covering_root(session, path)
        if root is None and request.allow_new_folders:
            root = ensure_files_root(
                session, path.parent, path.name, auto_analyze=request.analyze,
                analysis_focus=None, data_dir=request.data_dir,
            )  # fmt: skip
        return root.id if root is not None else None


def _count(report: SyncReport, outcome: _Outcome) -> None:
    if outcome.state == TimelineItemState.LINKED:
        report.linked += 1
        report.added += outcome.added
    elif outcome.state == TimelineItemState.MISSING:
        report.missing += 1
    elif outcome.state == TimelineItemState.OUTSIDE:
        report.outside += 1
    else:
        report.errors += 1


def _store(db: Database, bin_id: str, key: str, outcome: _Outcome) -> None:
    """Write the item's state; an item replaced meanwhile (a newer read) is left alone."""
    with db.write() as session:
        session.execute(
            sa.update(TimelineBinItem)
            .where(TimelineBinItem.bin_id == bin_id, TimelineBinItem.path_key == key)
            .values(state=outcome.state.value, note=outcome.note, video_key=outcome.video_key)
        )


def _ask_analyses(db: Database, video_ids: list[str]) -> int:
    """An ordinary (« complete ») analysis of the linked videos never fully analysed, unless
    one is already on its way. Returns how many were asked for."""
    wanted = list(dict.fromkeys(video_ids))
    with db.read() as session:
        needing = set(
            session.execute(
                sa.select(Video.id).where(Video.id.in_(wanted), Video.status.in_(queue.NEEDS_WORK))
            ).scalars()
        )
        busy = queue.active_analysis(session, needing)
    todo = [video_id for video_id in wanted if video_id in needing - busy]
    for video_id in todo:
        queue.enqueue(
            db, JobKind.ANALYZE_VIDEO, video_id=video_id, priority=ANALYSIS_PRIORITY,
            payload={"mode": "complete", "force": False, "refresh": False},
        )  # fmt: skip
    return len(todo)


# ---------------------------------------------------------------- analysis files
NO_LINKS = links_digest(())


def bin_videos(session: Session, bin_id: str) -> list[str]:
    """The ids of the videos a bin holds now."""
    return list(
        session.execute(
            sa.select(Video.id).where(Video.path_key.in_(bin_video_keys(bin_id)))
        ).scalars()
    )


def link_digests(
    session: Session, *, video_ids: Collection[str] = (), bin_id: str | None = None
) -> dict[str, str]:
    """The digest of the Resolve links of these videos and of a bin's videos, by video id."""
    conditions: list[sa.ColumnElement[bool]] = []
    if video_ids:
        conditions.append(Video.id.in_(list(video_ids)))
    if bin_id is not None:
        conditions.append(Video.path_key.in_(bin_video_keys(bin_id)))
    if not conditions:
        return {}
    rows = session.execute(sa.select(Video.id, Video.path_key).where(sa.or_(*conditions))).all()
    links = resolve_links(session, [row.path_key for row in rows])
    return {row.id: links_digest(links.get(row.path_key, ())) for row in rows}


def sidecars_to_write(db: Database, video_ids: Collection[str], bin_id: str | None) -> list[str]:
    """Among these videos and the bin's, those whose analysis file does not say their links as
    they are now (``Video.sidecar_links``, set when a file is written: an update stopped
    halfway is caught up by the next), that have an analysis and none queued or running (it
    writes the file when it ends)."""
    with db.read() as session:
        now = link_digests(session, video_ids=video_ids, bin_id=bin_id)
        written = dict(
            session.execute(
                sa.select(Video.id, Video.sidecar_links).where(Video.id.in_(list(now)))
            ).all()
        )
        changed = [
            video_id for video_id, digest in now.items()
            if (written.get(video_id) or NO_LINKS) != digest
        ]  # fmt: skip
        if not changed:
            return []
        analysed = set(
            session.execute(
                sa.select(Video.id)
                .where(Video.id.in_(changed))
                .where(Video.last_analyzed_at.is_not(None))
            ).scalars()
        )
        busy = queue.active_analysis(session, changed)
    return sorted(analysed - busy)


def _write_sidecars(
    db: Database,
    bin_id: str | None,
    video_ids: Collection[str],
    events: EventSink,
    *,
    job_id: str | None,
) -> None:
    if not load_preferences(db).sidecar_files:
        return
    job_log = log.bind(job_id=job_id, bin_id=bin_id)
    for video_id in sidecars_to_write(db, video_ids, bin_id):
        write_after(db, video_id, events, job_id=job_id, log=job_log)
