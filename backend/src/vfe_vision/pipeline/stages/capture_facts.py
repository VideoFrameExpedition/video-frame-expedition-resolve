"""Capture facts produced by ``metadata`` and read by the context stages (place, sun, weather).

Context stages only *follow* ``metadata`` (``after``): the facts they use go into their cache
configuration, so a better capture-time rule that moves the time by a few seconds does not redo
a network call, while a new position or a new day does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vfe_vision.db.models import Video
from vfe_vision.domain.enums import Confidence
from vfe_vision.domain.geo import GeoPoint
from vfe_vision.pipeline.stage import StageContext


@dataclass(frozen=True, slots=True)
class CaptureFacts:
    utc: datetime | None
    confidence: Confidence | None
    timezone: str | None
    utc_offset_min: int | None
    point: GeoPoint | None
    location_source: str | None
    duration_s: float | None

    @property
    def time_is_usable(self) -> bool:
        """Sun and weather claims need a capture time that is at least probable."""
        return self.utc is not None and self.confidence in {Confidence.HIGH, Confidence.MEDIUM}

    def time_key(self) -> dict[str, Any]:
        """Capture time rounded to the minute, and its confidence (cache configuration)."""
        return {
            "at": None if self.utc is None else self.utc.replace(second=0, microsecond=0),
            "confidence": None if self.confidence is None else self.confidence.value,
        }

    def point_key(self, decimals: int) -> list[float] | None:
        if self.point is None:
            return None
        return [round(self.point.latitude, decimals), round(self.point.longitude, decimals)]


def read_capture_facts(ctx: StageContext) -> CaptureFacts:
    with ctx.tools.db.read() as session:
        video = session.get_one(Video, ctx.video.id)
        point = None
        if video.latitude is not None and video.longitude is not None:
            point = GeoPoint(video.latitude, video.longitude, video.altitude_m)
        confidence = video.captured_at_confidence
        return CaptureFacts(
            utc=video.captured_at,
            confidence=Confidence(confidence) if confidence in set(Confidence) else None,
            timezone=video.capture_timezone,
            utc_offset_min=video.capture_utc_offset_min,
            point=point,
            location_source=video.location_source,
            duration_s=video.duration_s,
        )
