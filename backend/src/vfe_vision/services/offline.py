"""Offline videos: their files are gone from where the library knew them. Take them out of
the library, or relink them to their files moved elsewhere (« Relink… », a job)."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import sqlalchemy as sa

from vfe_vision.core.errors import InvalidInputError, NotFoundError, PathNotAllowedError
from vfe_vision.core.paths import is_within
from vfe_vision.db.models import Job, Video
from vfe_vision.domain.enums import JobKind, JobStatus, VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.jobs.roots import DATA_DIR, unlist
from vfe_vision.services.container import AppContainer

RELINK_PRIORITY = 10  # as a folder scan: the user waits for it
MAX_VIDEOS = 500
NONE_OFFLINE = "Aucune de ces vidéos n'est hors ligne : rien à relier."


def forget_videos(c: AppContainer, video_ids: Collection[str]) -> tuple[int, int]:
    """Take offline videos out of the library with their analyses (their files are gone: nothing
    is written anywhere). Videos still on the disk, or being analysed now, are left. Returns
    (taken out, left)."""
    ids = _ids(video_ids)
    with c.db.write() as session:
        running = set(
            session.execute(
                sa.select(Job.video_id).where(
                    Job.status == JobStatus.RUNNING, Job.video_id.in_(ids)
                )
            ).scalars()
        )
        videos = [
            video
            for video in session.execute(
                sa.select(Video).where(Video.id.in_(ids), Video.status == VideoStatus.OFFLINE)
            ).scalars()
            if video.id not in running
        ]
        for video in videos:
            root_id, name = video.root_id, video.filename
            session.delete(video)  # its analyses and its jobs go with it (foreign keys)
            session.flush()
            unlist(session, root_id, name)
    for video in videos:
        c.artifacts.delete_video(video.id)
    return len(videos), len(ids) - len(videos)


def request_relink(c: AppContainer, video_ids: Collection[str], folder: str) -> Job:
    """Queue the search of these offline videos' files in ``folder`` (sub-folders included)."""
    ids = _ids(video_ids)
    place = Path(folder)
    if not place.is_absolute() or not place.is_dir():
        raise NotFoundError(f"Dossier introuvable : {folder}")
    if is_within(place, c.settings.data_dir):
        raise PathNotAllowedError(DATA_DIR)
    with c.db.read() as session:
        offline = list(
            session.execute(
                sa.select(Video.id).where(Video.id.in_(ids), Video.status == VideoStatus.OFFLINE)
            ).scalars()
        )
    if not offline:
        raise InvalidInputError(NONE_OFFLINE)
    return queue.enqueue(
        c.db, JobKind.RELINK_VIDEOS, payload={"video_ids": offline, "folder": str(place)},
        priority=RELINK_PRIORITY,
    )  # fmt: skip


def _ids(video_ids: Collection[str]) -> list[str]:
    ids = list(dict.fromkeys(video_ids))
    if not ids:
        raise InvalidInputError("Sélectionnez au moins une vidéo.")
    if len(ids) > MAX_VIDEOS:
        raise InvalidInputError(f"Au plus {MAX_VIDEOS} vidéos à la fois.")
    return ids
