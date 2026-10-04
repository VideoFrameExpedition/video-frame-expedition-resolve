"""Place names from reverse geocoding: normalisation, labels, natural features.

Nominatim's zoom-18 object is often an unnamed track, elevator or car park, so the label is built
from the structured ``address``, never from ``name`` or ``display_name``. In France,
``municipality`` is the arrondissement (Les Andelys, Bernay…), not the commune, and ``region`` is
« France métropolitaine »: both are ignored. The offline gazetteer only knows the *nearest*
populated place, so its answers are worded « près de … (≈ d km) » (near …), never « à … »
(in …).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from vfe_vision.domain.geo import GeoPoint, haversine_m

GRID_DECIMALS = 3  # ~111 m × 79 m cells at 45° N: the point sent to the service moves ≤ ~70 m
NATURAL_MAX_DISTANCE_M = 1000.0
NATURAL_MAX_BBOX_DIAGONAL_M = 5000.0
_NATURAL_PLACES = {"island", "islet", "archipelago"}
_OSM_VALUE = re.compile(r"^[a-z0-9_]{1,40}$")  # OSM tag values; anything else is dropped
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
GEONAMES_ATTRIBUTION = "GeoNames (CC BY 4.0)"


def _thousandths(value: float) -> int:
    """Round half towards +∞ on the decimal digits as written (2.2945 → 2295, -0.0005 → 0);
    binary floats would say 2294."""
    scaled = Decimal(repr(value)) * 1000 + Decimal("0.5")
    return int(scaled.to_integral_value(rounding=ROUND_FLOOR))


def grid_e3(latitude: float, longitude: float) -> tuple[int, int]:
    """Point on the 0.001° grid, as integers (cache key; the same point is sent to the service)."""
    if not -180.0 <= longitude < 180.0:
        longitude = (longitude + 180.0) % 360.0 - 180.0
    return _thousandths(latitude), _thousandths(longitude)


@dataclass(frozen=True, slots=True)
class NaturalFeature:
    name: str
    category: str
    type: str
    distance_m: float


@dataclass(frozen=True, slots=True)
class Place:
    locality: str | None
    sublocality: str | None = None
    road: str | None = None
    postcode: str | None = None
    county: str | None = None  # FR: département
    state: str | None = None  # FR: région
    country: str | None = None
    country_code: str | None = None  # ISO 3166-1 alpha-2, upper case
    iso3166_2: str | None = None
    display_name: str | None = None
    feature: NaturalFeature | None = None
    at_sea: bool = False
    approximate: bool = False  # offline gazetteer: nearest locality, not the containing one
    locality_distance_km: float | None = None

    @property
    def region(self) -> str | None:
        """Département when it differs from the locality (Paris), else the région."""
        if self.county and self.county != self.locality:
            return self.county
        return self.state

    def label(self, language: str = "fr") -> str | None:
        """« Giverny, Eure, France », « près de Vernon (≈ 1 km), Eure, France », « En mer »."""
        if self.locality is None:
            if self.at_sea:
                return "En mer" if language == "fr" else "At sea"
            parts = [self.region, self.country]
        else:
            locality = self.locality
            if self.approximate and self.locality_distance_km is not None:
                distance = _format_km(self.locality_distance_km, language)
                near = "près de" if language == "fr" else "near"
                locality = f"{near} {self.locality} (≈ {distance})"
            parts = [locality, self.region, self.country]
        unique = [p for i, p in enumerate(parts) if p and p not in parts[:i]]
        return ", ".join(unique) or None


def _format_km(km: float, language: str) -> str:
    text = f"{km:.0f}" if km >= 10 else f"{km:.1f}".rstrip("0").rstrip(".")
    if language == "fr":
        text = text.replace(".", ",")
    return f"{text} km"


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())[:200]
    return cleaned or None


def parse_nominatim(payload: dict[str, Any]) -> Place | None:
    """Normalise a ``format=jsonv2`` reverse answer; ``None`` for « Unable to geocode »."""
    address = payload.get("address")
    if payload.get("error") or not isinstance(address, dict):
        return None
    a = {str(k): _text(v) for k, v in address.items()}
    code = (a.get("country_code") or "").upper() or None
    locality = (
        a.get("city")
        or a.get("town")
        or a.get("village")
        or (a.get("municipality") if code != "FR" else None)
        or a.get("hamlet")
        or a.get("isolated_dwelling")
    )
    district = a.get("city_district")
    sublocality = (
        a.get("suburb")
        or a.get("quarter")
        or (district if district != locality else None)
        or a.get("neighbourhood")
    )
    return Place(
        locality=locality,
        sublocality=sublocality if sublocality != locality else None,
        road=a.get("road"),
        postcode=a.get("postcode"),
        county=a.get("county"),
        state=a.get("state"),
        country=a.get("country"),
        country_code=code,
        iso3166_2=a.get("ISO3166-2-lvl6") or a.get("ISO3166-2-lvl4"),
        display_name=_text(payload.get("display_name")),
    )


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def natural_feature(query: GeoPoint, payload: dict[str, Any]) -> NaturalFeature | None:
    """Accept a ``layer=natural`` answer only when it is really *there*.

    The natural layer returns the nearest natural object at any distance (a river whose centroid
    is 7 km away): keep natural features and islands within 1 km, or whose bounding box contains
    the point and is at most 5 km across. Waterways are never accepted.
    """
    category, kind = payload.get("category"), payload.get("type")
    name = _text(payload.get("name"))
    if not name or not isinstance(category, str) or not isinstance(kind, str):
        return None
    if not _OSM_VALUE.match(kind):
        return None
    if not (category == "natural" or (category == "place" and kind in _NATURAL_PLACES)):
        return None
    lat, lon = _float(payload.get("lat")), _float(payload.get("lon"))
    if lat is None or lon is None:
        return None
    distance = haversine_m(query, GeoPoint(lat, lon))
    if distance > NATURAL_MAX_DISTANCE_M and not _bbox_contains(query, payload.get("boundingbox")):
        return None
    return NaturalFeature(name=name, category=category, type=kind, distance_m=round(distance))


def _bbox_contains(point: GeoPoint, bbox: Any) -> bool:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    south, north, west, east = (_float(v) for v in bbox)
    if south is None or north is None or west is None or east is None:
        return False
    try:
        diagonal = haversine_m(GeoPoint(south, west), GeoPoint(north, east))
    except ValueError:
        return False
    inside = south <= point.latitude <= north and west <= point.longitude <= east
    return inside and diagonal <= NATURAL_MAX_BBOX_DIAGONAL_M
