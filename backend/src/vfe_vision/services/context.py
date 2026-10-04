"""Capture context of a video: place, model weather, sun, and measured vs. theoretical light."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa

from vfe_vision.core.errors import NotFoundError
from vfe_vision.db.models import (
    ContextPlace,
    ContextSun,
    ContextWeather,
    Keyframe,
    StageRun,
    Video,
)
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.sun import (
    SkyRegime,
    TheoreticalLight,
    compare_with_measured,
    elevation_curve,
)
from vfe_vision.domain.translation import AS_WRITTEN, Dictionary
from vfe_vision.services.container import AppContainer
from vfe_vision.services.reading import texts_in

CONTEXT_STAGES = ("place", "weather", "sun")
STALE_NOTE = "Heure de tournage modifiée depuis le calcul : relancez l'analyse"


CURVE_STEP_MIN = 10


def same_minute(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return False
    return a.replace(second=0, microsecond=0) == b.replace(second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class SunCurve:
    start_utc: datetime  # local midnight of the capture day
    step_min: int
    elevations_deg: list[float]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def local_midnight(at: datetime, video: Video) -> datetime:
    """Midnight of the capture's local day, as a UTC instant (known offset, zone, else solar)."""
    at = _utc(at)
    offset: timedelta | None = None
    if video.capture_utc_offset_min is not None:
        offset = timedelta(minutes=video.capture_utc_offset_min)
    elif video.capture_timezone:
        try:
            offset = at.astimezone(ZoneInfo(video.capture_timezone)).utcoffset()
        except (ZoneInfoNotFoundError, ValueError):
            offset = None
    if offset is None:
        offset = timedelta(hours=round((video.longitude or 0.0) / 15))
    local = at + offset
    return datetime(local.year, local.month, local.day, tzinfo=UTC) - offset


def sun_curve(video: Video, sun: ContextSun | None) -> SunCurve | None:
    """Sun elevation through the capture day (computed on request: no analysis needed)."""
    if sun is None or video.latitude is None or video.longitude is None:
        return None
    start = local_midnight(sun.at_utc, video)
    values = elevation_curve(start, video.latitude, video.longitude, step_min=CURVE_STEP_MIN)
    return SunCurve(start_utc=start, step_min=CURVE_STEP_MIN, elevations_deg=values)


@dataclass(frozen=True, slots=True)
class ContextView:
    video: Video
    place: ContextPlace | None
    weather: ContextWeather | None
    sun: ContextSun | None
    online_services: bool
    measured_cct_k: int | None = None
    light_comparison: str | None = None  # consistent | warmer | cooler
    notes: dict[str, str] = field(default_factory=dict)  # stage → why it has no result
    sun_curve: SunCurve | None = None
    texts: Dictionary = AS_WRITTEN  # the place names in the language asked for


def _theoretical(sun: ContextSun | None) -> TheoreticalLight | None:
    light = sun.data.get("light") if sun else None
    if not isinstance(light, dict) or light.get("off_locus") or not light.get("comparable", True):
        return None  # twilight, or a take crossing phases: no single expected light
    try:
        direct = light.get("direct_k")
        return TheoreticalLight(
            regime=SkyRegime(light["regime"]),
            direct_k=(int(direct[0]), int(direct[1])) if direct else None,
            ambient_k=(int(light["ambient_k"][0]), int(light["ambient_k"][1])),
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def get_context(c: AppContainer, video_id: str, *, tr: Dictionary | None = None) -> ContextView:
    prefs = load_preferences(c.db)
    with c.db.read() as session:
        video = session.get(Video, video_id)
        if video is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
        sun = session.get(ContextSun, video_id)
        metrics: Iterable[dict[str, Any] | None] = session.execute(
            sa.select(Keyframe.metrics).where(
                Keyframe.video_id == video_id, Keyframe.duplicate_of.is_(None)
            )
        ).scalars()
        measured = [m["cct_k"] for m in metrics if m and isinstance(m.get("cct_k"), int | float)]
        runs = session.execute(
            sa.select(StageRun)
            .where(StageRun.video_id == video_id, StageRun.stage.in_(CONTEXT_STAGES))
            .where(StageRun.status != StageStatus.CACHED)
            .order_by(StageRun.created_at)
        ).scalars()
        notes: dict[str, str] = {}
        for run in runs:  # the latest run of each stage wins
            notes.pop(run.stage, None)
            degraded = run.status == StageStatus.SUCCEEDED and (run.summary or {}).get("retryable")
            if run.status in {StageStatus.SKIPPED, StageStatus.FAILED} or degraded:
                notes[run.stage] = run.skip_reason or run.error or run.status.value
        weather = session.get(ContextWeather, video_id)
        # Rows computed for another capture time (the time was re-dated since) are not shown.
        if weather is not None and not same_minute(weather.at_utc, video.captured_at):
            weather = None
            notes["weather"] = STALE_NOTE
        if sun is not None and not same_minute(sun.at_utc, video.captured_at):
            sun = None
            notes["sun"] = STALE_NOTE
        median = round(statistics.median(measured)) if measured else None
        light = _theoretical(sun)
        return ContextView(
            video=video,
            place=session.get(ContextPlace, video_id),
            weather=weather,
            sun=sun,
            online_services=prefs.online_services,
            measured_cct_k=median,
            light_comparison=compare_with_measured(median, light) if median and light else None,
            notes=notes,
            sun_curve=sun_curve(video, sun),
            texts=texts_in(c) if tr is None else tr,
        )
