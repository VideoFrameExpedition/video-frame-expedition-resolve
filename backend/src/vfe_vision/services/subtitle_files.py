"""A video's subtitles written next to it, when « Create a timeline » adds the timeline
to DaVinci Resolve with subtitles: ``<stem>_FR.srt`` for what is said (in the language spoken),
``<stem>_SHOTS_EN.srt`` for what each shot shows (in the language of the timeline), the language
at the end of the name (``<name>.<ext>_FR.srt``… when another video of the folder has
the same stem, as for the analysis files). Resolve on another computer that sees the
folder imports them from there; a player shows them with the video. The files named without a
language by earlier versions are removed when replaced, if they still hold what was written.

A file of that name is replaced only when the application wrote it and it still holds what was
written (``subtitle_files`` keeps its SHA-256): any other is left as it is, and said. Written
atomically, no folder ever created; a read-only folder, a refused access or a full disk is a
result to report, never an exception for the caller.
"""

from __future__ import annotations

import glob
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import sqlalchemy as sa

from vfe_vision.core.atomic_io import atomic_write_bytes
from vfe_vision.core.paths import path_key
from vfe_vision.db.base import utcnow
from vfe_vision.db.models import SubtitleFile
from vfe_vision.db.session import Database
from vfe_vision.domain.sidecar import sidecar_name
from vfe_vision.domain.timeline_files import SubtitleTrack
from vfe_vision.domain.transcript import to_srt
from vfe_vision.domain.translation import file_suffix
from vfe_vision.pipeline.sidecar.writer import failure

SUFFIXES = {"transcript": "", "shots": "_SHOTS"}  # after the video's stem, before the language
MAX_BYTES = 64 * 1024 * 1024  # a bigger file is not one of ours: never read whole
CONFLICT_DETAIL = (
    "un fichier de ce nom, que l'application n'a pas écrit ou qui a changé depuis, est laissé "
    "tel quel"
)


class WriteStatus(StrEnum):
    WRITTEN = "written"
    CONFLICT = "conflict"  # a file of that name that is not ours as we wrote it: left alone
    FAILED = "failed"  # read-only folder, access denied, disk full, folder gone…


@dataclass(frozen=True, slots=True)
class WrittenSubtitles:
    video_id: str
    part: str  # transcript | shots
    path: Path  # on this computer: the file written, or the one left as it was
    status: WriteStatus
    detail: str | None = None  # why it was not written


def subtitle_path(video: Path, part: str, language: str | None = None) -> Path:
    """Where the subtitle file ``part`` of ``video`` in ``language`` goes (no language: the
    name earlier versions gave it, or a transcript whose language is not known)."""
    siblings = [p.name for p in video.parent.glob(f"{glob.escape(video.stem)}.*")]
    spoken = f"_{file_suffix(language)}" if language else ""
    return video.with_name(sidecar_name(video.name, siblings, f"{SUFFIXES[part]}{spoken}.srt"))


def write_subtitles(
    db: Database, video_id: str, video: Path, track: SubtitleTrack
) -> WrittenSubtitles:
    """Write (or refresh) one subtitle file of a video, its cues in the video's own time."""
    target = subtitle_path(video, track.part, track.language)
    data = to_srt(track.cues).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    key = path_key(target)
    with db.read() as session:
        record = session.get(SubtitleFile, key)
        written = record.sha256 if record is not None else None
    try:
        if target.exists():
            if written is None or _digest(target) != written:
                return WrittenSubtitles(
                    video_id, track.part, target, WriteStatus.CONFLICT, CONFLICT_DETAIL
                )
            if written == digest:  # holds what it would be written with
                _remove_unnamed(db, video, track.part, target)
                return WrittenSubtitles(video_id, track.part, target, WriteStatus.WRITTEN)
        atomic_write_bytes(target, data, create_parents=False)
    except OSError as exc:
        return WrittenSubtitles(video_id, track.part, target, WriteStatus.FAILED, failure(exc))
    with db.write() as session:
        session.merge(
            SubtitleFile(
                path_key=key, path=str(target), video_id=video_id, sha256=digest,
                written_at=utcnow(),
            )
        )  # fmt: skip
    _remove_unnamed(db, video, track.part, target)
    return WrittenSubtitles(video_id, track.part, target, WriteStatus.WRITTEN)


def _remove_unnamed(db: Database, video: Path, part: str, written: Path) -> None:
    """The file of ``part`` an earlier version named without a language (``<stem>.srt``,
    ``<stem>_SHOTS.srt``), replaced by ``written``: removed while it holds exactly what the
    application wrote there, left alone otherwise."""
    old = subtitle_path(video, part)
    if old == written:
        return
    key = path_key(old)
    with db.read() as session:
        record = session.get(SubtitleFile, key)
    try:
        if record is None or not old.is_file() or _digest(old) != record.sha256:
            return
        old.unlink()
    except OSError:
        return
    with db.write() as session:
        session.execute(sa.delete(SubtitleFile).where(SubtitleFile.path_key == key))


def _digest(path: Path) -> str | None:
    if path.stat().st_size > MAX_BYTES:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()
