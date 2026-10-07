"""Library roots: declared folders of videos (only each video's analysis file is written there),
and the folders of files chosen from a Resolve timeline (``jobs.roots``)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from vfe_vision.core.errors import ConflictError, InvalidInputError, NotFoundError
from vfe_vision.core.paths import EXAMPLE_FOLDER, is_within, path_key, volume_serial
from vfe_vision.db.models import Job, LibraryRoot, Video
from vfe_vision.domain.enums import JobKind, RootKind, VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.roots import absorb_files_roots, covering_root, files_root_at
from vfe_vision.services.container import AppContainer

SCAN_PRIORITY = 10
CHOSEN_FILES_ONLY = "Un dossier de fichiers choisis n'a ni sous-dossiers ni exclusions."

_EDITABLE = {
    "label", "recursive", "exclude_globs", "analysis_focus", "clock_offset_s",
    "default_latitude", "default_longitude", "default_timezone", "auto_analyze",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class RootStats:
    root: LibraryRoot
    video_count: int
    ready_count: int
    offline_count: int


def list_roots(c: AppContainer) -> list[RootStats]:
    with c.db.read() as session:
        roots = session.execute(sa.select(LibraryRoot).order_by(LibraryRoot.label)).scalars().all()
        counts = {
            (row.root_id, row.status): row.n
            for row in session.execute(
                sa.select(Video.root_id, Video.status, sa.func.count().label("n")).group_by(
                    Video.root_id, Video.status
                )
            )
        }
    stats = []
    for root in roots:
        by_status = {status: n for (rid, status), n in counts.items() if rid == root.id}
        stats.append(
            RootStats(
                root=root,
                video_count=sum(by_status.values()),
                ready_count=by_status.get(VideoStatus.READY, 0),
                offline_count=by_status.get(VideoStatus.OFFLINE, 0),
            )
        )
    return stats


def get_root(c: AppContainer, root_id: str) -> LibraryRoot:
    with c.db.read() as session:
        root = session.get(LibraryRoot, root_id)
        if root is None:
            raise NotFoundError(f"Dossier introuvable : {root_id}")
        return root


def add_root(
    c: AppContainer,
    path: str,
    *,
    label: str | None = None,
    recursive: bool = True,
    analysis_focus: str | None = None,
    auto_analyze: bool = True,
) -> tuple[LibraryRoot, Job]:
    """Declare a folder and queue its scan. It may not overlap another folder; files chosen
    from a timeline in it or below join it with their analyses, and follow its settings."""
    folder = Path(path).expanduser()
    if not folder.is_absolute():
        raise InvalidInputError(f"Indiquez un chemin absolu, par exemple {EXAMPLE_FOLDER}.")
    if not folder.is_dir():
        raise InvalidInputError(f"Ce dossier n'existe pas ou n'est pas accessible : {folder}")
    if is_within(c.settings.data_dir, folder) or is_within(folder, c.settings.data_dir):
        raise InvalidInputError("Le dossier de données de l'application ne peut pas être ajouté.")
    with c.db.write() as session:
        folders = sa.select(LibraryRoot).where(LibraryRoot.kind == RootKind.FOLDER)
        for existing in session.execute(folders).scalars():
            if is_within(folder, existing.path) or is_within(existing.path, folder):
                raise ConflictError(
                    f"Ce dossier recoupe un dossier déjà déclaré : {existing.path}",
                    root_id=existing.id,
                )
        # Files chosen from a timeline in this very folder: that root becomes the whole folder,
        # its videos and their analyses stay.
        root = files_root_at(session, folder)
        if root is None:
            root = LibraryRoot(path_key=path_key(folder), volume_serial=volume_serial(folder))
            session.add(root)
        root.kind = RootKind.FOLDER
        root.files = None
        root.path = str(folder)
        root.label = label or folder.name or str(folder)
        root.recursive = recursive
        root.analysis_focus = analysis_focus
        root.auto_analyze = auto_analyze
        session.flush()
        absorb_files_roots(session, root)  # and those below it: their videos move in
        root_id = root.id
    job = queue.enqueue(c.db, JobKind.SCAN_ROOT, root_id=root_id, priority=SCAN_PRIORITY)
    return get_root(c, root_id), job


def update_root(c: AppContainer, root_id: str, patch: dict[str, Any]) -> LibraryRoot:
    unknown = set(patch) - _EDITABLE
    if unknown:
        raise InvalidInputError(f"Champs non modifiables : {', '.join(sorted(unknown))}")
    with c.db.write() as session:
        root = session.get(LibraryRoot, root_id)
        if root is None:
            raise NotFoundError(f"Dossier introuvable : {root_id}")
        if root.kind == RootKind.FILES and (patch.get("recursive") or patch.get("exclude_globs")):
            raise InvalidInputError(CHOSEN_FILES_ONLY)
        for key, value in patch.items():
            setattr(root, key, value)
        return root


def remove_root(c: AppContainer, root_id: str) -> None:
    """Forget a folder and its analyses (the video files themselves are untouched)."""
    with c.db.write() as session:
        root = session.get(LibraryRoot, root_id)
        if root is None:
            raise NotFoundError(f"Dossier introuvable : {root_id}")
        video_ids = list(
            session.execute(sa.select(Video.id).where(Video.root_id == root_id)).scalars()
        )
        session.delete(root)
    for video_id in video_ids:
        c.artifacts.delete_video(video_id)


def request_scan(c: AppContainer, root_id: str) -> Job:
    get_root(c, root_id)
    return queue.enqueue(c.db, JobKind.SCAN_ROOT, root_id=root_id, priority=SCAN_PRIORITY)


def root_for_path(c: AppContainer, path: Path) -> LibraryRoot | None:
    """The root a file belongs to (a file next to chosen files but not listed has none)."""
    with c.db.read() as session:
        return covering_root(session, path)


@dataclass(slots=True)
class Folder:
    """A bin: a folder of a root, as in DaVinci Resolve's media pool."""

    name: str
    path: str  # relative to the root, "/"-separated; "" for the root itself
    count: int = 0  # videos directly in it
    total: int = 0  # with its sub-folders
    children: list[Folder] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RootFolders:
    root: LibraryRoot
    tree: Folder  # the root's own bin, named like the root


def folder_tree(c: AppContainer) -> list[RootFolders]:
    """Every root with the folders its videos sit in (folders without videos are not listed)."""
    with c.db.read() as session:
        roots = list(
            session.execute(
                sa.select(LibraryRoot).order_by(sa.func.lower(LibraryRoot.label))
            ).scalars()
        )
        rows = session.execute(sa.select(Video.root_id, Video.rel_path)).all()
    paths: dict[str, list[str]] = defaultdict(list)
    for root_id, rel_path in rows:
        paths[root_id].append(rel_path)
    return [RootFolders(root, build_tree(root.label, paths[root.id])) for root in roots]


def build_tree(name: str, rel_paths: list[str]) -> Folder:
    top = Folder(name=name, path="")
    nodes = {"": top}
    for rel_path in rel_paths:
        node = top
        node.total += 1
        path = ""
        for part in rel_path.split("/")[:-1]:
            path = f"{path}/{part}" if path else part
            child = nodes.get(path.casefold())
            if child is None:
                child = nodes[path.casefold()] = Folder(name=part, path=path)
                node.children.append(child)
            node = child
            node.total += 1
        node.count += 1
    _sort(top)
    return top


def _sort(folder: Folder) -> None:
    folder.children.sort(key=lambda child: child.name.casefold())
    for child in folder.children:
        _sort(child)
