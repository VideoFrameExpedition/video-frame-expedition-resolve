"""Time-zone polygons (timezonefinder), or none when Windows refuses their compiled part.

timezonefinder finds its candidate polygons through h3, a compiled extension that Smart App
Control refused on a protected PC (h3 4.5.0). The application then goes on without the
polygons: no shooting time zone from the GPS point (the video's own one is kept), and offline places
without borders. Any other import error stands.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from vfe_vision.core import native_modules
from vfe_vision.core.logging import get_logger

if TYPE_CHECKING:
    from timezonefinder import TimezoneFinder

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Polygons:
    finder: TimezoneFinder | None
    refusal: str | None  # the refused files that keep the polygons away


@cache
def _polygons() -> _Polygons:
    try:
        from timezonefinder import TimezoneFinder
    except ImportError as error:
        if not native_modules.is_refusal(error):
            raise
        found = native_modules.refused_behind(error)
        refusal = ", ".join(map(native_modules.named, found)) or str(error)
        log.warning("time zones unavailable: refused by Windows", files=refusal)
        return _Polygons(None, refusal)
    return _Polygons(TimezoneFinder(in_memory=True), None)


def available() -> bool:
    """Whether the polygons can be used (loads them on the first call)."""
    return _polygons().finder is not None


def refusal() -> str | None:
    """The refused files that keep the polygons away, None when they are available."""
    return _polygons().refusal


def zone_at(latitude: float, longitude: float) -> str | None:
    """The IANA zone of a point (``Etc/GMT±n`` on the open sea), None without polygons."""
    finder = _polygons().finder
    return None if finder is None else finder.timezone_at(lat=latitude, lng=longitude)
