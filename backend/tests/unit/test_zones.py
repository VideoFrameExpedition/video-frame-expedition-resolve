"""Without the time-zone polygons (Windows refusing h3), places and times go on."""

from __future__ import annotations

import gzip
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from vfe_vision.adapters.geo import zones
from vfe_vision.adapters.geo.offline import AREAS_FILE, PLACES_FILE, GeoNamesGazetteer
from vfe_vision.core import native_modules
from vfe_vision.core.native_modules import Refused
from vfe_vision.domain.geo import GeoPoint
from vfe_vision.pipeline.stages.metadata import timezone_at

BEAUSOLEIL = (43.7445, 7.4220)  # France, a few hundred metres above Monte-Carlo
OPEN_SEA = (40.0, -40.0)


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    zones._polygons.cache_clear()
    yield
    zones._polygons.cache_clear()


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """timezonefinder cannot be imported: Windows refuses h3's compiled memory module."""
    monkeypatch.setitem(sys.modules, "timezonefinder", None)
    monkeypatch.setattr(native_modules, "is_refusal", lambda _error: True)
    found = [Refused(Path("memory.cp312-win_amd64.pyd"), "h3 4.5.0")]
    monkeypatch.setattr(native_modules, "refused_behind", lambda _error: found)


def _gazetteer(folder: Path) -> GeoNamesGazetteer:
    rows = [
        ("Monte-Carlo", 43.7397, 7.4271, "MC"),
        ("Roquebrune-Cap-Martin", 43.7600, 7.4600, "FR"),
    ]
    places = "".join(
        f"{name}\t{la}\t{lo}\t1\tPPL\t{i}\n" for i, (name, la, lo, _) in enumerate(rows)
    )
    (folder / PLACES_FILE).write_bytes(gzip.compress(places.encode()))
    areas = "\n".join(f"{code}\t\t" for *_, code in rows)
    (folder / AREAS_FILE).write_bytes(gzip.compress(areas.encode()))
    return GeoNamesGazetteer(folder)


def test_with_the_polygons_borders_and_the_open_sea_are_known(tmp_path: Path) -> None:
    assert zones.available()
    assert zones.refusal() is None
    assert timezone_at(GeoPoint(latitude=50.85, longitude=4.35)) == "Europe/Brussels"
    gazetteer = _gazetteer(tmp_path)
    assert gazetteer.nearest(*BEAUSOLEIL).locality == "Roquebrune-Cap-Martin"  # not Monaco's
    assert gazetteer.nearest(*OPEN_SEA).at_sea


@pytest.mark.usefixtures("refused")
def test_without_the_polygons_the_nearest_place_is_taken(tmp_path: Path) -> None:
    assert not zones.available()
    assert zones.refusal() == "memory.cp312-win_amd64.pyd (h3 4.5.0)"
    # The video's own zone stands: no zone comes from the GPS point.
    assert timezone_at(GeoPoint(latitude=50.85, longitude=4.35)) is None
    gazetteer = _gazetteer(tmp_path)
    nearest = gazetteer.nearest(*BEAUSOLEIL)
    assert (nearest.locality, nearest.country_code) == ("Monte-Carlo", "MC")  # across the border
    assert not gazetteer.nearest(*OPEN_SEA).at_sea  # unknown, not said


def test_any_other_import_error_stands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "timezonefinder", None)
    with pytest.raises(ImportError):
        zones.available()
