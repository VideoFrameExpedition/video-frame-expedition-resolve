"""The database as a whole: exported (with the frames taken from the videos, at will),
imported, or reset (the library, the settings, or both).

The running application holds the database open, and so does its worker: an import or a reset
is prepared now and carried out at the next start, before anything opens the database, once a
copy of the current one is with the backups. ``POST /system/restart`` brings that start about
from the interface.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import zipfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from pydantic import BaseModel, Field, ValidationError

from vfe_vision import __version__
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ConflictError, InvalidInputError, NotFoundError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.core.paths import joined_inside
from vfe_vision.db.migrate import alembic_config, current_revision
from vfe_vision.db.models import Video
from vfe_vision.services.container import AppContainer

log = get_logger(__name__)

DATABASE = "vfe.sqlite3"  # in an export, as in the data folder
MANIFEST = "manifest.json"
MEDIA = "media"
FORMAT = 1  # of an export
PENDING = "pending"  # the import or the reset carried out at the next start
PLAN = "plan.json"
LAST = "last-data-operation.json"
DOWNLOADS = "downloads"  # the export waiting to be downloaded
TRASH = "trash"  # what an import or a reset replaced, deleted at the start
UPLOAD = "upload"
TOKEN = re.compile(r"[0-9a-f]{32}")
SQLITE_MAGIC = b"SQLite format 3\x00"
ZIP_MAGIC = b"PK\x03\x04"
SPARE_BYTES = 512 * 2**20  # left free on the disk after an import is unpacked
COPY_CHUNK = 2**20

_busy = threading.Lock()  # one export, import or reset prepared at a time


class NotALibraryError(InvalidInputError):
    code = "not_a_library"


class NewerLibraryError(InvalidInputError):
    code = "newer_library"


class PendingOut(BaseModel):
    """An import or a reset waiting for the next start."""

    action: Literal["import", "reset"]
    library: bool  # import: replaced; reset: erased (the videos, their analyses, their frames)
    settings: bool  # import: replaced by the imported ones; reset: erased
    images: bool = False  # import: the archive brings the frames
    videos: int | None = None  # import: the videos of the imported library
    source: str | None = None  # import: the name of the file
    prepared_at: datetime


class LastOperationOut(BaseModel):
    """What the last start carried out."""

    action: Literal["import", "reset"]
    library: bool
    settings: bool
    images: bool = False
    videos: int | None = None
    source: str | None = None
    done_at: datetime
    backup: str | None = None  # the copy of the database it replaced (backups folder)
    error: str | None = None


class DataOut(BaseModel):
    database_bytes: int
    media_bytes: int  # the frames taken from the videos
    videos: int
    backups_dir: str
    pending: PendingOut | None = None
    last: LastOperationOut | None = None
    can_restart: bool = False  # the interface can restart the application (``vfe serve``)


class ExportChoice(BaseModel):
    images: bool = Field(default=True, description="With the frames taken from the videos.")


class ExportOut(BaseModel):
    token: str  # GET /system/data/export/{token} downloads it, once
    size_bytes: int
    images: bool


class ResetChoice(BaseModel):
    library: bool = Field(default=True, description="The videos, their analyses, their frames.")
    settings: bool = Field(default=False, description="The settings of the application.")


# ---------------------------------------------------------------------- state


def status(c: AppContainer, *, can_restart: bool) -> DataOut:
    settings = c.settings
    with c.db.read() as session:
        videos = session.execute(sa.select(sa.func.count()).select_from(Video)).scalar_one()
    database = sum(_size(Path(f"{settings.db_path}{suffix}")) for suffix in ("", "-wal", "-shm"))
    return DataOut(
        database_bytes=database,
        media_bytes=sum(_size(path) for path in _files(settings.artifacts_dir)),
        videos=videos,
        backups_dir=str(settings.backups_dir),
        pending=_read(settings.data_dir / PENDING / PLAN, PendingOut),
        last=_read(settings.data_dir / LAST, LastOperationOut),
        can_restart=can_restart,
    )


def cancel(c: AppContainer) -> None:
    """Drop the import or the reset waiting for the next start."""
    with _held():
        _clear(c.settings.data_dir / PENDING)


# ---------------------------------------------------------------------- export


def export(c: AppContainer, choice: ExportChoice) -> ExportOut:
    """An archive of the database (a copy made while it runs) and, at will, of the frames.
    It replaces the previous one; it is downloaded once (``export_file``)."""
    settings = c.settings
    with _held():
        folder = settings.data_dir / DOWNLOADS
        _clear(folder)
        folder.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        snapshot = folder / f"{token}.sqlite3"
        part = folder / f"{token}.part"
        target = folder / f"{token}.zip"
        try:
            _snapshot(settings.db_path, snapshot)
            manifest = {
                "application": "Video Frame Expedition for DaVinci Resolve",
                "format": FORMAT,
                "version": __version__,
                "revision": current_revision(snapshot),
                "exported_at": datetime.now(UTC).isoformat(),
                "images": choice.images,
            }
            with zipfile.ZipFile(part, "w", allowZip64=True) as archive:
                archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
                archive.write(snapshot, DATABASE, compress_type=zipfile.ZIP_DEFLATED)
                if choice.images:  # already compressed (JPEG, WebP, MP4): stored as they are
                    media = settings.artifacts_dir
                    for path in _files(media):
                        name = f"{MEDIA}/{path.relative_to(media).as_posix()}"
                        with contextlib.suppress(FileNotFoundError):  # gone with an analysis
                            archive.write(path, name)
            part.replace(target)
        except (OSError, sqlite3.Error) as exc:
            _clear(folder)
            raise VfeError(f"Export impossible : {_reason(exc)}") from exc
        finally:
            snapshot.unlink(missing_ok=True)
        return ExportOut(token=token, size_bytes=target.stat().st_size, images=choice.images)


def export_file(c: AppContainer, token: str) -> tuple[Path, str]:
    """The prepared export and the name it is downloaded under."""
    path = c.settings.data_dir / DOWNLOADS / f"{token}.zip"
    if not TOKEN.fullmatch(token) or not path.is_file():
        raise NotFoundError("Cet export n'est plus disponible : préparez-en un nouveau.")
    made = datetime.fromtimestamp(path.stat().st_mtime, UTC).astimezone()  # the user's clock
    return path, f"video-frame-expedition-{made:%Y-%m-%d-%H%M}.zip"


# ---------------------------------------------------------------------- import and reset


async def import_file(
    c: AppContainer, chunks: AsyncIterator[bytes], *, name: str | None, keep_settings: bool
) -> PendingOut:
    """Receive an export (or a database from the backups folder), check it, and prepare its
    import for the next start."""
    pending = c.settings.data_dir / PENDING
    with _held():
        await anyio.to_thread.run_sync(_clear, pending)
        pending.mkdir(parents=True, exist_ok=True)
        upload = pending / UPLOAD
        try:
            async with await anyio.open_file(upload, "wb") as out:
                async for chunk in chunks:
                    await out.write(chunk)
            return await anyio.to_thread.run_sync(_unpack, upload, pending, name, keep_settings)
        except OSError as exc:
            await anyio.to_thread.run_sync(_clear, pending)
            raise VfeError(f"Import impossible : {_reason(exc)}") from exc
        except BaseException:
            await anyio.to_thread.run_sync(_clear, pending)
            raise


def reset(c: AppContainer, choice: ResetChoice) -> PendingOut:
    """Prepare the reset of the library, of the settings, or of both, for the next start."""
    if not (choice.library or choice.settings):
        raise InvalidInputError("Choisissez la bibliothèque, les réglages, ou les deux.")
    pending = c.settings.data_dir / PENDING
    with _held():
        _clear(pending)
        pending.mkdir(parents=True, exist_ok=True)
        plan = PendingOut(
            action="reset", library=choice.library, settings=choice.settings,
            prepared_at=datetime.now(UTC),
        )  # fmt: skip
        (pending / PLAN).write_text(plan.model_dump_json(), encoding="utf-8")
        return plan


def _unpack(upload: Path, pending: Path, name: str | None, keep_settings: bool) -> PendingOut:
    with upload.open("rb") as file:
        head = file.read(len(SQLITE_MAGIC))
    if head.startswith(SQLITE_MAGIC):  # a database of the backups folder
        upload.replace(pending / DATABASE)
        images = False
    elif head.startswith(ZIP_MAGIC):
        images = _extract(upload, pending)
        upload.unlink()
    else:
        raise NotALibraryError(
            "Ce fichier n'est ni un export de Video Frame Expedition ni une de ses bases de "
            "données."
        )
    videos = _check(pending / DATABASE)
    plan = PendingOut(
        action="import", library=True, settings=not keep_settings, images=images,
        videos=videos, source=name, prepared_at=datetime.now(UTC),
    )  # fmt: skip
    (pending / PLAN).write_text(plan.model_dump_json(), encoding="utf-8")
    return plan


def _extract(upload: Path, pending: Path) -> bool:
    """The database and the frames of an export (nothing else, nowhere else). True when it
    brings frames."""
    try:
        with zipfile.ZipFile(upload) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if DATABASE not in {info.filename for info in entries}:
                raise NotALibraryError(
                    "Cette archive n'est pas un export de Video Frame Expedition : elle ne "
                    "contient pas de base de données."
                )
            needed = sum(info.file_size for info in entries)
            if needed > shutil.disk_usage(pending).free - SPARE_BYTES:
                raise VfeError(
                    f"Pas assez de place sur le disque pour cet import ({needed / 2**30:.1f} Go)."
                )
            images = False
            for info in entries:
                if info.filename == DATABASE:
                    target = pending / DATABASE
                elif info.filename.startswith(f"{MEDIA}/"):
                    inside = joined_inside(pending / MEDIA, info.filename.removeprefix(f"{MEDIA}/"))
                    if inside is None:
                        raise NotALibraryError(f"Chemin refusé dans l'archive : {info.filename}")
                    target, images = inside, True
                else:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out, COPY_CHUNK)
            return images
    except zipfile.BadZipFile as exc:
        raise NotALibraryError(f"Archive illisible : {exc}") from exc


def _check(database: Path) -> int:
    """A database of this application, sound, of a version this one knows: its videos."""
    try:
        with contextlib.closing(sqlite3.connect(database)) as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise NotALibraryError("Cette base de données est abîmée.")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            if not {"alembic_version", "videos", "settings"} <= tables:
                raise NotALibraryError(
                    "Cette base de données n'est pas celle de Video Frame Expedition."
                )
            revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            videos: int = conn.execute("SELECT count(*) FROM videos").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise NotALibraryError(f"Base de données illisible : {exc}") from exc
    scripts = ScriptDirectory.from_config(alembic_config(database))
    if revision is None or revision[0] not in {r.revision for r in scripts.walk_revisions()}:
        raise NewerLibraryError(
            "Cette bibliothèque vient d'une version plus récente de l'application : mettez "
            "d'abord celle-ci à jour (« Video Frame Expedition - mise à jour »)."
        )
    return videos


# ---------------------------------------------------------------------- at the start


def apply_pending(settings: Settings) -> None:
    """At the start, before anything opens the database: the import or the reset prepared from
    the interface, once the current database is copied to the backups. What it did (or why it
    could not) is kept for the System page."""
    data = settings.data_dir
    _clear(data / TRASH)
    _clear(data / DOWNLOADS)
    pending = data / PENDING
    plan_path = pending / PLAN
    if not plan_path.is_file():
        _clear(pending)  # an upload cut short
        return
    try:
        plan = PendingOut.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        log.exception("unreadable import or reset, dropped", path=str(plan_path))
        _clear(pending)
        return
    log.info(
        "carrying out a prepared import or reset", action=plan.action, library=plan.library,
        settings=plan.settings, images=plan.images,
    )  # fmt: skip
    backup: Path | None = None
    error: str | None = None
    try:
        backup = _backup(settings, plan.action)
        _carry_out(settings, plan, pending, backup)
    except Exception as exc:  # the data stays as it was, or as the copy in the backups
        log.exception("prepared import or reset failed", action=plan.action)
        error = _reason(exc)
    last = LastOperationOut(
        **plan.model_dump(exclude={"prepared_at"}), done_at=datetime.now(UTC),
        backup=backup.name if backup else None, error=error,
    )  # fmt: skip
    (data / LAST).write_text(last.model_dump_json(), encoding="utf-8")
    _clear(pending)
    _clear(data / TRASH)


def _carry_out(settings: Settings, plan: PendingOut, pending: Path, backup: Path | None) -> None:
    if plan.action == "reset" and not plan.library:  # the settings only
        with contextlib.closing(sqlite3.connect(settings.db_path)) as conn, conn:
            conn.execute("DELETE FROM settings")
        return
    database = pending / DATABASE  # imported, or (reset) a new one, empty
    command.upgrade(alembic_config(database), "head")
    if not plan.settings and backup is not None:
        _copy_settings(backup, database)
    _replace_database(settings.db_path, database)
    if plan.action == "reset" or plan.images:
        _replace_media(settings, pending / MEDIA)


def _backup(settings: Settings, action: str) -> Path | None:
    """The current database, whole, with the backups; its journal emptied into it."""
    if not settings.db_path.exists():
        return None
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    revision = current_revision(settings.db_path) or "none"
    target = settings.backups_dir / f"vfe-{stamp}-{revision}-before-{action}.sqlite3"
    with contextlib.closing(sqlite3.connect(settings.db_path)) as conn:
        conn.execute("VACUUM INTO ?", (str(target),))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info("database backed up", before=action, backup=str(target))
    return target


def _copy_settings(source: Path, target: Path) -> None:
    with contextlib.closing(sqlite3.connect(target)) as conn:
        conn.execute("ATTACH DATABASE ? AS kept", (str(source),))
        with conn:
            conn.execute("DELETE FROM settings")
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) "
                "SELECT key, value, updated_at FROM kept.settings"
            )
        conn.execute("DETACH DATABASE kept")


def _replace_database(current: Path, new: Path) -> None:
    # The journal of the old database must not be read as the new one's: emptied into it by
    # the backup, it goes first (the replacement fails before it, if the file is in use).
    for suffix in ("-wal", "-shm"):
        Path(f"{current}{suffix}").unlink(missing_ok=True)
    new.replace(current)


def _replace_media(settings: Settings, incoming: Path) -> None:
    media = settings.artifacts_dir
    trash = settings.data_dir / TRASH
    trash.mkdir(parents=True, exist_ok=True)
    if media.exists():
        media.replace(trash / MEDIA)
    if incoming.is_dir():
        incoming.replace(media)
    media.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------- helpers


@contextlib.contextmanager
def _held() -> Iterator[None]:
    if not _busy.acquire(blocking=False):
        raise ConflictError("Un export, un import ou une réinitialisation est déjà en préparation.")
    try:
        yield
    finally:
        _busy.release()


def _snapshot(database: Path, target: Path) -> None:
    """A consistent copy of the database while the application writes to it."""
    with contextlib.closing(sqlite3.connect(database)) as conn:
        conn.execute("VACUUM INTO ?", (str(target),))


def _files(folder: Path) -> Iterator[Path]:
    if not folder.is_dir():
        return
    for directory, _, names in os.walk(folder):
        for name in names:
            yield Path(directory, name)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read[T: BaseModel](path: Path, model: type[T]) -> T | None:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _clear(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _reason(exc: BaseException) -> str:
    if isinstance(exc, VfeError):
        return exc.detail
    if isinstance(exc, OSError) and exc.strerror:
        return f"{exc.strerror} ({exc.filename})" if exc.filename else exc.strerror
    return str(exc) or type(exc).__name__
