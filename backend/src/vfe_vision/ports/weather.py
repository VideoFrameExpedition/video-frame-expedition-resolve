"""Port: hourly model weather for a point and a range of UTC dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from vfe_vision.domain.weather import HourlySeries, WeatherSource


@dataclass(frozen=True, slots=True)
class WeatherResponse:
    source: WeatherSource
    series: HourlySeries
    grid_latitude: float | None  # the model cell that answered
    grid_longitude: float | None
    elevation_m: float | None
    raw: dict[str, Any]  # stored as-is (cache, audit)


class WeatherProvider(Protocol):
    def hourly(
        self, source: WeatherSource, latitude: float, longitude: float, start: date, end: date
    ) -> WeatherResponse:
        """Raise ``ServiceUnavailableError`` (retry later) or ``VfeError`` (bad request)."""
        ...

    def parse(self, source: WeatherSource, raw: dict[str, Any]) -> WeatherResponse:
        """Rebuild a response from its stored raw form (cache hit)."""
        ...
