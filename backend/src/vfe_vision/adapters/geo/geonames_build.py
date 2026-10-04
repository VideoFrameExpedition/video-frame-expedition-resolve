"""Download GeoNames extracts (CC BY 4.0) and build the offline gazetteer (~3 MB).

Run once with ``vfe models geonames`` (an online action, like model downloads). The dumps are
updated daily upstream, so there is no fixed checksum: the snapshot date is recorded instead.
"""

from __future__ import annotations

import csv
import gzip
import io
import shutil
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from vfe_vision import __version__
from vfe_vision.adapters.geo.offline import AREAS_FILE, PLACES_FILE

DUMP_URL = "https://download.geonames.org/export/dump"
SOURCES = ("cities1000.zip", "admin1CodesASCII.txt", "admin2Codes.txt")
SOURCE_NOTE = "SOURCE.txt"
COLUMNS = 19
RENAME_ATTEMPTS = 5  # antivirus or indexer may hold a file for a moment (WinError 5/32)


def _rename(source: Path, target: Path) -> None:
    for attempt in range(RENAME_ATTEMPTS):
        try:
            source.replace(target)
        except PermissionError:
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * (attempt + 1))
        else:
            return


def _swap_in(staging: Path, folder: Path) -> None:
    """Replace ``folder`` by ``staging``; the old gazetteer survives any failure."""
    backup = folder.with_name(f"{folder.name}.old")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    had_old = folder.exists()
    if had_old:
        _rename(folder, backup)  # fails early: nothing lost
    try:
        _rename(staging, folder)
    except OSError:
        if had_old:
            _rename(backup, folder)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _download(client: httpx.Client, name: str, target: Path) -> None:
    with client.stream("GET", f"{DUMP_URL}/{name}") as response:
        response.raise_for_status()
        with target.open("wb") as out:
            for chunk in response.iter_bytes():
                out.write(chunk)


def _lines(text: str) -> list[str]:
    """Split on newlines only: names may contain U+2028, NEL… that ``splitlines`` would cut."""
    return [line.rstrip("\r") for line in text.split("\n") if line]


def _codes(path: Path) -> dict[str, str]:
    codes: dict[str, str] = {}
    for line in _lines(path.read_text(encoding="utf-8")):
        cols = line.split("\t")
        if len(cols) >= 2:
            codes[cols[0]] = cols[1]
    return codes


def build_gazetteer(sources: Path, folder: Path) -> int:
    """Turn the raw dumps in ``sources`` into the compact files in ``folder``; return the rows."""
    admin1 = _codes(sources / "admin1CodesASCII.txt")
    admin2 = _codes(sources / "admin2Codes.txt")
    text = zipfile.ZipFile(sources / "cities1000.zip").read("cities1000.txt").decode("utf-8")
    csv.field_size_limit(2**31 - 1)
    areas: list[tuple[str, str, str]] = []
    area_index: dict[tuple[str, str, str], int] = {}
    places = io.StringIO()
    rows = 0
    for r in csv.reader(_lines(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        # geonameid, name, asciiname, alternatenames, lat, lon, class, code, cc, cc2, admin1,
        # admin2, admin3, admin4, population, elevation, dem, timezone, modified
        if len(r) < COLUMNS:
            continue  # a damaged row is skipped, not fatal
        try:
            lat, lon = float(r[4]), float(r[5])
        except ValueError:
            continue
        cc, key1 = r[8], f"{r[8]}.{r[10]}"
        key = (cc, admin1.get(key1, ""), admin2.get(f"{key1}.{r[11]}", ""))
        if key not in area_index:
            area_index[key] = len(areas)
            areas.append(key)
        name = " ".join(r[1].split())
        places.write(f"{name}\t{lat:.4f}\t{lon:.4f}\t{r[14]}\t{r[7]}\t{area_index[key]}\n")
        rows += 1
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PLACES_FILE).write_bytes(gzip.compress(places.getvalue().encode("utf-8"), 9))
    (folder / AREAS_FILE).write_bytes(
        gzip.compress("\n".join("\t".join(a) for a in areas).encode("utf-8"), 9)
    )
    return rows


def download_gazetteer(folder: Path, *, transport: httpx.BaseTransport | None = None) -> int:
    """Download the dumps, build the gazetteer next to ``folder``, then swap it in (the old one
    is kept if anything fails)."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": f"vfe-vision/{__version__} (local video analysis application)"}
    with (
        tempfile.TemporaryDirectory(dir=folder.parent) as tmp,
        httpx.Client(
            timeout=httpx.Timeout(120, connect=10), headers=headers, transport=transport
        ) as client,
    ):
        sources, staging = Path(tmp) / "sources", Path(tmp) / "gazetteer"
        sources.mkdir()
        for name in SOURCES:
            _download(client, name, sources / name)
        rows = build_gazetteer(sources, staging)
        (staging / SOURCE_NOTE).write_text(
            f"GeoNames {', '.join(SOURCES)} — {DUMP_URL}\n"
            f"Downloaded {datetime.now(UTC):%Y-%m-%d}. Licence: CC BY 4.0 "
            "(https://creativecommons.org/licenses/by/4.0/).\n",
            encoding="utf-8",
        )
        _swap_in(staging, folder)
    return rows
