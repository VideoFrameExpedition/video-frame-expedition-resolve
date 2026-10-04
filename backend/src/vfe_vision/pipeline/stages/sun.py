"""Stage ``sun``: sun position, light phase, events and theoretical light (local computation).

Needs a position and a capture time that is at least probable. With a probable time, the phase
is only stated when it is the same over ± 30 min; a long take reports the phases it crosses.
The weather (when available) tells whether the sun was out, which picks the expected colour
temperature range; without it, a clear sky is assumed and said so.
"""

from __future__ import annotations

import contextlib
import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa

from vfe_vision.db.models import ContextSun, ContextWeather
from vfe_vision.domain.enums import Confidence
from vfe_vision.domain.sun import (
    CIVIL,
    CULMINATION_HOUR_ANGLE,
    MAX_YEAR,
    MEDIUM_WINDOW,
    MIN_YEAR,
    LightPhase,
    SkyRegime,
    SunEvents,
    TwilightPhase,
    moon_phase,
    phase_window,
    sky_regime,
    sun_at,
    sun_events,
    sun_up_seconds,
    theoretical_light,
    twilight_phase,
)
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage
from vfe_vision.pipeline.stages.capture_facts import CaptureFacts, read_capture_facts

SCHEMA_VERSION = 1
LONG_TAKE = timedelta(minutes=5)
_TWILIGHT_SIDE = {LightPhase.GOLDEN_HOUR, LightPhase.BLUE_HOUR}


def _zone(facts: CaptureFacts) -> tzinfo:
    if facts.timezone:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(facts.timezone)
    if facts.utc_offset_min is not None:
        return timezone(timedelta(minutes=facts.utc_offset_min))
    return UTC


@dataclass(frozen=True, slots=True)
class _Weather:
    sunshine_s: float | None
    diffuse_fraction: float | None
    cloud_cover_pct: float | None


def _weather_inputs(ctx: StageContext, facts: CaptureFacts) -> _Weather | None:
    """The weather row, only when it describes this very capture minute (never a stale one)."""
    if facts.utc is None:
        return None
    with ctx.tools.db.read() as session:
        row = session.get(ContextWeather, ctx.video.id)
        if row is None or _minute(row.at_utc) != _minute(facts.utc):
            return None
        values = row.data.get("values") or {}
        return _Weather(
            sunshine_s=values.get("sunshine_s"),
            diffuse_fraction=row.data.get("diffuse_fraction"),
            cloud_cover_pct=values.get("cloud_cover_pct"),
        )


def _minute(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _events(events: SunEvents) -> dict[str, Any]:
    return {
        "local_date": events.local_date.isoformat(),
        "rising": {k: _iso(v) for k, v in events.rising.items()},
        "setting": {k: _iso(v) for k, v in events.setting.items()},
        "solar_noon": _iso(events.solar_noon),
        "polar": events.polar,
    }


class SunStage(SyncStage):
    name = "sun"
    version = 1
    family = StageFamily.CONTEXT
    after = ("metadata", "weather")  # capture facts; whether the sun was out
    optional = True

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        facts = read_capture_facts(ctx)
        weather = _weather_inputs(ctx, facts)
        return {
            **facts.time_key(),
            "point": facts.point_key(3),
            "timezone": facts.timezone,
            "utc_offset_min": facts.utc_offset_min,
            "duration_s": None if facts.duration_s is None else round(facts.duration_s),
            # Exact inputs of the sky regime: any weather revision recomputes the light.
            "weather": None if weather is None else dataclasses.astuple(weather),
        }

    def run(self, ctx: StageContext) -> StageOutcome:
        facts = read_capture_facts(ctx)
        if facts.point is None:
            _clear(ctx)
            return StageOutcome.skipped("Position de tournage inconnue", permanent=True)
        if facts.utc is None or not facts.time_is_usable:
            _clear(ctx)
            return StageOutcome.skipped(
                "Heure de tournage trop incertaine pour le soleil", permanent=True
            )
        if not MIN_YEAR <= facts.utc.year <= MAX_YEAR:
            _clear(ctx)
            return StageOutcome.skipped(
                "Date de tournage hors de la plage de calcul (1900–2100)", permanent=True
            )
        utc, lat, lon = facts.utc, facts.point.latitude, facts.point.longitude
        at = sun_at(utc, lat, lon)
        certain = facts.confidence == Confidence.HIGH
        window = None
        if not certain:
            window = phase_window(utc - MEDIUM_WINDOW, utc + MEDIUM_WINDOW, lat, lon)
        take = None
        if facts.duration_s and timedelta(seconds=facts.duration_s) > LONG_TAKE:
            take = phase_window(utc, utc + timedelta(seconds=facts.duration_s), lat, lon)
        phase = at.light_phase if window is None else window.single_phase
        official: TwilightPhase | None = at.twilight_phase
        if window is not None and twilight_phase(window.elevation_min) != twilight_phase(
            window.elevation_max
        ):
            official = None
        weather = _weather_inputs(ctx, facts)
        cover_end = utc.replace(minute=0, second=0, microsecond=0)
        if cover_end != utc:
            cover_end += timedelta(hours=1)
        regime = sky_regime(
            at.position.elevation,
            sunshine_s=None if weather is None else weather.sunshine_s,
            sun_up_s=sun_up_seconds(cover_end, lat, lon),
            diffuse_fraction=None if weather is None else weather.diffuse_fraction,
            cloud_cover_pct=None if weather is None else weather.cloud_cover_pct,
        )
        light = theoretical_light(at.position.elevation, regime)
        # A take crossing phases has no single expected light: no measured-vs-theory verdict.
        comparable = (
            light is not None and not light.off_locus and (take is None or len(take.phases) == 1)
        )
        moon = moon_phase(utc)
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "apparent_elevation_deg": round(at.position.apparent_elevation, 2),
            "hour_angle_deg": round(at.position.hour_angle, 2),
            "rate_deg_per_h": round(at.rate_deg_per_h, 2),
            "direction": at.direction,
            "near_culmination": at.near_culmination,
            "phase_at_instant": at.light_phase.value,
            "time_confidence": None if facts.confidence is None else facts.confidence.value,
            "window": None
            if window is None
            else {
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "elevation_min": round(window.elevation_min, 2),
                "elevation_max": round(window.elevation_max, 2),
                "phases": [p.value for p in window.phases],
            },
            "take": None
            if take is None
            else {
                "end": take.end.isoformat(),
                "elevation_end": round(sun_at(take.end, lat, lon).position.elevation, 2),
                "phases": [p.value for p in take.phases],
            },
            "events": _events(sun_events(utc, lat, lon, _zone(facts))),
            "light": None
            if light is None
            else {
                "regime": light.regime.value,
                "direct_k": light.direct_k,
                "ambient_k": light.ambient_k,
                "off_locus": light.off_locus,
                "nominal_k": light.nominal_k,
                "comparable": comparable,
            },
            "moon": {"illuminated": round(moon.illuminated, 3), "waxing": moon.waxing},
            "assumptions": ["level_horizon"]
            + (["clear_sky"] if regime == SkyRegime.CLEAR_ASSUMED else [])
            + (["capture_start"] if take is not None else []),
        }
        with ctx.tools.db.write() as session:
            row = session.get(ContextSun, ctx.video.id) or ContextSun(video_id=ctx.video.id)
            row.at_utc = utc
            row.elevation_deg = round(at.position.elevation, 3)
            row.azimuth_deg = round(at.position.azimuth, 2)
            row.light_phase = None if phase is None else phase.value
            row.twilight_phase = None if official is None else official.value
            row.day_part = _day_part(at.position.elevation, at.position.hour_angle, phase)
            # Twilight light is off the Planckian locus: no single kelvin is claimed.
            row.theoretical_cct_k = None if light is None or light.off_locus else light.nominal_k
            row.data = data
            session.add(row)
        return StageOutcome.ok(
            light_phase=None if phase is None else phase.value,
            elevation_deg=round(at.position.elevation, 1),
            regime=regime.value,
        )


def _day_part(elevation: float, hour_angle: float, phase: LightPhase | None) -> str | None:
    """morning / midday / afternoon / evening; nothing at night or when the phase is unsure."""
    if phase is None or elevation < CIVIL:
        return None
    if phase == LightPhase.DAY and abs(hour_angle) < CULMINATION_HOUR_ANGLE:
        return "midday"
    if hour_angle < 0:
        return "morning"
    return "evening" if phase in _TWILIGHT_SIDE else "afternoon"


def _clear(ctx: StageContext) -> None:
    with ctx.tools.db.write() as session:
        session.execute(sa.delete(ContextSun).where(ContextSun.video_id == ctx.video.id))
