"""Normalise ExifTool output (``-json -G1 -n -ee3``) into facts the pipeline can use."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from vfe_vision.domain.capture_time import (
    Candidate,
    Kind,
    parse_utc_offset,
    records_local_time,
)
from vfe_vision.domain.devices import samsung_model_name
from vfe_vision.domain.geo import GeoPoint, parse_iso6709

_DATE_RE = re.compile(
    r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)
_DOC_RE = re.compile(r"^Doc(\d+):(?:[^:]+:)?(.+)$")  # Doc1:Track2:GPSLatitude

LOG_PROFILES = {
    "d-log m": "d-log-m", "d-log": "d-log", "s-log3": "s-log3", "s-log2": "s-log2",
    "v-log": "v-log", "c-log": "c-log", "f-log": "f-log", "n-log": "n-log",
    "apple log": "apple-log", "hlg": "hlg", "gopro log": "protune-flat", "flat": "flat",
}  # fmt: skip


@dataclass(slots=True)
class TrackSample:
    t_s: float | None
    utc: datetime | None
    point: GeoPoint
    speed_mps: float | None = None


@dataclass(slots=True)
class NormalizedMetadata:
    make: str | None = None
    model: str | None = None
    software: str | None = None
    lens: str | None = None
    iso: float | None = None
    f_number: float | None = None
    exposure_time: float | None = None
    focal_length_mm: float | None = None
    point: GeoPoint | None = None
    point_source: str | None = None
    track: list[TrackSample] = field(default_factory=list)
    dates: list[Candidate] = field(default_factory=list)
    color_profile: str | None = None
    reencoded_by: str | None = None  # a transcoder that leaves no Software tag
    model_name: str | None = None  # marketing name written by the device ("Galaxy S26 Ultra")
    os_version: str | None = None  # "Android 16"
    capture_fps: float | None = None  # frames captured per second (slow motion > playback fps)
    utc_offset_min: int | None = None  # device offset tag (Samsung Keys:AndroidTimeZone)
    duration_s: float | None = None
    # AOSP MPEG4Writer writes the container dates when the file is finalised (end of recording).
    container_dates_at_stop: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "make": self.make, "model": self.model, "software": self.software, "lens": self.lens,
            "iso": self.iso, "f_number": self.f_number, "exposure_time": self.exposure_time,
            "focal_length_mm": self.focal_length_mm,
            "point": None if self.point is None else {
                "lat": self.point.latitude, "lon": self.point.longitude,
                "alt": self.point.altitude_m, "source": self.point_source,
            },
            "track_points": len(self.track),
            "dates": [{"value": c.value.isoformat(), "kind": c.kind.name, "source": c.source}
                      for c in self.dates],
            "color_profile": self.color_profile,
            "reencoded_by": self.reencoded_by,
            "model_name": self.model_name, "os_version": self.os_version,
            "capture_fps": self.capture_fps, "utc_offset_min": self.utc_offset_min,
            "duration_s": self.duration_s,
            "container_dates_at_stop": self.container_dates_at_stop,
        }  # fmt: skip


def parse_exif_date(value: Any) -> datetime | None:
    """``2025:07:14 18:32:10+02:00`` → aware datetime; without offset → naive."""
    if not isinstance(value, str):
        return None
    match = _DATE_RE.match(value.strip())
    if match is None:
        return None
    y, mo, d, h, mi, s, frac, tz = match.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))  # noqa: DTZ001 - naive by design
    except ValueError:
        return None
    if frac:
        dt += timedelta(seconds=float(frac))
    if tz == "Z":
        return dt.replace(tzinfo=UTC)
    if tz:
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        if offset >= timedelta(hours=24):
            return None
        return dt.replace(tzinfo=timezone(sign * offset))
    return dt


def _first(data: dict[str, Any], suffixes: tuple[str, ...]) -> Any:
    for key, value in data.items():
        if key.startswith("Doc"):
            continue
        tag = key.split(":", 1)[-1]
        if tag in suffixes and value not in (None, "", "Unknown"):
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point(data: dict[str, Any]) -> tuple[GeoPoint | None, str | None]:
    lat, lon = _float(data.get("Composite:GPSLatitude")), _float(data.get("Composite:GPSLongitude"))
    if lat is not None and lon is not None:
        try:
            point = GeoPoint(lat, lon, _float(data.get("Composite:GPSAltitude")))
        except ValueError:
            point = None
        if point is not None and not point.is_null_island:
            return point, "exif:composite"
    for key, value in data.items():
        if key.endswith(":GPSCoordinates") and isinstance(value, str) and not key.startswith("Doc"):
            parsed = parse_iso6709(value)
            if parsed is None:  # "-n" output: "lat lon alt"
                parts = value.split()
                if len(parts) >= 2 and all(_float(p) is not None for p in parts[:2]):
                    try:
                        parsed = GeoPoint(float(parts[0]), float(parts[1]),
                                          _float(parts[2]) if len(parts) > 2 else None)  # fmt: skip
                    except ValueError:
                        parsed = None
            if parsed is not None and not parsed.is_null_island:
                return parsed, f"exif:{key}"
        if key.endswith(":xyz") and isinstance(value, str):
            parsed = parse_iso6709(value)
            if parsed is not None:
                return parsed, "exif:xyz"
    return None, None


def _track(data: dict[str, Any]) -> list[TrackSample]:
    docs: dict[int, dict[str, Any]] = {}
    for key, value in data.items():
        match = _DOC_RE.match(key)
        if match:
            docs.setdefault(int(match.group(1)), {})[match.group(2)] = value
    samples: list[TrackSample] = []
    for _, doc in sorted(docs.items()):
        lat, lon = _float(doc.get("GPSLatitude")), _float(doc.get("GPSLongitude"))
        if lat is None or lon is None:
            continue
        try:
            point = GeoPoint(lat, lon, _float(doc.get("GPSAltitude")))
        except ValueError:
            continue
        if point.is_null_island:
            continue
        utc = parse_exif_date(doc.get("GPSDateTime"))
        speed = _float(doc.get("GPSSpeed"))
        samples.append(
            TrackSample(
                t_s=_float(doc.get("SampleTime")),
                utc=utc if utc and utc.tzinfo else (utc.replace(tzinfo=UTC) if utc else None),
                point=point,
                speed_mps=speed / 3.6 if speed is not None else None,  # km/h → m/s
            )
        )
    return samples


def _dates(data: dict[str, Any], make: str | None) -> list[Candidate]:
    local_camera = records_local_time(make)
    candidates: list[Candidate] = []
    for key, value in data.items():
        if key.startswith("Doc"):
            continue
        tag = key.split(":", 1)[-1]
        parsed = parse_exif_date(value)
        if parsed is None:
            continue
        if tag == "GPSDateTime":
            aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            candidates.append(Candidate(aware, Kind.GPS_UTC, key))
        elif tag in {"CreationDate", "DateTimeOriginal", "CreateDate", "MediaCreateDate",
                     "TrackCreateDate", "DateTimeDigitized"}:  # fmt: skip
            if parsed.tzinfo is not None:
                candidates.append(Candidate(parsed, Kind.WITH_OFFSET, key))
            elif key.startswith(("QuickTime:", "Track")) and tag in {
                "CreateDate",
                "MediaCreateDate",
                "TrackCreateDate",
            }:
                if local_camera:
                    candidates.append(Candidate(parsed, Kind.CAMERA_LOCAL, key))
                else:
                    candidates.append(
                        Candidate(parsed.replace(tzinfo=UTC), Kind.CONTAINER_UTC, key)
                    )
            else:
                candidates.append(Candidate(parsed, Kind.CAMERA_LOCAL, key))
        elif key == "System:FileModifyDate":
            aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            candidates.append(Candidate(aware, Kind.FILE_SYSTEM, key))
    track_start = next((s.utc for s in _track(data) if s.utc), None)
    if track_start is not None:
        candidates.append(Candidate(track_start, Kind.GPS_UTC, "track:GPSDateTime"))
    return candidates


def _color_profile(data: dict[str, Any]) -> str | None:
    for key, value in data.items():
        tag = key.split(":", 1)[-1].lower()
        if tag == "samsungandroidlogvideo" and value:  # Samsung Log (APV or HEVC)
            return "samsung-log"
        if not isinstance(value, str) or not any(
            word in tag for word in ("color", "colour", "gamma", "profile", "picture", "look")
        ):
            continue
        lowered = value.lower()
        for marker, profile in LOG_PROFILES.items():
            if marker in lowered:
                return profile
    return None


def normalize(data: dict[str, Any]) -> NormalizedMetadata:
    make = _as_str(_first(data, ("Make", "AndroidMake", "CameraMake")))
    if (make is None and _is_samsung_camera(data)) or (make or "").lower() == "samsung":
        make = "Samsung"
    model = _as_str(_first(data, ("Model", "AndroidModel", "CameraModelName", "SamsungModel")))
    point, source = _point(data)
    android = _as_str(_first(data, ("AndroidVersion",)))
    return NormalizedMetadata(
        make=make,
        model=model,
        software=_as_str(_first(data, ("Software", "Encoder", "CreatorTool"))),
        lens=_as_str(_first(data, ("LensModel", "LensID", "Lens"))),
        iso=_float(_first(data, ("ISO", "ISOSpeed"))),
        f_number=_float(_first(data, ("FNumber", "Aperture"))),
        exposure_time=_float(_first(data, ("ExposureTime", "ShutterSpeed"))),
        focal_length_mm=_float(_first(data, ("FocalLength",))),
        point=point,
        point_source=source,
        track=_track(data),
        dates=_dates(data, make),
        color_profile=_color_profile(data),
        reencoded_by=_reencoder(data),
        model_name=_samsung_model_name(data, make, model),
        os_version=f"Android {android}" if android else None,
        capture_fps=_float(_first(data, ("AndroidCaptureFPS",))),
        # ExifTool >= 13.43 names the Samsung key AndroidTimeZone; older versions derive a name.
        utc_offset_min=parse_utc_offset(
            _first(data, ("AndroidTimeZone", "SamsungAndroidUtcOffset", "SamsungAndroidUtc_offset"))
        ),
        duration_s=_float(data.get("QuickTime:Duration")),
        container_dates_at_stop=android is not None,
    )


def _is_samsung_camera(data: dict[str, Any]) -> bool:
    """Samsung Camera fingerprint: its smta box (Samsung group) or the SDLN "SEQ_PLAY" atom."""
    return data.get("UserData:PlayMode") == "SEQ_PLAY" or any(
        key.startswith("Samsung:") for key in data
    )


def _samsung_model_name(data: dict[str, Any], make: str | None, model: str | None) -> str | None:
    """Recent Samsung firmware writes the marketing name in the 3GPP ``auth`` box.

    Elsewhere ``Author`` may hold a person's name, so it is only read for Samsung devices and
    only when it looks like a device name; otherwise the model code is looked up.
    """
    if make != "Samsung":
        return None
    author = _as_str(data.get("UserData:Author"))
    if author and author.startswith("Galaxy "):
        return author
    return samsung_model_name(model)


def _reencoder(data: dict[str, Any]) -> str | None:
    """Windows Media Foundation (Photos « Trim », Clipchamp, Movie Maker) adds an ``Xtra`` atom."""
    if any(key.startswith("Microsoft:") for key in data):
        return "Windows Media Foundation"
    return None


def _as_str(value: Any, limit: int = 200) -> str | None:
    """Metadata text written by devices or other people: one line, bounded length."""
    if value in (None, ""):
        return None
    text = " ".join(str(value).split())[:limit]
    return text or None
