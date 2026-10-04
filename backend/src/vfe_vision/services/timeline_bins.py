"""DaVinci Resolve timelines in the library: read a timeline, bring its videos in as a
timeline bin, update it from Resolve; list, rename and remove the bins.

Resolve is only read (``c.resolve``: a short-lived child process). What was read is stored in
one write — the bin and its items, the timeline as it is now — with the update job
(``jobs.timeline_bins``) that looks at the files on the disk and registers them: a request
returns as soon as Resolve has answered.
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.errors import ConflictError, InvalidInputError, NotFoundError
from vfe_vision.core.paths import is_cloud_placeholder, is_video_file, path_key
from vfe_vision.db.models import Job, LibraryRoot, TimelineBin, TimelineBinItem, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.timeline_bins import ITEM_KEY
from vfe_vision.domain.clip_paths import canonical_path
from vfe_vision.domain.enums import JobKind, JobStatus, TimelineItemState, VideoStatus
from vfe_vision.domain.path_map import from_resolve
from vfe_vision.domain.preferences import folder_pairs
from vfe_vision.domain.resolve_timeline import (
    ResolveProjectInfo,
    SkippedItems,
    TimelineContent,
    TimelineFile,
    TimelineInfo,
    identity_parts,
    resolve_identity,
    skipped_to_json,
    source_key,
    timeline_files,
    use_to_json,
)
from vfe_vision.jobs import queue
from vfe_vision.jobs.roots import root_covering
from vfe_vision.jobs.timeline_bins import CLOUD_NOTE, FolderFiles, bin_videos, folder_files
from vfe_vision.services.container import AppContainer

SYNC_PRIORITY = 10  # as a folder scan: the page waits for it
LABEL_MAX = 200
NEW_FOLDERS_SHOWN = 20
LOOK_DEADLINE_S = 5.0  # folders not listed by then: a network share out of reach
LOOK_THREADS = 8

PROJECT_CHANGED = "Le projet ouvert dans DaVinci Resolve a changé (« {name} »)."
SYNC_RUNNING = "Une mise à jour de cette timeline est déjà en cours."
OPEN_PROJECT = "Ouvrez le projet « {name} » dans DaVinci Resolve pour mettre cette timeline à jour."
SAME_NAME = "Un autre projet porte le même nom (base « {db} ») : ouvrez celui d'origine."
TIMELINE_GONE = "La timeline « {name} » n'existe plus dans ce projet."
UNREACHABLE_NOTE = "partage réseau injoignable lors de la lecture de la timeline"

# How a file of a timeline stands, before it is brought in.
IN_LIBRARY = "in_library"  # a video of the library at this path
OTHER_PATH = "other_path"  # no video at this path, but one of the same name and size elsewhere
IN_FOLDER = "in_folder"  # in a folder of the library, not registered yet
NEW_FOLDER = "new_folder"  # in no folder of the library: only this file would join it
MISSING = "missing"
UNKNOWN = "unknown"  # its folder could not be read in time (a network share out of reach)

# What the page shows of a bin's file (the stored state, and what the library holds now).
DISPLAY_STATES = (
    "in_library", "adding", "not_processed", "removed", "missing", "outside", "error"
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class TimelinePreview:
    """What bringing a timeline in would do (nothing written)."""

    content: TimelineContent  # clips aside, what was read
    files: int
    skipped: SkippedItems
    in_library: int = 0
    other_path: int = 0
    in_folder: int = 0
    new_folder: int = 0
    missing: int = 0
    unknown: int = 0
    disabled_only: int = 0  # files used on disabled clips or tracks only (still brought in)
    to_analyze: int = 0  # videos an analysis would be asked for (with the analysis switch on)
    new_folders: tuple[str, ...] = ()  # folders of the files that would join alone
    bin_id: str | None = None  # the bin of this timeline, when it is already in the library
    bin_label: str | None = None
    snapshot_id: str | None = None  # the read, kept a little while for the import


@dataclass(frozen=True, slots=True)
class BinStats:
    bin: TimelineBin
    items: int = 0
    videos: int = 0  # distinct videos of the library
    states: dict[str, int] = field(default_factory=dict)  # DISPLAY_STATES → count
    sync_job: Job | None = None  # the latest update


@dataclass(frozen=True, slots=True)
class BinImport:
    stats: BinStats
    job: Job
    created: bool
    preview: TimelinePreview


@dataclass(frozen=True, slots=True)
class ItemView:
    position: int
    path: str
    state: str  # one of DISPLAY_STATES
    note: str | None
    video_id: str | None


@dataclass(frozen=True, slots=True)
class TimelineView:
    info: TimelineInfo
    bin: TimelineBin | None = None
    changed_since_sync: bool | None = None  # None: not in the library


@dataclass(frozen=True, slots=True)
class ProjectView:
    info: ResolveProjectInfo
    timelines: list[TimelineView]  # the current one first, then by name


# ---------------------------------------------------------------- the open project
def project_view(c: AppContainer) -> ProjectView:
    """The project open in Resolve and its timelines, each with its bin when it is in the
    library (a timeline whose id changed is found back by its name)."""
    info = c.resolve.project()
    with c.db.read() as session:
        bins = _project_bins(session, info.project.id)
    by_timeline = {identity_parts(b.resolve)[2].id: b for b in bins}
    live = {timeline.id for timeline in info.timelines}
    lost = [b for tid, b in by_timeline.items() if tid not in live]
    views = []
    for timeline in info.timelines:
        found = by_timeline.get(timeline.id) or _same_name(lost, timeline.name)
        views.append(TimelineView(timeline, found, _changed(found, timeline) if found else None))
    views.sort(key=lambda v: (not v.info.is_current, v.info.name.casefold(), v.info.id))
    return ProjectView(info, views)


def _project_bins(session: Session, project_id: str) -> list[TimelineBin]:
    prefix = source_key(project_id, "")
    return list(
        session.execute(
            sa.select(TimelineBin).where(TimelineBin.source_key.startswith(prefix, autoescape=True))
        ).scalars()
    )


def _same_name(bins: Iterable[TimelineBin], name: str) -> TimelineBin | None:
    return next((b for b in bins if identity_parts(b.resolve)[2].name == name), None)


def _changed(timeline_bin: TimelineBin, live: TimelineInfo) -> bool:
    """Whether the timeline looks different from its last read (name, length, clip count)."""
    stored = identity_parts(timeline_bin.resolve)[2]
    clips = None not in (stored.video_clips, live.video_clips)
    return (
        stored.name != live.name
        or stored.end_frame != live.end_frame
        or (clips and stored.video_clips != live.video_clips)
    )


# ---------------------------------------------------------------- preview, import, update
def preview(c: AppContainer, *, project_id: str | None, timeline_id: str | None) -> TimelinePreview:
    """Read a timeline and tell what bringing it in would do, without writing anything nor
    waiting on a network share out of reach. The read is kept for the import that follows."""
    content = c.resolve.timeline(timeline_id)
    _check_project(content, project_id)
    checks, skipped = _check(c, content)
    return _preview(
        content, checks, skipped, _bin_of(c, content),
        snapshot_id=c.timeline_snapshots.remember(content),
    )  # fmt: skip


def import_timeline(
    c: AppContainer,
    *,
    project_id: str | None,
    timeline_id: str | None,
    snapshot_id: str | None = None,
    label: str | None = None,
    auto_analyze: bool = True,
    allow_new_folders: bool = True,
) -> BinImport:
    """Bring a timeline in: a new bin, or the update of its bin (the preview's read is reused
    when it is recent and of this timeline). Returns once the update job is queued."""
    content = _recalled(c, snapshot_id, project_id, timeline_id) or c.resolve.timeline(timeline_id)
    _check_project(content, project_id)
    found = _bin_of(c, content)
    return _bring_in(
        c, content, found.id if found else None, label=label, auto_analyze=auto_analyze,
        analyze=auto_analyze, allow_new_folders=allow_new_folders,
    )  # fmt: skip


def sync_bin(
    c: AppContainer, bin_id: str, *, allow_new_folders: bool = True, analyze: bool | None = None
) -> BinImport:
    """Read the bin's timeline again (« Update from Resolve »): its project must be the
    one open; a timeline whose id changed is found back by its name. ``analyze`` None: as the
    bin says."""
    timeline_bin = _get_bin(c, bin_id)
    _database, project, stored = identity_parts(timeline_bin.resolve)
    info = c.resolve.project()
    if info.project.id != project.id:
        message = OPEN_PROJECT.format(name=project.name)
        if info.project.name == project.name:
            message = f"{message} {SAME_NAME.format(db=info.database.name)}"
        raise ConflictError(message, project=info.project.name)
    live = next((t for t in info.timelines if t.id == stored.id), None)
    if live is None:
        with c.db.read() as session:
            taken = {b.source_key for b in _project_bins(session, project.id)}
        live = next(
            (t for t in info.timelines
             if t.name == stored.name and source_key(project.id, t.id) not in taken),
            None,
        )  # fmt: skip
    if live is None:
        raise NotFoundError(TIMELINE_GONE.format(name=stored.name))
    content = c.resolve.timeline(live.id)
    _check_project(content, project.id)
    return _bring_in(
        c, content, timeline_bin.id, label=None, auto_analyze=None,
        analyze=timeline_bin.auto_analyze if analyze is None else analyze,
        allow_new_folders=allow_new_folders,
    )  # fmt: skip


def _check_project(content: TimelineContent, project_id: str | None) -> None:
    if project_id is not None and content.project.id != project_id:
        name = content.project.name
        raise ConflictError(PROJECT_CHANGED.format(name=name), project=name)


def _recalled(
    c: AppContainer, snapshot_id: str | None, project_id: str | None, timeline_id: str | None
) -> TimelineContent | None:
    """The preview's read, when it is still kept and of this very timeline."""
    content = c.timeline_snapshots.recall(snapshot_id) if snapshot_id else None
    if content is None or timeline_id is None or content.timeline.id != timeline_id:
        return None
    return content if project_id in (None, content.project.id) else None


def _bin_of(c: AppContainer, content: TimelineContent) -> TimelineBin | None:
    """The bin of this timeline: by its ids, else a bin of the project whose timeline is gone
    from it and had this name (a timeline duplicated or imported again gets a new id)."""
    project = content.project
    with c.db.read() as session:
        bins = _project_bins(session, project.id)
    key = source_key(project.id, content.timeline.id)
    exact = next((b for b in bins if b.source_key == key), None)
    if exact is not None:
        return exact
    same_name = [b for b in bins if identity_parts(b.resolve)[2].name == content.timeline.name]
    if not same_name:
        return None
    info = c.resolve.project()
    if info.project.id != project.id:  # another project open meanwhile: nothing to compare
        return None
    live = {timeline.id for timeline in info.timelines}
    return next((b for b in same_name if identity_parts(b.resolve)[2].id not in live), None)


# ---------------------------------------------------------------- the files of a timeline
@dataclass(frozen=True, slots=True)
class _Checked:
    file: TimelineFile
    key: str
    found: str  # IN_LIBRARY, OTHER_PATH…
    to_analyze: bool = False
    cloud: bool = False  # a OneDrive file whose content is not on this computer


def _is_video(path: str) -> bool:
    return is_video_file(Path(path))


def _check(c: AppContainer, content: TimelineContent) -> tuple[list[_Checked], SkippedItems]:
    """Where each video file of the timeline stands (see IN_LIBRARY…), without writing."""
    pairs = folder_pairs(load_preferences(c.db))

    def here(path: str) -> str | None:  # Resolve's paths, as this computer sees them
        return canonical_path(from_resolve(path, pairs) or path)

    files, skipped = timeline_files(content.clips, is_video=_is_video, canonical=here, key=path_key)
    keys = [path_key(f.path) for f in files]
    with c.db.read() as session:
        known: dict[str, VideoStatus] = dict(
            session.execute(
                sa.select(Video.path_key, Video.status).where(Video.path_key.in_(keys))
            ).all()
        )
        roots = session.execute(sa.select(LibraryRoot)).scalars().all()
    elsewhere = [(f, k) for f, k in zip(files, keys, strict=True) if k not in known]
    listings = _list_folders({str(Path(f.path).parent) for f, _k in elsewhere})
    sizes: dict[str, int] = {}
    for f, k in elsewhere:
        listing = listings.get(str(Path(f.path).parent)) or {}
        entry = listing.get(Path(f.path).name.casefold())
        if entry is not None:
            sizes[k] = entry[1]
    same = _same_files(c, [f for f, k in elsewhere if k in sizes], sizes)
    checks = []
    for f, k in zip(files, keys, strict=True):
        if k in known:
            checks.append(_Checked(f, k, IN_LIBRARY, known[k] in queue.NEEDS_WORK))
        elif str(Path(f.path).parent) not in listings:
            checks.append(_Checked(f, k, UNKNOWN))
        elif k not in sizes:
            checks.append(_Checked(f, k, MISSING))
        elif k in same:
            checks.append(_Checked(f, k, OTHER_PATH, same[k] in queue.NEEDS_WORK))
        else:
            found = NEW_FOLDER if root_covering(roots, Path(f.path)) is None else IN_FOLDER
            cloud = is_cloud_placeholder(Path(f.path))
            checks.append(_Checked(f, k, found, to_analyze=True, cloud=cloud))
    return checks, skipped


def _same_files(
    c: AppContainer, files: list[TimelineFile], sizes: Mapping[str, int]
) -> dict[str, VideoStatus]:
    """Files likely known to the library under another path (a network share and its mapped
    drive): an online video of the same name and size, or the one our Resolve script tagged.
    By path key, the status of that video. The update job confirms by content."""
    if not files:
        return {}
    hints = {f.vfe_video_id for f in files if f.vfe_video_id}
    with c.db.read() as session:
        rows = session.execute(
            sa.select(Video.id, Video.filename, Video.size_bytes, Video.status)
            .where(Video.status != VideoStatus.OFFLINE)
            .where(sa.or_(Video.size_bytes.in_(set(sizes.values())), Video.id.in_(hints)))
        ).all()
    by_name = {(row.filename.casefold(), row.size_bytes): row.status for row in rows}
    by_id = {row.id: row.status for row in rows}
    found: dict[str, VideoStatus] = {}
    for f in files:
        k = path_key(f.path)
        status = by_name.get((Path(f.path).name.casefold(), sizes[k]))
        if status is None and f.vfe_video_id:
            status = by_id.get(f.vfe_video_id)
        if status is not None:
            found[k] = status
    return found


def _list_folders(folders: Collection[str]) -> dict[str, FolderFiles | None]:
    """The files of each folder (``folder_files``), several folders at a time, for at most
    LOOK_DEADLINE_S: a folder missing from the answer could not be read in that time, or at
    all (a network share out of reach). A hanging share is left to its thread."""
    todo: SimpleQueue[str] = SimpleQueue()
    for folder in folders:
        todo.put(folder)
    listed: dict[str, FolderFiles | None] = {}
    lock = threading.Lock()
    done = threading.Event()
    left = len(folders)

    def work() -> None:
        nonlocal left
        while True:
            try:
                folder = todo.get_nowait()
            except Empty:
                return
            try:
                files, readable = folder_files(folder), True
            except OSError:
                files, readable = None, False
            with lock:
                if readable:
                    listed[folder] = files
                left -= 1
                if left == 0:
                    done.set()

    for _ in range(min(LOOK_THREADS, len(folders))):
        threading.Thread(target=work, name="timeline-folders", daemon=True).start()
    if folders:
        done.wait(LOOK_DEADLINE_S)
    with lock:
        return dict(listed)


def _preview(
    content: TimelineContent,
    checks: list[_Checked],
    skipped: SkippedItems,
    timeline_bin: TimelineBin | None,
    *,
    snapshot_id: str | None = None,
) -> TimelinePreview:
    counts = Counter(check.found for check in checks)
    folders = dict.fromkeys(str(Path(x.file.path).parent) for x in checks if x.found == NEW_FOLDER)
    return TimelinePreview(
        content=content,
        files=len(checks),
        skipped=skipped,
        in_library=counts[IN_LIBRARY],
        other_path=counts[OTHER_PATH],
        in_folder=counts[IN_FOLDER],
        new_folder=counts[NEW_FOLDER],
        missing=counts[MISSING],
        unknown=counts[UNKNOWN],
        disabled_only=sum(1 for check in checks if not check.file.enabled),
        to_analyze=sum(1 for check in checks if check.to_analyze),
        new_folders=tuple(folders)[:NEW_FOLDERS_SHOWN],
        bin_id=timeline_bin.id if timeline_bin else None,
        bin_label=timeline_bin.label if timeline_bin else None,
        snapshot_id=snapshot_id,
    )


# ---------------------------------------------------------------- storing a read
def _bring_in(
    c: AppContainer,
    content: TimelineContent,
    bin_id: str | None,
    *,
    label: str | None,
    auto_analyze: bool | None,
    analyze: bool,
    allow_new_folders: bool,
) -> BinImport:
    checks, skipped = _check(c, content)
    save = partial(
        _save, c, content, checks, skipped, label=label, auto_analyze=auto_analyze,
        analyze=analyze, allow_new_folders=allow_new_folders,
    )  # fmt: skip
    try:
        saved_id, job, created = save(bin_id)
    except sa.exc.IntegrityError:  # the same timeline brought in at the same time: update it
        found = _bin_of(c, content)
        saved_id, job, created = save(found.id if found else None)
    [stats] = list_bins(c, [saved_id])
    return BinImport(stats, job, created, _preview(content, checks, skipped, stats.bin))


def _save(
    c: AppContainer,
    content: TimelineContent,
    checks: list[_Checked],
    skipped: SkippedItems,
    bin_id: str | None,
    *,
    label: str | None,
    auto_analyze: bool | None,
    analyze: bool,
    allow_new_folders: bool,
) -> tuple[str, Job, bool]:
    """One write: the bin (made, or found and checked idle), its items replaced by the
    timeline's files as read, and the update job. Returns (bin id, job, created)."""
    label = (label or "").strip()[:LABEL_MAX] or None
    with c.db.write() as session:
        timeline_bin = session.get(TimelineBin, bin_id) if bin_id else None
        created = timeline_bin is None
        before: list[str] = []
        carried: dict[str, str] = {}
        if timeline_bin is None:
            timeline_bin = TimelineBin(
                label=label or _new_label(session, content), auto_analyze=bool(auto_analyze)
            )
            session.add(timeline_bin)
        else:
            if _sync_running(session, timeline_bin.id):
                raise ConflictError(SYNC_RUNNING)
            before = bin_videos(session, timeline_bin.id)
            carried = _confirmed_keys(session, timeline_bin.id)
            old_name = identity_parts(timeline_bin.resolve)[2].name
            if label is not None:
                timeline_bin.label = label
            elif timeline_bin.label == old_name:  # named after the timeline: follows its name
                timeline_bin.label = (content.timeline.name or old_name)[:LABEL_MAX]
            session.execute(
                sa.delete(TimelineBinItem).where(TimelineBinItem.bin_id == timeline_bin.id)
            )
        timeline_bin.source_key = source_key(content.project.id, content.timeline.id)
        timeline_bin.resolve = {
            **resolve_identity(
                content.product, content.version, content.database, content.project,
                content.timeline,
            ),
            "studio": content.studio,
        }  # fmt: skip
        timeline_bin.report = {
            "schema_version": 1, "skipped": skipped_to_json(skipped), "errors": content.errors,
        }  # fmt: skip
        timeline_bin.synced_at = datetime.now(UTC)
        if auto_analyze is not None:
            timeline_bin.auto_analyze = auto_analyze
        session.flush()
        session.add_all(_item(timeline_bin.id, check, carried.get(check.key)) for check in checks)
        payload = {
            "bin_id": timeline_bin.id,
            "allow_new_folders": allow_new_folders,
            "analyze": analyze,
            "videos": before,  # their analysis files may name the bin no longer
            "hints": {x.key: x.file.vfe_video_id for x in checks if x.file.vfe_video_id},
        }
        job = queue.enqueue_in(
            session, JobKind.SYNC_TIMELINE, payload=payload, priority=SYNC_PRIORITY
        )
        return timeline_bin.id, job, created


def _new_label(session: Session, content: TimelineContent) -> str:
    """The timeline's name, with its project's when another bin already has it."""
    name = content.timeline.name or content.timeline.id
    labels = {label.casefold() for label in session.execute(sa.select(TimelineBin.label)).scalars()}
    if name.casefold() in labels:
        name = f"{name} ({content.project.name})"
    return name[:LABEL_MAX]


def _sync_running(session: Session, bin_id: str) -> bool:
    return (
        session.execute(
            sa.select(Job.id)
            .where(Job.kind == JobKind.SYNC_TIMELINE, Job.status == JobStatus.RUNNING)
            .where(Job.payload["bin_id"].as_string() == bin_id)
            .limit(1)
        ).first()
        is not None
    )


def _confirmed_keys(session: Session, bin_id: str) -> dict[str, str]:
    """The files found before to be a video known under another path (kept: no need to read
    them again), by path key."""
    rows = session.execute(
        sa.select(TimelineBinItem.path_key, TimelineBinItem.video_key).where(
            TimelineBinItem.bin_id == bin_id,
            TimelineBinItem.state == TimelineItemState.LINKED,
            TimelineBinItem.video_key.is_not(None),
        )
    ).all()
    return {row.path_key: row.video_key for row in rows}


def _item(bin_id: str, check: _Checked, video_key: str | None) -> TimelineBinItem:
    state, note = TimelineItemState.PENDING, None
    if check.found == MISSING:
        state = TimelineItemState.MISSING
    elif check.cloud:
        state, note = TimelineItemState.ERROR, CLOUD_NOTE
    elif check.found == UNKNOWN:
        note = UNREACHABLE_NOTE
    return TimelineBinItem(
        bin_id=bin_id,
        position=check.file.position,
        path=check.file.path,
        path_key=check.key,
        video_key=video_key,
        uses=[use_to_json(use) for use in check.file.uses],
        state=state.value,
        note=note,
    )


# ---------------------------------------------------------------- the bins
def list_bins(c: AppContainer, bin_ids: Collection[str] | None = None) -> list[BinStats]:
    """The timeline bins (or these ones), by label, with the state of their files."""
    with c.db.read() as session:
        stmt = sa.select(TimelineBin)
        if bin_ids is not None:
            stmt = stmt.where(TimelineBin.id.in_(list(bin_ids)))
        bins = list(session.execute(stmt).scalars())
        ids = [b.id for b in bins]
        rows = session.execute(
            sa.select(TimelineBinItem.bin_id, TimelineBinItem.state, Video.id)
            .outerjoin(Video, Video.path_key == ITEM_KEY)
            .where(TimelineBinItem.bin_id.in_(ids))
        ).all()
        jobs = _latest_jobs(session, ids)
    states: dict[str, Counter[str]] = defaultdict(Counter)
    videos: dict[str, set[str]] = defaultdict(set)
    for bin_id, state, video_id in rows:
        states[bin_id][display_state(state, video_id, jobs.get(bin_id))] += 1
        if video_id is not None:
            videos[bin_id].add(video_id)
    stats = [
        BinStats(
            bin=b,
            items=sum(states[b.id].values()),
            videos=len(videos[b.id]),
            states={name: states[b.id][name] for name in DISPLAY_STATES},
            sync_job=jobs.get(b.id),
        )
        for b in bins
    ]
    return sorted(stats, key=lambda s: (s.bin.label.casefold(), s.bin.id))


def _latest_jobs(session: Session, bin_ids: list[str]) -> dict[str, Job]:
    """The latest update job of each bin."""
    rows = session.execute(
        sa.select(Job)
        .where(Job.kind == JobKind.SYNC_TIMELINE)
        .where(Job.payload["bin_id"].as_string().in_(bin_ids))
        .order_by(Job.created_at.desc())
    ).scalars()
    latest: dict[str, Job] = {}
    for job in rows:
        latest.setdefault(str(job.payload.get("bin_id")), job)
    return latest


def display_state(state: str, video_id: str | None, sync_job: Job | None) -> str:
    """What the page shows of a file: in the library, being added, not looked at (the update
    stopped), removed from the library since, or its stored state."""
    if video_id is not None:
        return "in_library"
    if state == TimelineItemState.PENDING:
        active = sync_job is not None and sync_job.status in queue.ACTIVE
        return "adding" if active else "not_processed"
    if state == TimelineItemState.LINKED:
        return "removed"
    return state


def bin_items(c: AppContainer, bin_id: str) -> list[ItemView]:
    """The files of a bin in timeline order, with what the page shows of each."""
    _get_bin(c, bin_id)
    with c.db.read() as session:
        rows = session.execute(
            sa.select(TimelineBinItem, Video.id)
            .outerjoin(Video, Video.path_key == ITEM_KEY)
            .where(TimelineBinItem.bin_id == bin_id)
            .order_by(TimelineBinItem.position)
        ).all()
        job = _latest_jobs(session, [bin_id]).get(bin_id)
    return [
        ItemView(item.position, item.path, display_state(item.state, video_id, job), item.note,
                 video_id)
        for item, video_id in rows
    ]  # fmt: skip


def _get_bin(c: AppContainer, bin_id: str) -> TimelineBin:
    with c.db.read() as session:
        timeline_bin = session.get(TimelineBin, bin_id)
    if timeline_bin is None:
        raise NotFoundError(f"Timeline introuvable dans la bibliothèque : {bin_id}")
    return timeline_bin


def update_bin(c: AppContainer, bin_id: str, patch: Mapping[str, Any]) -> BinStats:
    """Rename a bin, or switch its automatic analysis."""
    unknown = set(patch) - {"label", "auto_analyze"}
    if unknown:
        raise InvalidInputError(f"Champs non modifiables : {', '.join(sorted(unknown))}")
    label = patch.get("label")
    if "label" in patch and not (label or "").strip():
        raise InvalidInputError("Le nom de la timeline ne peut pas être vide.")
    with c.db.write() as session:
        timeline_bin = session.get(TimelineBin, bin_id)
        if timeline_bin is None:
            raise NotFoundError(f"Timeline introuvable dans la bibliothèque : {bin_id}")
        if label is not None:
            timeline_bin.label = label.strip()[:LABEL_MAX]
        if patch.get("auto_analyze") is not None:
            timeline_bin.auto_analyze = bool(patch["auto_analyze"])
    [stats] = list_bins(c, [bin_id])
    return stats


def remove_bin(c: AppContainer, bin_id: str) -> Job | None:
    """Take a timeline out of the library: its bin only (no video, no analysis is forgotten,
    nothing changes in Resolve). The analysis files of its videos, which no longer name it, are
    written again by a job (a network share can be slow): returned, None when there is none."""
    sidecars = load_preferences(c.db).sidecar_files
    with c.db.write() as session:
        timeline_bin = session.get(TimelineBin, bin_id)
        if timeline_bin is None:
            raise NotFoundError(f"Timeline introuvable dans la bibliothèque : {bin_id}")
        if _sync_running(session, bin_id):
            raise ConflictError(SYNC_RUNNING)
        before = bin_videos(session, bin_id)
        session.delete(timeline_bin)  # its items go with it (foreign key)
        if not (sidecars and before):
            return None
        payload = {"bin_id": bin_id, "allow_new_folders": False, "analyze": False, "videos": before}
        return queue.enqueue_in(
            session, JobKind.SYNC_TIMELINE, payload=payload, priority=SYNC_PRIORITY
        )
