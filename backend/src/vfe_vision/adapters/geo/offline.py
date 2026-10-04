"""Offline gazetteer: nearest GeoNames locality, country and « at sea ».

No scipy (Smart App Control blocks its native modules on this kind of machine): the nearest place
is a vectorised haversine over ~170,000 GeoNames ``cities1000`` rows (≈ 5 ms). Borders come from
the time-zone polygons (timezonefinder): a candidate place must lie in the same polygon as the
point, so Menton is not Monaco, and the country is the *place's* own one. A zone is not a country:
Asia/Bangkok also covers north Vietnam, Europe/Belgrade six countries, and Kosovo, East Jerusalem
or the Golan sit in another country's zone. Without a locality, a country is only named when the
zone belongs to a single one (tzdata ``zone1970.tab``). Without the downloaded extract
(``vfe models geonames``), only that country and « at sea » are known.
"""

from __future__ import annotations

import gzip
import math
import threading
from functools import cache
from importlib import resources
from pathlib import Path

import numpy as np
from timezonefinder import TimezoneFinder

from vfe_vision.domain.place import Place

EARTH_RADIUS_KM = 6371.0088
MAX_LOCALITY_KM = 30.0
PLACES_FILE = "places.tsv.gz"  # name, lat, lon, population, feature code, area index
AREAS_FILE = "areas.tsv.gz"  # country code, admin1 name, admin2 name
SOURCE_FILE = "SOURCE.txt"
CANDIDATES = 64  # nearest rows checked against the time-zone polygon of the point
COUNTRY_SUPPLEMENT = {"XK": "Kosovo"}  # GeoNames codes missing from tzdata's iso3166.tab


@cache
def _timezones() -> TimezoneFinder:
    return TimezoneFinder(in_memory=True)


@cache
def _zone_countries() -> dict[str, frozenset[str]]:
    """Countries whose territory a zone covers (``zone1970.tab``, completed by ``zone.tab``)."""
    zones: dict[str, set[str]] = {}
    for table in ("zone1970.tab", "zone.tab"):
        text = (resources.files("tzdata") / "zoneinfo" / table).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line and not line.startswith("#"):
                cols = line.split("\t")
                zones.setdefault(cols[2], set()).update(cols[0].split(","))
    return {zone: frozenset(codes) for zone, codes in zones.items()}


def _single_country(zone: str | None) -> str | None:
    codes = _zone_countries().get(zone or "", frozenset())
    return next(iter(codes)) if len(codes) == 1 else None


@cache
def _country_names() -> dict[str, str]:
    text = (resources.files("tzdata") / "zoneinfo" / "iso3166.tab").read_text(encoding="utf-8")
    names: dict[str, str] = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            code, _, name = line.partition("\t")
            names[code] = name.strip()
    return names | COUNTRY_SUPPLEMENT


class _Index:
    def __init__(self, folder: Path) -> None:
        names: list[str] = []
        lat: list[float] = []
        lon: list[float] = []
        area: list[int] = []
        with gzip.open(folder / PLACES_FILE, "rt", encoding="utf-8") as lines:
            for line in lines:
                name, la, lo, _population, _code, index = line.rstrip("\n").split("\t")
                names.append(name)
                lat.append(float(la))
                lon.append(float(lo))
                area.append(int(index))
        text = gzip.decompress((folder / AREAS_FILE).read_bytes()).decode("utf-8")
        self.areas = [tuple(line.split("\t")) for line in text.split("\n") if line]
        self.names = names
        self.lat_deg = lat
        self.lon_deg = lon
        self.lat = np.radians(np.asarray(lat))
        self.lon = np.radians(np.asarray(lon))
        self.cos_lat = np.cos(self.lat)
        self.area = np.asarray(area, dtype=np.int32)


class GeoNamesGazetteer:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._index: _Index | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return (self.folder / PLACES_FILE).is_file() and (self.folder / AREAS_FILE).is_file()

    @property
    def version(self) -> str | None:
        """Identity of the installed extract (its download note), ``None`` when absent."""
        try:
            return (self.folder / SOURCE_FILE).read_text(encoding="utf-8").strip()[:200]
        except OSError:
            return "installed" if self.available else None

    def _load(self) -> _Index | None:
        with self._lock:
            if self._index is None and self.available:
                self._index = _Index(self.folder)
            return self._index

    def nearest(self, latitude: float, longitude: float) -> Place:
        finder = _timezones()
        zone = finder.timezone_at(lat=latitude, lng=longitude)
        at_sea = zone is None or zone.startswith("Etc/")
        code = None if at_sea else _single_country(zone)
        country_only = Place(
            locality=None,
            country=_country_names().get(code) if code else None,
            country_code=code,
            at_sea=at_sea,
        )
        index = self._load()
        if index is None or at_sea:
            return country_only
        la, lo = math.radians(latitude), math.radians(longitude)
        h = (
            np.sin((index.lat - la) / 2) ** 2
            + math.cos(la) * index.cos_lat * np.sin((index.lon - lo) / 2) ** 2
        )
        count = min(CANDIDATES, len(h))
        nearest = np.argpartition(h, count - 1)[:count]
        for i in nearest[np.argsort(h[nearest])]:
            distance = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(float(h[i]), 1.0)))
            if distance > MAX_LOCALITY_KM:
                break
            row_zone = finder.timezone_at(lat=index.lat_deg[i], lng=index.lon_deg[i])
            if row_zone != zone:  # across a border (or a zone boundary): not this place
                continue
            area_code, admin1, admin2 = index.areas[int(index.area[i])]
            return Place(
                locality=index.names[i],
                county=admin2 or None,
                state=admin1 or None,
                country=_country_names().get(area_code),
                country_code=area_code or None,
                approximate=True,
                locality_distance_km=round(distance, 1),
            )
        return country_only
