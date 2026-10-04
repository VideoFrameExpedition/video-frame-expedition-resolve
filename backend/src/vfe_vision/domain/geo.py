"""Geographic helpers: ISO 6709 parsing, distances, track statistics (pure functions).

The legacy application parsed ``+DD.DDDD+DDD.DDDD/`` by fixed character positions, which broke on
negative latitudes, altitudes and other precisions. This parser follows ISO 6709 Annex H.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8

# Sign, integer part (2-3 degrees, 4-5 DDMM, 6-7 DDMMSS digits), optional fraction.
_COMPONENT = r"([+-])(\d+)(?:\.(\d+))?"
_ISO6709 = re.compile(rf"^\s*{_COMPONENT}{_COMPONENT}(?:{_COMPONENT})?(?:CRS[\w:]+)?/?\s*$")


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float
    altitude_m: float | None = None

    def __post_init__(self) -> None:
        if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValueError(f"coordonnées hors limites : {self.latitude}, {self.longitude}")

    @property
    def is_null_island(self) -> bool:
        """(0, 0) is what many devices write when they have no fix."""
        return abs(self.latitude) < 1e-6 and abs(self.longitude) < 1e-6


def _angle(sign: str, integer: str, fraction: str | None, degree_digits: int) -> float:
    """Decode ±DD[.d], ±DDMM[.m] or ±DDMMSS[.s] (lat: 2 degree digits, lon: 3)."""
    frac = float(f"0.{fraction}") if fraction else 0.0
    extra = len(integer) - degree_digits
    if extra == 0:
        value = int(integer) + frac
    elif extra == 2:
        value = int(integer[:degree_digits]) + (int(integer[degree_digits:]) + frac) / 60
    elif extra == 4:
        minutes = int(integer[degree_digits : degree_digits + 2])
        seconds = int(integer[degree_digits + 2 :]) + frac
        value = int(integer[:degree_digits]) + minutes / 60 + seconds / 3600
    else:
        raise ValueError(f"composante ISO 6709 invalide : {sign}{integer}")
    return -value if sign == "-" else value


def parse_iso6709(text: str) -> GeoPoint | None:
    """Parse strings such as ``+45.7640+004.8357/`` or ``-33.8688+151.2093+012.5/``.

    Returns ``None`` for empty, malformed or null-island values.
    """
    match = _ISO6709.match(text or "")
    if match is None:
        return None
    lat_s, lat_i, lat_f, lon_s, lon_i, lon_f, alt_s, alt_i, alt_f = match.groups()
    try:
        latitude = _angle(lat_s, lat_i, lat_f, 2)
        longitude = _angle(lon_s, lon_i, lon_f, 3)
        altitude = None
        if alt_s is not None:
            altitude = float(f"{alt_i}.{alt_f or 0}") * (-1 if alt_s == "-" else 1)
        point = GeoPoint(latitude, longitude, altitude)
    except ValueError:
        return None
    return None if point.is_null_island else point


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True, slots=True)
class TrackStats:
    points: int
    distance_m: float
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    max_altitude_m: float | None
    min_altitude_m: float | None

    @property
    def centre(self) -> GeoPoint:
        return GeoPoint((self.min_lat + self.max_lat) / 2, (self.min_lon + self.max_lon) / 2)


def track_stats(points: Sequence[GeoPoint]) -> TrackStats | None:
    if not points:
        return None
    distance = sum(haversine_m(a, b) for a, b in itertools.pairwise(points))
    altitudes = [p.altitude_m for p in points if p.altitude_m is not None]
    return TrackStats(
        points=len(points),
        distance_m=distance,
        min_lat=min(p.latitude for p in points),
        max_lat=max(p.latitude for p in points),
        min_lon=min(p.longitude for p in points),
        max_lon=max(p.longitude for p in points),
        max_altitude_m=max(altitudes) if altitudes else None,
        min_altitude_m=min(altitudes) if altitudes else None,
    )
