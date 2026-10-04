"""The analysis file next to each video around an analysis job.

Imported first when the video has no analysis yet; written again when the job ends (succeeded
or partial), unless the user turned it off. Neither ever fails the job: what happened is
logged and told by an event (``sidecar.imported``, ``sidecar.ignored``, ``sidecar.written``,
``sidecar.conflict``, ``sidecar.failed``).
"""

from __future__ import annotations

import structlog

from vfe_vision.core.errors import CancelledError
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.session import Database
from vfe_vision.domain.sidecar import SidecarStatus
from vfe_vision.pipeline.registry import StageRegistry
from vfe_vision.pipeline.runner import EventSink
from vfe_vision.pipeline.sidecar.reader import ImportReport, import_sidecar
from vfe_vision.pipeline.sidecar.writer import SidecarResult, write_sidecar
from vfe_vision.pipeline.stage import StageContext

_UNEXPECTED = "erreur inattendue (voir le journal)"


def import_first(
    ctx: StageContext, registry: StageRegistry, events: EventSink, *, job_id: str | None
) -> ImportReport | None:
    """Import the video's analysis file before the stages run, when it has no analysis yet."""
    try:
        report = import_sidecar(ctx, registry)
    except CancelledError:
        raise
    except Exception:  # the analysis goes on as if there were no file
        ctx.log.exception("analysis file import crashed")
        events.emit(
            "sidecar.ignored", job_id=job_id, video_id=ctx.video.id, data={"reason": _UNEXPECTED}
        )
        return None
    if report is None:
        return None
    name = report.path.name
    if report.imported:
        ctx.log.info(
            "analysis file imported",
            file=name,
            keyframes=report.keyframes,
            stages=report.stages,
            fresh_ids=report.fresh_ids,
        )
        events.emit(
            "sidecar.imported",
            job_id=job_id,
            video_id=ctx.video.id,
            data={"file": name, "keyframes": report.keyframes, "stages": report.stages},
        )
    else:
        ctx.log.warning("analysis file left aside", file=name, reason=report.reason)
        events.emit(
            "sidecar.ignored",
            job_id=job_id,
            video_id=ctx.video.id,
            data={"file": name, "reason": report.reason},
        )
    return report


def write_after(
    db: Database,
    video_id: str,
    events: EventSink,
    *,
    job_id: str | None,
    log: structlog.stdlib.BoundLogger,
) -> SidecarResult | None:
    """Write the video's analysis file once its analysis ended (None: turned off)."""
    try:
        if not load_preferences(db).sidecar_files:
            return None
        result = write_sidecar(db, video_id)
    except Exception:  # the analysis itself is done: it stays done
        log.exception("analysis file not written")
        events.emit("sidecar.failed", job_id=job_id, video_id=video_id, data={"error": _UNEXPECTED})
        return None
    name = result.path.name if result.path is not None else None
    if result.status == SidecarStatus.WRITTEN:
        files = [path.name for path in result.paths]
        events.emit(
            "sidecar.written",
            job_id=job_id,
            video_id=video_id,
            data={"file": name, "files": files},
        )
    elif result.status == SidecarStatus.CONFLICT:
        log.warning("analysis file left as it is", file=name, reason=result.detail)
        events.emit("sidecar.conflict", job_id=job_id, video_id=video_id, data={"file": name})
    elif result.status == SidecarStatus.FAILED:
        log.warning("analysis file not written", file=name, error=result.detail)
        events.emit(
            "sidecar.failed",
            job_id=job_id,
            video_id=video_id,
            data={"file": name, "error": result.detail},
        )
    return result
