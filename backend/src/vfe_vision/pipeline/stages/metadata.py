"""Stage ``metadata``: camera, GPS point and track, capture time (ExifTool + sidecars)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from timezonefinder import TimezoneFinder

from vfe_vision.adapters.exiftool.normalize import NormalizedMetadata, TrackSample, normalize
from vfe_vision.adapters.exiftool.sidecars import find_sidecars, parse_dji_srt, parse_gpx
from vfe_vision.core.errors import ExternalToolError
from vfe_vision.db.models import GpsPoint, Video, VideoMetadata
from vfe_vision.domain.capture_time import (
    Candidate,
    CaptureTime,
    Kind,
    parse_filename_date,
    resolve_capture_time,
    zone_offset_min,
)
from vfe_vision.domain.geo import GeoPoint, parse_iso6709, track_stats
from vfe_vision.domain.media import is_editing_software
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage

MAX_TRACK_POINTS = 2000
# Capture rate this far from the playback rate means slow motion or time-lapse.
SPEED_CHANGE = 0.1
MAX_RECORDING_S = 7 * 24 * 3600  # longer "durations" come from corrupted headers


@lru_cache(maxsize=1)
def _timezones() -> TimezoneFinder:
    return TimezoneFinder(in_memory=True)


def timezone_at(point: GeoPoint | None) -> str | None:
    if point is None:
        return None
    return _timezones().timezone_at(lat=point.latitude, lng=point.longitude)


def _downsample(samples: list[TrackSample], limit: int) -> list[TrackSample]:
    if len(samples) <= limit:
        return samples
    step = len(samples) / limit
    return [samples[int(i * step)] for i in range(limit)]


def _sidecar_signature(video: Path) -> list[list[Any]]:
    signature: list[list[Any]] = []
    for suffix, path in sorted(find_sidecars(video).items()):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append([suffix, path.name, stat.st_size, int(stat.st_mtime)])
    return signature


def real_duration(
    duration_s: float | None, fps: float | None, capture_fps: float | None
) -> float | None:
    """Wall-clock length of the recording: a 240 fps slow motion played at 30 fps lasted 8×
    less than the file; a time-lapse lasted longer."""
    if duration_s is not None and not 0 < duration_s <= MAX_RECORDING_S:
        return None
    if not duration_s or not fps or not capture_fps:
        return duration_s
    if abs(capture_fps / fps - 1) <= SPEED_CHANGE:
        return duration_s
    return duration_s * fps / capture_fps


def display_model(model: str | None, model_name: str | None) -> str | None:
    """``Galaxy S26 Ultra (SM-S948B)`` when the device wrote both."""
    if model_name and model and model_name != model:
        return f"{model_name} ({model})"
    return model_name or model


@dataclass(slots=True)
class _Findings:
    meta: NormalizedMetadata
    raw: dict[str, Any]
    point: GeoPoint | None
    location_source: str | None
    timezone: str | None
    capture: CaptureTime
    track: list[TrackSample]
    normalized: dict[str, Any] = field(default_factory=dict)


class MetadataStage(SyncStage):
    name = "metadata"
    version = 3
    family = StageFamily.FILE
    requires = ("probe",)
    optional = True  # the analysis stays useful without ExifTool

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        v = ctx.video
        return {
            "clock_offset": v.root_clock_offset_s,
            "timezone": v.root_timezone,
            "default_location": [v.root_latitude, v.root_longitude],
            # Inputs besides the file content: its name (dates) and its sidecar files.
            "filename": v.filename,
            "sidecars": _sidecar_signature(v.path),
        }

    def run(self, ctx: StageContext) -> StageOutcome:
        try:
            raw = ctx.tools.exiftool.read(ctx.video.path)
        except ExternalToolError as exc:
            return StageOutcome.skipped(f"ExifTool indisponible : {exc.detail}", retryable=True)
        findings = self._analyse(ctx, raw)
        self._persist(ctx, findings)
        meta, capture, point = findings.meta, findings.capture, findings.point
        return StageOutcome.ok(
            location=None
            if point is None
            else [round(point.latitude, 5), round(point.longitude, 5)],
            location_source=findings.location_source,
            timezone=findings.timezone,
            capture=capture.utc.isoformat() if capture.utc else None,
            capture_source=capture.source,
            capture_confidence=capture.confidence.value,
            utc_offset_min=capture.utc_offset_min,
            track_points=len(findings.track),
            camera=" ".join(x for x in (meta.make, display_model(meta.model, meta.model_name)) if x)
            or None,
            warnings=findings.normalized["warnings"],
        )

    def _analyse(self, ctx: StageContext, raw: dict[str, Any]) -> _Findings:
        meta = normalize(raw)
        with ctx.tools.db.read() as session:
            video = session.get_one(Video, ctx.video.id)
            encoder, fps, duration = video.encoder, video.fps, video.duration_s
            stored = session.get(VideoMetadata, ctx.video.id)
            probe: dict[str, Any] = ((stored.normalized or {}).get("probe") or {}) if stored else {}
        # ffprobe reads the same Android keys: fill what ExifTool (older versions) missed.
        meta.os_version = meta.os_version or (
            f"Android {probe['android_version']}" if probe.get("android_version") else None
        )
        meta.container_dates_at_stop = meta.container_dates_at_stop or bool(
            probe.get("android_version")
        )
        meta.capture_fps = meta.capture_fps or probe.get("capture_fps")
        meta.utc_offset_min = meta.utc_offset_min if meta.utc_offset_min is not None else (
            probe.get("utc_offset_min")
        )  # fmt: skip

        track = list(meta.track)
        sidecar_settings: dict[str, float] = {}
        sidecars = find_sidecars(ctx.video.path)
        if not track and ".srt" in sidecars:
            track, sidecar_settings = parse_dji_srt(sidecars[".srt"])
        if not track and ".gpx" in sidecars:
            track = parse_gpx(sidecars[".gpx"])

        point, location_source = meta.point, meta.point_source
        if point is None and probe.get("location"):
            point, location_source = parse_iso6709(probe["location"]), "probe:location"
        if point is None and track:
            point, location_source = track[0].point, "track"
        if (
            point is None
            and ctx.video.root_latitude is not None
            and ctx.video.root_longitude is not None
        ):
            point = GeoPoint(ctx.video.root_latitude, ctx.video.root_longitude)
            location_source = "folder_default"
        if point is None:
            location_source = None
        timezone = timezone_at(point) or ctx.video.root_timezone

        exported = (
            is_editing_software(encoder)
            or is_editing_software(meta.software)
            or meta.reencoded_by is not None
        )
        candidates = list(meta.dates)
        from_name = parse_filename_date(ctx.video.filename)
        if from_name is not None:
            candidates.append(from_name)
        first_local = next(
            (s.utc for s in track if s.utc is not None and s.utc.tzinfo is None), None
        )
        if first_local is not None:  # DJI telemetry: local wall-clock time
            candidates.append(Candidate(first_local, Kind.CAMERA_LOCAL, "sidecar:srt"))
        nominal = probe.get("nominal_fps") or fps
        recording = real_duration(meta.duration_s or duration, nominal, meta.capture_fps)
        capture = resolve_capture_time(
            candidates,
            timezone=timezone,
            exported_by_editor=exported,
            clock_offset_s=ctx.video.root_clock_offset_s,
            duration_s=recording,
            container_at_stop=meta.container_dates_at_stop,
            utc_offset_min=meta.utc_offset_min,
        )
        findings = _Findings(meta, raw, point, location_source, timezone, capture, track)
        findings.normalized = self._normalized(
            findings,
            sidecars=sorted(sidecars),
            sidecar_settings=sidecar_settings,
            exported=exported,
            recording_s=recording,
        )
        return findings

    @staticmethod
    def _normalized(
        findings: _Findings,
        *,
        sidecars: list[str],
        sidecar_settings: dict[str, float],
        exported: bool,
        recording_s: float | None,
    ) -> dict[str, Any]:
        capture, meta = findings.capture, findings.meta
        warnings: list[str] = list(capture.warnings)
        zone_offset = (
            zone_offset_min(findings.timezone, capture.utc) if capture.utc is not None else None
        )
        if (
            meta.utc_offset_min is not None
            and zone_offset is not None
            and zone_offset != meta.utc_offset_min
        ):
            # The phone clock and the place disagree (travel, manual clock): UTC stays exact,
            # local times are shown in the place's zone.
            warnings.append("utc_offset_mismatch")
        stats = track_stats([s.point for s in findings.track])
        local = capture.local
        return {
            **meta.as_dict(),
            "sidecars": sidecars,
            "sidecar_settings": sidecar_settings,
            "exported_by_editor": exported,
            "timezone": findings.timezone,
            "track": None if stats is None else {
                "points": stats.points, "distance_m": round(stats.distance_m, 1),
                "bbox": [stats.min_lat, stats.min_lon, stats.max_lat, stats.max_lon],
                "altitude_range_m": [stats.min_altitude_m, stats.max_altitude_m],
            },
            "capture": {
                "utc": capture.utc.isoformat() if capture.utc else None,
                "local": local.isoformat() if local else None,
                "utc_offset_min": capture.utc_offset_min,
                "source": capture.source, "confidence": capture.confidence.value,
                "recording_s": round(recording_s, 3) if recording_s else None,
            },
            "warnings": warnings,
        }  # fmt: skip

    @staticmethod
    def _persist(ctx: StageContext, findings: _Findings) -> None:
        # Embedded track samples (Doc1…DocN) are stored in gps_points, not duplicated here.
        compact = {k: v for k, v in findings.raw.items() if not k.startswith("Doc")}
        meta, capture, point = findings.meta, findings.capture, findings.point
        normalized = findings.normalized
        with ctx.tools.db.write() as session:
            video = session.get_one(Video, ctx.video.id)
            video.camera_make = meta.make
            video.camera_model = display_model(meta.model, meta.model_name)
            video.camera_os = meta.os_version
            video.color_profile = meta.color_profile
            video.capture_fps = meta.capture_fps or video.capture_fps
            video.latitude = point.latitude if point else None
            video.longitude = point.longitude if point else None
            video.altitude_m = point.altitude_m if point else None
            video.location_source = findings.location_source
            video.captured_at = capture.utc
            video.captured_at_source = capture.source
            video.captured_at_confidence = capture.confidence.value
            video.capture_timezone = capture.timezone or findings.timezone
            video.capture_utc_offset_min = capture.utc_offset_min
            stored = session.get(VideoMetadata, ctx.video.id) or VideoMetadata(
                video_id=ctx.video.id
            )
            stored.exif = compact
            stored.normalized = {**(stored.normalized or {}), "exif": normalized}
            session.add(stored)
            session.execute(sa.delete(GpsPoint).where(GpsPoint.video_id == ctx.video.id))
            clock = timedelta(seconds=ctx.video.root_clock_offset_s or 0)
            for sample in _downsample(findings.track, MAX_TRACK_POINTS):
                utc = sample.utc
                if utc is not None and utc.tzinfo is None:  # camera wall clock (DJI .SRT)
                    local = capture.local
                    utc = (
                        (utc + clock).replace(tzinfo=local.tzinfo).astimezone(UTC)
                        if local
                        else None
                    )
                session.add(
                    GpsPoint(
                        video_id=ctx.video.id,
                        t_s=sample.t_s,
                        utc=utc,
                        latitude=sample.point.latitude,
                        longitude=sample.point.longitude,
                        altitude_m=sample.point.altitude_m,
                        speed_mps=sample.speed_mps,
                        source="exif" if not normalized["sidecars"] else "sidecar",
                    )
                )
