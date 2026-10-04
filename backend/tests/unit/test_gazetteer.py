"""Offline gazetteer: borders by time-zone polygon, robust build, safe swap."""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import pytest

from vfe_vision.adapters.geo import geonames_build
from vfe_vision.adapters.geo.geonames_build import _swap_in, build_gazetteer
from vfe_vision.adapters.geo.offline import AREAS_FILE, PLACES_FILE, GeoNamesGazetteer

# name, lat, lon, population, feature code, area index — areas: country, admin1, admin2
PLACES = [
    ("Hoàn Kiếm", 21.0285, 105.8542, 100000, "PPLX", 0),
    ("Mae Sai", 20.4280, 99.8840, 20000, "PPL", 1),
    ("Monte-Carlo", 43.7397, 7.4271, 15000, "PPL", 2),
    ("Roquebrune-Cap-Martin", 43.7600, 7.4600, 12000, "PPL", 3),
    ("Pristina", 42.6727, 21.1669, 200000, "PPLC", 4),
]
AREAS = [
    ("VN", "Hanoi", "Quận Hoàn Kiếm"),
    ("TH", "Chiang Rai", ""),
    ("MC", "Municipality of Monaco", ""),
    ("FR", "Provence-Alpes-Côte d'Azur", "Alpes-Maritimes"),
    ("XK", "Pristina", ""),
]


@pytest.fixture
def gazetteer(tmp_path: Path) -> GeoNamesGazetteer:
    rows = "".join(f"{n}\t{la}\t{lo}\t{p}\t{c}\t{a}\n" for n, la, lo, p, c, a in PLACES)
    (tmp_path / PLACES_FILE).write_bytes(gzip.compress(rows.encode()))
    (tmp_path / AREAS_FILE).write_bytes(
        gzip.compress("\n".join("\t".join(a) for a in AREAS).encode())
    )
    return GeoNamesGazetteer(tmp_path)


def test_north_vietnam_is_not_thailand(gazetteer: GeoNamesGazetteer) -> None:
    # Asia/Bangkok covers north Vietnam: the zone is not the country.
    place = gazetteer.nearest(21.03, 105.85)
    assert (place.locality, place.country_code, place.country) == ("Hoàn Kiếm", "VN", "Vietnam")


def test_candidates_across_a_border_are_skipped(gazetteer: GeoNamesGazetteer) -> None:
    # Menton (Europe/Paris): Monte-Carlo is nearer but lies in the Europe/Monaco polygon.
    place = gazetteer.nearest(43.7747, 7.4975)
    assert (place.locality, place.country_code) == ("Roquebrune-Cap-Martin", "FR")


def test_kosovo_has_a_country_name(gazetteer: GeoNamesGazetteer) -> None:
    place = gazetteer.nearest(42.6629, 21.1655)
    assert (place.locality, place.country) == ("Pristina", "Kosovo")


def test_no_country_is_guessed_from_a_multi_country_zone(tmp_path: Path) -> None:
    empty = GeoNamesGazetteer(tmp_path / "missing")
    assert empty.version is None
    hanoi = empty.nearest(21.03, 105.85)  # Asia/Bangkok: TH, KH, LA, VN…
    assert (hanoi.country_code, hanoi.locality) == (None, None)
    giverny = empty.nearest(49.0758, 1.5339)  # Europe/Paris covers FR and MC: no guess either
    assert giverny.country_code is None
    madrid = empty.nearest(40.4168, -3.7038)  # Europe/Madrid: Spain only
    assert (madrid.country_code, madrid.country) == ("ES", "Spain")
    assert empty.nearest(42.5, 6.5).at_sea


def _dumps(folder: Path, cities: str) -> Path:
    folder.mkdir()
    with zipfile.ZipFile(folder / "cities1000.zip", "w") as archive:
        archive.writestr("cities1000.txt", cities)
    (folder / "admin1CodesASCII.txt").write_text(
        "FR.28\tNormandie\tNormandie\t1\n", encoding="utf-8"
    )
    (folder / "admin2Codes.txt").write_text("FR.28.27\tEure\tEure\t2\n", encoding="utf-8")
    return folder


def _row(name: str, alternate: str = "", lat: str = "49.1") -> str:
    cols = ["1", name, name, alternate, lat, "1.5", "P", "PPL", "FR", "", "28", "27",
            "", "", "3000", "", "200", "Europe/Paris", "2026-01-01"]  # fmt: skip
    return "\t".join(cols)


def test_build_survives_odd_unicode_and_damaged_rows(tmp_path: Path) -> None:
    cities = "\n".join(
        [
            _row("Vernon", "Vernonnum Vernon-sur-Seine"),  # U+2028 inside a field
            "damaged\trow",
            _row("Bad", lat="north"),
            _row("Giverny"),
        ]
    )
    rows = build_gazetteer(_dumps(tmp_path / "src", cities + "\n"), tmp_path / "out")
    assert rows == 2
    text = gzip.decompress((tmp_path / "out" / PLACES_FILE).read_bytes()).decode()
    assert [line.split("\t")[0] for line in text.splitlines()] == ["Vernon", "Giverny"]


def test_failed_swap_keeps_the_old_gazetteer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, staging = tmp_path / "geonames", tmp_path / "staging"
    folder.mkdir()
    (folder / "old.txt").write_text("old")
    staging.mkdir()
    (staging / "new.txt").write_text("new")
    real = geonames_build._rename

    def flaky(source: Path, target: Path) -> None:
        if source == staging:
            raise PermissionError("locked by an antivirus scan")
        real(source, target)

    monkeypatch.setattr(geonames_build, "_rename", flaky)
    with pytest.raises(PermissionError):
        _swap_in(staging, folder)
    assert (folder / "old.txt").read_text() == "old"
    monkeypatch.setattr(geonames_build, "_rename", real)
    _swap_in(staging, folder)
    assert (folder / "new.txt").exists()
    assert not (tmp_path / "geonames.old").exists()
