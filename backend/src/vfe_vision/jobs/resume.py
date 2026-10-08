"""Analyses left waiting for LM Studio, completed on their own once it answers.

A stage that needs the model in LM Studio is skipped when LM Studio does not answer or has no
model loaded: a new installation still aimed at a local LM Studio, the other computer asleep, a
model unloaded. Its run is marked ``waits_for: lmstudio`` and the video stays « partielle ».
The worker looks for such videos regularly and, when there are some and a vision model answers,
queues a « Compléter » analysis for them: what was left waiting runs, nothing else is redone.
Each waiting run is resumed once per worker, so a stage that keeps skipping never loops.
"""

from __future__ import annotations

from collections.abc import Collection

import sqlalchemy as sa

from vfe_vision.adapters.lmstudio.catalog import pick_vision_instance
from vfe_vision.adapters.lmstudio.client import LmStudioClient
from vfe_vision.core.errors import VfeError
from vfe_vision.db.models import StageRun, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import AnalysisMode, JobKind, StageStatus, VideoStatus
from vfe_vision.jobs import queue
from vfe_vision.pipeline.stage import WAITS_FOR_LMSTUDIO

RESUME_INTERVAL_S = 30.0  # how often the worker looks: soon after LM Studio is set or started
RESUME_PRIORITY = 150  # after the analyses asked for


def waiting_videos(db: Database, *, besides: Collection[str] = ()) -> dict[str, list[str]]:
    """The videos whose latest run of a stage waits for LM Studio, with those runs' ids (except
    ``besides``). Videos out of reach, or with an analysis already queued or running, are left
    out: that analysis will do it."""
    waits = StageRun.summary["waits_for"].as_string() == WAITS_FOR_LMSTUDIO
    with db.read() as session:
        if session.execute(sa.select(StageRun.id).where(waits).limit(1)).first() is None:
            return {}  # the usual case, without grouping every run
        latest = (
            sa.select(
                StageRun.video_id,
                StageRun.stage,
                sa.func.max(StageRun.created_at).label("at"),
            )
            .where(StageRun.status != StageStatus.CACHED)
            .group_by(StageRun.video_id, StageRun.stage)
            .subquery()
        )
        rows = session.execute(
            sa.select(StageRun.id, StageRun.video_id)
            .join(
                latest,
                sa.and_(
                    StageRun.video_id == latest.c.video_id,
                    StageRun.stage == latest.c.stage,
                    StageRun.created_at == latest.c.at,
                ),
            )
            .join(Video, Video.id == StageRun.video_id)
            .where(StageRun.status == StageStatus.SKIPPED, waits)
            .where(Video.status != VideoStatus.OFFLINE)
        ).all()
        waiting: dict[str, list[str]] = {}
        for run_id, video_id in rows:
            if run_id not in besides:
                waiting.setdefault(video_id, []).append(run_id)
        busy = queue.active_analysis(session, waiting)
    return {video_id: ids for video_id, ids in waiting.items() if video_id not in busy}


async def model_answers(db: Database, lmstudio: LmStudioClient) -> bool:
    """Whether the stages would now find their model: the same choice as theirs, a vision
    model already loaded (never one LM Studio would have to load)."""
    try:
        models = await lmstudio.list_models()
    except VfeError:  # unreachable, or an unexpected answer
        return False
    return pick_vision_instance(models, load_preferences(db).vision_model) is not None


def queue_completions(db: Database, video_ids: Collection[str]) -> int:
    """A « Compléter » analysis for each of these videos; returns how many were queued."""
    payload = {"mode": AnalysisMode.COMPLETE.value, "force": False, "refresh": False}
    with db.write() as session:
        for video_id in video_ids:
            queue.enqueue_in(
                session,
                JobKind.ANALYZE_VIDEO,
                video_id=video_id,
                payload=dict(payload),
                priority=RESUME_PRIORITY,
            )
    return len(video_ids)
