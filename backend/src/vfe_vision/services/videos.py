"""Queries and annotations on analysed videos."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, selectinload

from vfe_vision.core.errors import InvalidInputError, NotFoundError, PathNotAllowedError
from vfe_vision.core.paths import is_within, path_key
from vfe_vision.db.models import (
    AudioStats,
    ContextPlace,
    ContextSun,
    ContextWeather,
    FrameAnalysis,
    GpsPoint,
    Keyframe,
    LibraryRoot,
    Shot,
    ShotStory,
    StageRun,
    Video,
    VideoMetadata,
    VideoSignals,
)
from vfe_vision.db.timeline_bins import bin_positions, bins_of, resolve_links
from vfe_vision.domain.enums import Orientation, StageStatus, VideoStatus
from vfe_vision.domain.resolve_timeline import ResolveLink
from vfe_vision.pipeline.runner import is_reusable, latest_runs
from vfe_vision.pipeline.stages import default_registry
from vfe_vision.pipeline.stages.proxy import PROXY_DIR, PROXY_FILE
from vfe_vision.services.audio_text import StageState, stage_state
from vfe_vision.services.container import AppContainer
from vfe_vision.services.context import same_minute

_EDITABLE = {"title", "rating", "favorite", "user_notes", "transcript_mode", "transcript_language"}


@dataclass(frozen=True, slots=True)
class VideoFilters:
    q: str | None = None
    status: tuple[VideoStatus, ...] = ()
    root_id: str | None = None
    folder: str | None = None  # a bin: videos directly in this folder of ``root_id``
    timeline_bin_id: str | None = None  # the videos of a Resolve timeline
    orientation: Orientation | None = None
    favorite: bool | None = None
    light_phase: tuple[str, ...] = ()
    limit: int = 60
    offset: int = 0
    sort: str | None = None  # "recent" by default; a timeline's videos: in timeline order


@dataclass(frozen=True, slots=True)
class ContextBrief:
    """What a library card shows of the capture context (current results only)."""

    place: str | None = None  # locality (or the first part of the label)
    place_approximate: bool = False
    light_phase: str | None = None
    weather_category: str | None = None
    temperature_c: float | None = None


@dataclass(frozen=True, slots=True)
class VideoPage:
    items: list[Video]
    total: int
    briefs: dict[str, ContextBrief] = field(default_factory=dict)
    timeline_bins: dict[str, list[str]] = field(default_factory=dict)  # by video id


def context_briefs(session: Session, videos: list[Video]) -> dict[str, ContextBrief]:
    ids = [video.id for video in videos]
    if not ids:
        return {}
    places = {
        row.video_id: row
        for row in session.execute(
            sa.select(ContextPlace).where(ContextPlace.video_id.in_(ids))
        ).scalars()
    }
    suns = {
        row.video_id: row
        for row in session.execute(
            sa.select(ContextSun).where(ContextSun.video_id.in_(ids))
        ).scalars()
    }
    weathers = {
        row.video_id: row
        for row in session.execute(
            sa.select(ContextWeather).where(ContextWeather.video_id.in_(ids))
        ).scalars()
    }
    briefs: dict[str, ContextBrief] = {}
    for video in videos:
        place = places.get(video.id)
        sun = suns.get(video.id)
        weather = weathers.get(video.id)
        # Rows computed for another capture time are not shown (as in the context view).
        sun = sun if sun and same_minute(sun.at_utc, video.captured_at) else None
        weather = weather if weather and same_minute(weather.at_utc, video.captured_at) else None
        if place is None and sun is None and weather is None:
            continue
        label = place.locality or (place.label or "").split(",")[0].strip() if place else None
        briefs[video.id] = ContextBrief(
            place=label or None,
            place_approximate=bool(place and (place.data or {}).get("approximate")),
            light_phase=sun.light_phase if sun else None,
            weather_category=weather.category if weather else None,
            temperature_c=weather.temperature_c if weather else None,
        )
    return briefs


@dataclass(frozen=True, slots=True)
class KeyframeView:
    keyframe: Keyframe
    analysis: FrameAnalysis | None


@dataclass(frozen=True, slots=True)
class AnalysisGaps:
    """What an ordinary ("complete") analysis would do, and results from older stage versions."""

    missing: list[str] = field(default_factory=list)  # never run, failed, degraded, skipped for now
    outdated: list[str] = field(default_factory=list)  # kept, but made by an older version
    # Skipped for a reason other than the video itself (online services off, model missing…),
    # or provisional (made without a setting: offline place, index without its model): an
    # ordinary analysis checks them again. Skips due to the video (no position) are not here.
    skipped: list[str] = field(default_factory=list)


def analysis_gaps(runs: list[StageRun]) -> AnalysisGaps:
    latest = {run.stage: run for run in runs}
    gaps = AnalysisGaps()
    for stage in default_registry().plan():
        run = latest.get(stage.name)
        if run is None or not is_reusable(run.status, run.cache_key, run.summary):
            gaps.missing.append(stage.name)
            continue
        if run.stage_version != stage.version:
            gaps.outdated.append(stage.name)
        summary = run.summary or {}
        if (run.status == StageStatus.SKIPPED and not summary.get("permanent")) or summary.get(
            "provisional"
        ):
            gaps.skipped.append(stage.name)
    return gaps


@dataclass(frozen=True, slots=True)
class VideoDetail:
    video: Video
    root: LibraryRoot
    stages: list[StageRun] = field(default_factory=list)
    keyframe_count: int = 0
    analysed_count: int = 0
    brief: ContextBrief | None = None
    pix_fmt: str | None = None
    has_proxy: bool = False
    resolve: list[ResolveLink] = field(default_factory=list)  # the timelines using it

    @property
    def gaps(self) -> AnalysisGaps:
        return analysis_gaps(self.stages)


def list_videos(c: AppContainer, filters: VideoFilters) -> VideoPage:
    stmt = sa.select(Video)
    orders: dict[str, tuple[Any, ...]] = {
        "recent": (Video.created_at.desc(),),
        "name": (Video.filename.asc(),),
        "duration": (Video.duration_s.desc().nulls_last(),),
        "captured": (Video.captured_at.desc().nulls_last(),),
    }
    if filters.timeline_bin_id is not None:
        if filters.root_id or filters.folder is not None:
            raise InvalidInputError("Une timeline se choisit seule, sans dossier.")
        positions = bin_positions(filters.timeline_bin_id)
        stmt = stmt.join(positions, positions.c.key == Video.path_key)
        orders["timeline"] = (positions.c.position, Video.filename.asc())
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        stmt = stmt.where(
            sa.or_(
                Video.filename.ilike(pattern),
                Video.rel_path.ilike(pattern),
                Video.title.ilike(pattern),
                Video.summary.ilike(pattern),
            )
        )
    if filters.status:
        stmt = stmt.where(Video.status.in_(filters.status))
    if filters.root_id:
        stmt = stmt.where(Video.root_id == filters.root_id)
    if filters.folder is not None:
        if not filters.root_id:
            raise InvalidInputError("Un dossier se choisit dans un dossier racine (root_id).")
        folder = filters.folder.strip("/")
        if folder:  # its own videos, not those of its sub-folders (a bin)
            prefix = folder + "/"
            rest = sa.func.substr(Video.rel_path, len(prefix) + 1)
            stmt = stmt.where(
                Video.rel_path.istartswith(prefix, autoescape=True),
                sa.func.instr(rest, "/") == 0,
            )
        else:
            stmt = stmt.where(sa.func.instr(Video.rel_path, "/") == 0)
    if filters.orientation:
        stmt = stmt.where(Video.orientation == filters.orientation)
    if filters.favorite is not None:
        stmt = stmt.where(Video.favorite.is_(filters.favorite))
    if filters.light_phase:
        minute = "%Y-%m-%d %H:%M"
        stmt = stmt.where(
            sa.exists().where(
                ContextSun.video_id == Video.id,
                ContextSun.light_phase.in_(filters.light_phase),
                sa.func.strftime(minute, ContextSun.at_utc)
                == sa.func.strftime(minute, Video.captured_at),
            )
        )
    order = orders.get(filters.sort or ("timeline" if filters.timeline_bin_id else "recent"))
    if order is None:
        raise InvalidInputError(f"Tri inconnu : {filters.sort}")
    with c.db.read() as session:
        total = session.execute(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        ).scalar_one()
        items = list(
            session.execute(
                stmt.order_by(*order).limit(filters.limit).offset(filters.offset)
            ).scalars()
        )
        briefs = context_briefs(session, items)
        in_bins = bins_of(session, [video.path_key for video in items])
    return VideoPage(
        items=items,
        total=total,
        briefs=briefs,
        timeline_bins={v.id: in_bins[v.path_key] for v in items if v.path_key in in_bins},
    )


def get_video(c: AppContainer, video_id: str) -> VideoDetail:
    with c.db.read() as session:
        video = session.get(Video, video_id)
        if video is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        root = session.get_one(LibraryRoot, video.root_id)
        stages = latest_runs(session, video_id)
        keyframe_count = session.execute(
            sa.select(sa.func.count()).where(Keyframe.video_id == video_id)
        ).scalar_one()
        analysed = session.execute(
            sa.select(sa.func.count())
            .select_from(FrameAnalysis)
            .join(Keyframe, FrameAnalysis.keyframe_id == Keyframe.id)
            .where(Keyframe.video_id == video_id)
        ).scalar_one()
        brief = context_briefs(session, [video]).get(video.id)
        meta = session.get(VideoMetadata, video_id)
        probe = ((meta.normalized or {}) if meta else {}).get("probe") or {}
        links = resolve_links(session, [video.path_key]).get(video.path_key, [])
    return VideoDetail(
        video,
        root,
        stages,
        keyframe_count,
        analysed,
        brief,
        pix_fmt=probe.get("pix_fmt"),
        has_proxy=proxy_file(c, video_id) is not None,
        resolve=links,
    )


def gaps_for(c: AppContainer, video_id: str) -> AnalysisGaps:
    with c.db.read() as session:
        return analysis_gaps(latest_runs(session, video_id))


def get_keyframes(c: AppContainer, video_id: str) -> list[KeyframeView]:
    with c.db.read() as session:
        if session.get(Video, video_id) is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        rows = session.execute(
            sa.select(Keyframe)
            .options(selectinload(Keyframe.analysis))
            .where(Keyframe.video_id == video_id)
            .order_by(Keyframe.idx)
        ).scalars()
        return [KeyframeView(k, k.analysis) for k in rows]


def find_by_path(c: AppContainer, path: Path) -> Video | None:
    with c.db.read() as session:
        return session.execute(
            sa.select(Video).where(Video.path_key == path_key(path))
        ).scalar_one_or_none()


def update_video(c: AppContainer, video_id: str, patch: dict[str, Any]) -> Video:
    unknown = set(patch) - _EDITABLE
    if unknown:
        raise InvalidInputError(f"Champs non modifiables : {', '.join(sorted(unknown))}")
    if "rating" in patch and patch["rating"] is not None and not 0 <= patch["rating"] <= 5:
        raise InvalidInputError("La note doit être comprise entre 0 et 5.")
    with c.db.write() as session:
        video = session.get(Video, video_id)
        if video is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        for key, value in patch.items():
            setattr(video, key, value)
        return video


def proxy_file(c: AppContainer, video_id: str) -> Path | None:
    """The viewing copy made for formats the browser cannot play, when it exists."""
    path = c.artifacts.video_dir(video_id) / PROXY_DIR / PROXY_FILE
    return path if path.is_file() else None


def source_file(c: AppContainer, video_id: str) -> Path:
    """Path of the original file, checked to lie inside its declared root (streaming)."""
    detail = get_video(c, video_id)
    path = Path(detail.video.path)
    if not is_within(path, detail.root.path):
        raise PathNotAllowedError("Le fichier n'est plus dans son dossier de bibliothèque.")
    if not path.is_file():
        raise NotFoundError("Le fichier vidéo est introuvable (déplacé ou disque déconnecté).")
    return path


@dataclass(frozen=True, slots=True)
class SignalsView:
    visual: dict[str, Any] | None
    audio: dict[str, Any] | None
    audio_stats: AudioStats | None


def get_shots(c: AppContainer, video_id: str) -> list[Shot]:
    with c.db.read() as session:
        if session.get(Video, video_id) is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        return list(
            session.execute(
                sa.select(Shot).where(Shot.video_id == video_id).order_by(Shot.idx)
            ).scalars()
        )


def get_shot_stories(c: AppContainer, video_id: str) -> dict[str, list[ShotStory]]:
    """What happens in each shot (``vision_shots``), by shot id, parts in order."""
    with c.db.read() as session:
        rows = session.execute(
            sa.select(ShotStory)
            .where(ShotStory.video_id == video_id)
            .order_by(ShotStory.start_s, ShotStory.part)
        ).scalars()
        stories: dict[str, list[ShotStory]] = {}
        for row in rows:
            stories.setdefault(row.shot_id, []).append(row)
        return stories


@dataclass(frozen=True, slots=True)
class ShotsView:
    shots: list[Shot]
    stories: dict[str, list[ShotStory]]  # by shot id
    state: StageState  # of the stage that tells them


def get_shots_view(c: AppContainer, video_id: str) -> ShotsView:
    shots = get_shots(c, video_id)
    with c.db.read() as session:
        state = stage_state(session, video_id, "vision_shots")
    return ShotsView(shots, get_shot_stories(c, video_id), state)


def get_signals(c: AppContainer, video_id: str) -> SignalsView:
    with c.db.read() as session:
        if session.get(Video, video_id) is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        signals = session.get(VideoSignals, video_id)
        stats = session.get(AudioStats, video_id)
        return SignalsView(
            visual=signals.visual if signals else None,
            audio=signals.audio if signals else None,
            audio_stats=stats,
        )


def get_track(c: AppContainer, video_id: str) -> list[GpsPoint]:
    with c.db.read() as session:
        if session.get(Video, video_id) is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        return list(
            session.execute(
                sa.select(GpsPoint).where(GpsPoint.video_id == video_id).order_by(GpsPoint.id)
            ).scalars()
        )


def get_metadata(c: AppContainer, video_id: str) -> VideoMetadata | None:
    with c.db.read() as session:
        return session.get(VideoMetadata, video_id)
