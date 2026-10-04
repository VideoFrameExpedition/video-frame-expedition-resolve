"""Telemetry stored next to the video: DJI ``.SRT`` flight logs and ``.gpx`` tracks.

A ``.srt`` holding subtitles (the application writes a video's there) is not telemetry:
only one giving positions in its first blocks is a sidecar."""

from __future__ import annotations

import glob
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from vfe_vision.adapters.exiftool.normalize import TrackSample, parse_exif_date
from vfe_vision.domain.geo import GeoPoint

_SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->")
_SRT_LAT = re.compile(r"latitude\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_SRT_LON = re.compile(r"longi?tude\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_SRT_ALT = re.compile(r"(?:abs_alt|altitude)\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_SRT_GPS_TUPLE = re.compile(r"GPS\s*\(\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*,\s*(-?\d+(?:\.\d+)?)")
_SRT_DATE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:[,.](\d+))?")
_SRT_FIELD = re.compile(
    r"\[?\b(iso|shutter|fnum|ev|ct|focal_len)\s*[:=]\s*([^\]\s]+)", re.IGNORECASE
)
TELEMETRY_HEAD = 64 * 1024  # bytes of a .srt read to tell a flight log from subtitles


def find_sidecars(video: Path) -> dict[str, Path]:
    """Sidecar files sharing the video's stem (case-insensitive extension): a GPX track, a
    ``.srt`` that is a flight log (subtitles are not: see ``is_telemetry``)."""
    found: dict[str, Path] = {}
    for candidate in video.parent.glob(f"{glob.escape(video.stem)}.*"):
        suffix = candidate.suffix.lower()
        if candidate == video:
            continue
        if suffix == ".gpx" or (suffix == ".srt" and is_telemetry(candidate)):
            found[suffix] = candidate
    return found


def is_telemetry(path: Path) -> bool:
    """A flight log rather than subtitles: a position in its first blocks (DJI writes one in
    every block, 0 before the GPS fix)."""
    try:
        with path.open("rb") as handle:
            head = handle.read(TELEMETRY_HEAD).decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(_SRT_LAT.search(head) or _SRT_GPS_TUPLE.search(head))


def parse_dji_srt(path: Path) -> tuple[list[TrackSample], dict[str, float]]:
    """DJI flight telemetry subtitles → GPS samples and median camera settings.

    Returns empty results for ordinary subtitle files (no GPS fields).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    samples: list[TrackSample] = []
    settings: dict[str, list[float]] = {}
    for block in re.split(r"\n\s*\n", text):
        time_match = _SRT_TIME.search(block)
        if time_match is None:
            continue
        h, m, s, ms = (int(g) for g in time_match.groups())
        t_s = h * 3600 + m * 60 + s + ms / 1000
        lat = _SRT_LAT.search(block)
        lon = _SRT_LON.search(block)
        alt = _SRT_ALT.search(block)
        if lat and lon:
            latitude, longitude = float(lat.group(1)), float(lon.group(1))
            altitude = float(alt.group(1)) if alt else None
        elif (gps := _SRT_GPS_TUPLE.search(block)) is not None:  # older firmwares: GPS(lon,lat,alt)
            longitude, latitude, altitude = (float(v) for v in gps.groups())
        else:
            continue
        try:
            point = GeoPoint(latitude, longitude, altitude)
        except ValueError:
            continue
        if point.is_null_island:
            continue
        utc = None
        if (date := _SRT_DATE.search(block)) is not None:
            try:
                parsed = datetime.fromisoformat(date.group(1))
            except ValueError:  # corrupted or hand-edited log
                parsed = None
            # DJI writes local time; resolved later with the time zone.
            utc = parsed.replace(tzinfo=None) if parsed is not None else None
        samples.append(TrackSample(t_s=t_s, utc=utc, point=point))
        for name, value in _SRT_FIELD.findall(block):
            number = _number(value)
            if number is not None:
                settings.setdefault(name.lower(), []).append(number)
    medians = {k: sorted(v)[len(v) // 2] for k, v in settings.items() if v}
    return samples, medians


def parse_gpx(path: Path) -> list[TrackSample]:
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - local user file, no DTD/entities
    except ET.ParseError:
        return []
    samples: list[TrackSample] = []
    for element in root.iter():
        if not element.tag.endswith("trkpt"):
            continue
        try:
            point = GeoPoint(
                float(element.attrib["lat"]),
                float(element.attrib["lon"]),
                _child_float(element, "ele"),
            )
        except (KeyError, ValueError):
            continue
        when = _child_text(element, "time")
        utc = parse_exif_date(when.replace("-", ":", 2).replace("T", " ")) if when else None
        if utc is not None and utc.tzinfo is None:
            utc = utc.replace(tzinfo=UTC)
        samples.append(TrackSample(t_s=None, utc=utc, point=point))
    return samples


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if child.tag.endswith(name) and child.text:
            return child.text.strip()
    return None


def _child_float(element: ET.Element, name: str) -> float | None:
    text = _child_text(element, name)
    return _number(text) if text else None


def _number(value: str) -> float | None:
    cleaned = value.strip().rstrip(",;")
    if "/" in cleaned:  # shutter "1/1000"
        num, _, den = cleaned.partition("/")
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(cleaned)
    except ValueError:
        return None
