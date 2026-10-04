"""Open-Meteo client (historical-forecast and archive endpoints), hourly data in UTC.

Terms: free API for non-commercial use, CC BY 4.0 (attribution shown wherever the data is), less
than 600 calls/min and 10,000/day; server logs may keep coordinates for 90 days. Only rounded
coordinates and a date are sent. Base URLs and the API key are configurable (paid plan or
self-hosting).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from vfe_vision import __version__
from vfe_vision.core.errors import ServiceUnavailableError, VfeError
from vfe_vision.core.ratelimit import MinInterval
from vfe_vision.domain.weather import HOURLY_VARIABLES, HourlySeries, WeatherSource
from vfe_vision.ports.weather import WeatherResponse

HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
ATTRIBUTION_URL = "https://open-meteo.com/"
COPERNICUS_NOTICE = "Generated using Copernicus Climate Change Service information"
COORDINATE_DECIMALS = 2  # ~1 km: plenty for 1.5–9 km model grids, and a small privacy gain


def rounded(value: float) -> float:
    return round(value, COORDINATE_DECIMALS)


class OpenMeteoClient:
    def __init__(
        self,
        *,
        historical_forecast_url: str = HISTORICAL_FORECAST_URL,
        archive_url: str = ARCHIVE_URL,
        api_key: str | None = None,
        timeout_s: float = 20.0,
        min_interval_s: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._urls = {
            WeatherSource.HISTORICAL_FORECAST: historical_forecast_url,
            WeatherSource.ARCHIVE: archive_url,
        }
        self._api_key = api_key
        self._throttle = MinInterval(min_interval_s)
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            headers={"User-Agent": f"vfe-vision/{__version__} (local video analysis)"},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def hourly(
        self, source: WeatherSource, latitude: float, longitude: float, start: date, end: date
    ) -> WeatherResponse:
        params: dict[str, str] = {
            "latitude": f"{rounded(latitude):.2f}",
            "longitude": f"{rounded(longitude):.2f}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(HOURLY_VARIABLES),
            "timezone": "GMT",
            "timeformat": "iso8601",
        }
        if self._api_key:
            params["apikey"] = self._api_key
        self._throttle.wait()
        try:
            response = self._http.get(self._urls[source], params=params)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"Open-Meteo injoignable : {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ServiceUnavailableError(f"Open-Meteo indisponible (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ServiceUnavailableError("Réponse Open-Meteo illisible") from exc
        if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("error"):
            reason = payload.get("reason") if isinstance(payload, dict) else None
            raise VfeError(f"Open-Meteo a refusé la requête : {reason or response.status_code}")
        return self.parse(source, payload)

    def parse(self, source: WeatherSource, raw: dict[str, Any]) -> WeatherResponse:
        if raw.get("utc_offset_seconds", 0) != 0:
            raise VfeError("Open-Meteo a répondu dans un autre fuseau que l'UTC")
        hourly = raw.get("hourly")
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise VfeError("Réponse Open-Meteo sans données horaires")
        times = tuple(
            datetime.fromisoformat(str(stamp)).replace(tzinfo=UTC) for stamp in hourly["time"]
        )
        values = {
            name: [_number(v) for v in column]
            for name, column in hourly.items()
            if name != "time" and isinstance(column, list)
        }
        units = {str(k): str(v) for k, v in (raw.get("hourly_units") or {}).items()}
        return WeatherResponse(
            source=source,
            series=HourlySeries(times=times, values=values, units=units),
            grid_latitude=_number(raw.get("latitude")),
            grid_longitude=_number(raw.get("longitude")),
            elevation_m=_number(raw.get("elevation")),
            raw=raw,
        )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
