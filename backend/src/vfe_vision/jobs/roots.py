"""Which root a file belongs to, and the roots of chosen files.

A folder root covers its whole folder tree: its sub-folder and exclusion settings only steer its
scan, so a timeline's file it excludes is still registered under it (an explicit request wins).
A root of chosen files covers only the files it lists, directly in its folder; it is made for
the files of a Resolve timeline that no folder root covers, so that only those videos join the
library, never the rest of their folder. A file is covered by at most one root.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision.core.errors import ConflictError, PathNotAllowedError
from vfe_vision.core.paths import is_within, path_key, volume_serial
from vfe_vision.db.models import Job, LibraryRoot, Video
from vfe_vision.domain.enums import JobKind, RootKind
from vfe_vision.jobs import queue

DATA_DIR = "Le dossier de données de l'application ne peut pas être ajouté."
BUSY = "Une mise à jour d'un dossier concerné est en cours, réessayez dans un instant."


def covering_root(session: Session, path: Path) -> LibraryRoot | None:
    """The root of a file (see ``root_covering``)."""
    return root_covering(session.execute(sa.select(LibraryRoot)).scalars().all(), path)


def root_covering(roots: Sequence[LibraryRoot], path: Path) -> LibraryRoot | None:
    """The root of a file among ``roots``: the root of chosen files listing it (its name
    compared without case), else the deepest folder root holding it."""
    folder = path_key(path.parent)
    name = path.name.casefold()
    for root in roots:
        if root.kind != RootKind.FILES or path_key(root.path) != folder:
            continue
        if name in {listed.casefold() for listed in root.files or ()}:
            return root
    return folder_root(roots, path)


def folder_root(roots: Iterable[LibraryRoot], path: Path) -> LibraryRoot | None:
    """The deepest folder root holding ``path`` (roots of chosen files aside)."""
    holding = [r for r in roots if r.kind == RootKind.FOLDER and is_within(path, r.path)]
    return max(holding, key=lambda r: len(path_key(r.path)), default=None)


def files_root_at(session: Session, folder: Path) -> LibraryRoot | None:
    """The root of chosen files whose folder is ``folder``."""
    key = path_key(folder)
    roots = session.execute(sa.select(LibraryRoot).where(LibraryRoot.kind == RootKind.FILES))
    return next((r for r in roots.scalars() if path_key(r.path) == key), None)


def files_roots_under(session: Session, folder: Path) -> list[LibraryRoot]:
    """The roots of chosen files in ``folder`` or below it."""
    roots = session.execute(sa.select(LibraryRoot).where(LibraryRoot.kind == RootKind.FILES))
    return [r for r in roots.scalars() if is_within(r.path, folder)]


def ensure_files_root(
    session: Session,
    folder: Path,
    name: str,
    *,
    auto_analyze: bool,
    analysis_focus: str | None,
    data_dir: Path,
) -> LibraryRoot:
    """The root that covers ``folder/name``, made or completed when none does: the root of
    chosen files of that folder then lists the name (the settings of an existing one are
    kept). Coverage is checked again here, in the caller's write, so that two updates adding
    files of one folder never make two roots."""
    file = folder / name
    if is_within(file, data_dir):
        raise PathNotAllowedError(DATA_DIR)
    covering = covering_root(session, file)
    if covering is not None:
        return covering
    root = files_root_at(session, folder)
    if root is not None:
        root.files = [*(root.files or []), name]  # a new list: the JSON column sees the change
        return root
    root = LibraryRoot(
        kind=RootKind.FILES,
        path=str(folder),
        path_key=path_key(folder),
        files=[name],
        volume_serial=volume_serial(folder),
        label=folder.name or str(folder),
        recursive=False,
        analysis_focus=analysis_focus,
        auto_analyze=auto_analyze,
    )
    session.add(root)
    session.flush()
    return root


def unlist(session: Session, root_id: str, name: str) -> None:
    """A video of a root of chosen files left it (moved, or taken out of the library): its name
    leaves the list, and the root goes when it holds nothing any more. Call after the video
    moved or was deleted (flushed)."""
    root = session.get(LibraryRoot, root_id)
    if root is None or root.kind != RootKind.FILES:
        return
    root.files = [n for n in (root.files or []) if n.casefold() != name.casefold()]
    left = session.scalar(
        sa.select(sa.func.count()).select_from(Video).where(Video.root_id == root_id)
    )
    if not root.files and not left:
        session.delete(root)


def absorb_files_roots(session: Session, folder_root: LibraryRoot) -> int:
    """A folder root declared above roots of chosen files takes their videos, with their
    analyses (only the video rows move: new root, path relative to it), and the roots go.
    The videos then follow the folder root's settings. Refused while one of them is being
    scanned. Returns how many roots were absorbed."""
    base = Path(folder_root.path)
    absorbed = [r for r in files_roots_under(session, base) if r.id != folder_root.id]
    busy = session.execute(
        sa.select(Job.id)
        .where(Job.kind == JobKind.SCAN_ROOT, Job.status.in_(queue.ACTIVE))
        .where(Job.root_id.in_([r.id for r in absorbed]))
        .limit(1)
    ).first()
    if busy is not None:
        raise ConflictError(BUSY)
    for root in absorbed:
        prefix = "/".join(Path(root.path).parts[len(base.parts) :])
        session.execute(
            sa.update(Video)
            .where(Video.root_id == root.id)
            .values(root_id=folder_root.id, rel_path=sa.literal(f"{prefix}/") + Video.rel_path)
            .execution_options(synchronize_session=False)
        )
        session.flush()
        # A plain delete: the videos moved, and the ORM would otherwise load them to cascade.
        session.execute(
            sa.delete(LibraryRoot)
            .where(LibraryRoot.id == root.id)
            .execution_options(synchronize_session=False)
        )
        session.expunge(root)
    return len(absorbed)
