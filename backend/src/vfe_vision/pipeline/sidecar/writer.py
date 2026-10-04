"""Write the analysis files next to a video: after each analysis, and on demand.

One file per language: ``<stem>_FR.txt`` in the language the analyses were written in,
and the other once they are translated, each with every text in its language. The single file
of earlier versions (``<stem>.txt``) is removed once they are written, when it is ours.

A file is replaced atomically (a temporary file in the same folder, then a rename that waits
out an antivirus lock). A file of that name that the application did not write is never
replaced, and no folder is ever created. A read-only folder, a refused access or a full disk
is a result to report, never an exception for the caller.
"""

from __future__ import annotations

import errno
import glob
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from vfe_vision import __version__
from vfe_vision.core.atomic_io import atomic_write_bytes
from vfe_vision.db.models import StageRun, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.session import Database
from vfe_vision.db.timeline_bins import links_digest, resolve_links
from vfe_vision.db.translations import dictionary_for
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.resolve_timeline import link_to_json
from vfe_vision.domain.sidecar import (
    FORMAT,
    FORMAT_VERSION,
    MAX_BYTES,
    SUFFIX,
    SidecarStatus,
    document_texts,
    is_ours,
    iso_utc,
    language_suffix,
    localized,
    other_name,
    parse,
    render,
    sidecar_name,
    without_folder,
    written_language,
)
from vfe_vision.domain.translation import LANGUAGES, other_language
from vfe_vision.pipeline.runner import latest_runs
from vfe_vision.pipeline.sidecar.tables import OWNER, TABLES, dump, dump_run, dump_video, rows_of
from vfe_vision.pipeline.stages import default_registry

CONFLICT_DETAIL = "un fichier de ce nom, que l'application n'a pas écrit, est laissé tel quel"


@dataclass(frozen=True, slots=True)
class SidecarResult:
    video_id: str
    status: SidecarStatus
    path: Path | None = None  # the file left as it was, or the first one written
    detail: str | None = None  # why it could not be written
    paths: tuple[Path, ...] = ()  # every file written, one per language


def sidecar_path(video: Path, language: str | None = None) -> Path:
    """Where the analysis file of ``video`` in ``language`` goes (``<stem>_FR.txt``, see
    ``domain.sidecar.sidecar_name``); no language: the single file of earlier versions."""
    siblings = [p.name for p in video.parent.glob(f"{glob.escape(video.stem)}.*")]
    suffix = SUFFIX if language is None else language_suffix(language)
    return video.with_name(sidecar_name(video.name, siblings, suffix))


def spellings(video: Path, language: str | None = None) -> tuple[Path, Path]:
    """The name the rule gives now, then its other spelling (the folder's videos may have
    changed since the file was written)."""
    chosen = sidecar_path(video, language)
    suffix = SUFFIX if language is None else language_suffix(language)
    return chosen, chosen.with_name(other_name(video.name, chosen.name, suffix))


def read_document(path: Path) -> Any:
    """The JSON value of an existing file; None when it cannot be one of ours (a folder, too
    big to be ours, not JSON)."""
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        return None
    return parse(path.read_bytes())


def build_document(session: Session, video: Video) -> dict[str, Any] | None:
    """Everything known about the video, without images or absolute paths; None while it has
    no finished analysis (nothing worth a file)."""
    runs = latest_runs(session, video.id)
    if not any(run.status == StageStatus.SUCCEEDED for run in runs):
        return None
    facts, user = dump_video(video)
    # A success whose result is not in the file (the viewing copy, the search index) would
    # claim a result the import cannot give back: the job makes it again.
    rebuilt = {stage.name for stage in default_registry().plan() if stage.rebuilt_after_import}
    runs = [r for r in runs if not (r.stage in rebuilt and r.status == StageStatus.SUCCEEDED)]
    document: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": iso_utc(datetime.now(UTC)),
        "video": facts,
        "user": user,
        "stage_runs": [dump_run(run) for run in runs],
    }
    for table in TABLES:
        left_out = table.left_out | {OWNER}
        rows = [dump(table.model, row, left_out) for row in rows_of(session, table, video.id)]
        document[table.key] = rows if table.many else (rows[0] if rows else None)
    # The DaVinci Resolve timelines using the video: kept for whoever reads the file
    # (Claude, another tool), left aside by the import (adding the timeline brings them back).
    links = resolve_links(session, [video.path_key]).get(video.path_key, [])
    document["resolve"] = [link_to_json(link) for link in links]
    # ffprobe and ExifTool name the file by its absolute path: only its name stays.
    cleaned: dict[str, Any] = without_folder(document, str(Path(video.path).parent))
    return cleaned


def write_sidecar(db: Database, video_id: str) -> SidecarResult:
    """Write (or refresh) the analysis files of a video now: the one in the language its
    analyses were written in, and the other once they were translated (or when it is there)."""
    default = load_preferences(db).language
    with db.read() as session:
        video = session.get(Video, video_id)
        if video is None:
            return SidecarResult(video_id, SidecarStatus.UNKNOWN)
        path = Path(video.path)
        if not path.is_file():
            return SidecarResult(video_id, SidecarStatus.OFFLINE)
        document = build_document(session, video)
        links = links_digest(resolve_links(session, [video.path_key]).get(video.path_key, ()))
        if document is None:
            return SidecarResult(video_id, SidecarStatus.NOT_ANALYZED)
        written = written_language(document, default)
        texts = document_texts(document)
        dictionaries = {
            language: dictionary_for(session, language, texts) for language in {written, *LANGUAGES}
        }
        translated_now = _translated(session, video_id)
    target: Path | None = None
    done: list[Path] = []
    conflict: Path | None = None
    try:
        languages = [written]
        if written in LANGUAGES:
            other = other_language(written)
            if translated_now or is_ours(read_document(sidecar_path(path, other))):
                languages.append(other)
        for language in languages:
            target = sidecar_path(path, language)
            if target.exists() and not is_ours(read_document(target)):
                conflict = conflict or target
                continue
            text = render(localized(document, dictionaries[language]))
            atomic_write_bytes(target, text.encode("utf-8"), create_parents=False)
            done.append(target)
    except OSError as exc:
        return SidecarResult(video_id, SidecarStatus.FAILED, target, failure(exc), tuple(done))
    if conflict is None:
        _remove_single_file(path)
    if done:
        with db.write() as session:  # what the files now say of its timelines
            session.execute(
                sa.update(Video)
                .where(Video.id == video_id)
                .values(sidecar_links=links, updated_at=Video.updated_at)
            )
    if conflict is not None:
        return SidecarResult(
            video_id, SidecarStatus.CONFLICT, conflict, CONFLICT_DETAIL, tuple(done)
        )
    return SidecarResult(video_id, SidecarStatus.WRITTEN, done[0], paths=tuple(done))


def _translated(session: Session, video_id: str) -> bool:
    """The video's texts were translated (its latest translation run succeeded)."""
    status = session.execute(
        sa.select(StageRun.status)
        .where(
            StageRun.video_id == video_id,
            StageRun.stage == "translation",
            StageRun.status != StageStatus.CACHED,
        )
        .order_by(StageRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return status == StageStatus.SUCCEEDED


def _remove_single_file(video: Path) -> None:
    """The single file of earlier versions (``<stem>.txt``), replaced by the files of each
    language: removed when ours, left alone otherwise (or when it cannot be)."""
    for candidate in spellings(video):
        try:
            if candidate.is_file() and is_ours(read_document(candidate)):
                candidate.unlink()
        except OSError:
            continue


def failure(exc: OSError) -> str:
    """Why a file could not be written, for the user."""
    if exc.errno == errno.ENOSPC:
        return "disque plein"
    if isinstance(exc, PermissionError) or exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}:
        return "écriture refusée (dossier en lecture seule ou accès refusé)"
    if isinstance(exc, FileNotFoundError):
        return "dossier introuvable (déplacé ou disque déconnecté)"
    return exc.strerror or str(exc)
