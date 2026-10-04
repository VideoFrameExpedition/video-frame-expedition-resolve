"""Fake online services for the context stages (no network in tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from vfe_vision.adapters.weather.open_meteo import OpenMeteoClient
from vfe_vision.core.errors import ServiceUnavailableError
from vfe_vision.domain.weather import HOURLY_VARIABLES, WeatherSource
from vfe_vision.ports.weather import WeatherResponse

PARIS_ADDRESS: dict[str, Any] = {
    "licence": "Data © OpenStreetMap contributors, ODbL 1.0. http://osm.org/copyright",
    "category": "highway",
    "type": "elevator",
    "name": "",
    "display_name": "Avenue Gustave Eiffel, Paris 7e Arrondissement, Paris, France",
    "address": {
        "road": "Avenue Gustave Eiffel",
        "suburb": "Paris 7e Arrondissement",
        "city": "Paris",
        "state": "Île-de-France",
        "postcode": "75007",
        "country": "France",
        "country_code": "fr",
    },
}
SAMPLE: dict[str, float] = {
    "temperature_2m": 24.0,
    "relative_humidity_2m": 50.0,
    "dew_point_2m": 12.9,
    "apparent_temperature": 24.5,
    "precipitation": 0.0,
    "rain": 0.0,
    "showers": 0.0,
    "snowfall": 0.0,
    "weather_code": 1.0,
    "cloud_cover": 10.0,
    "cloud_cover_low": 0.0,
    "cloud_cover_mid": 5.0,
    "cloud_cover_high": 10.0,
    "pressure_msl": 1018.0,
    "wind_speed_10m": 8.0,
    "wind_direction_10m": 270.0,
    "wind_gusts_10m": 15.0,
    "shortwave_radiation": 700.0,
    "direct_radiation": 600.0,
    "diffuse_radiation": 100.0,
    "sunshine_duration": 3600.0,
    "visibility": 30000.0,
    "is_day": 1.0,
}


@dataclass
class FakeWeather:
    """Constant fine weather for any requested dates; can simulate an outage."""

    down: bool = False
    calls: list[tuple[WeatherSource, float, float, date, date]] = field(default_factory=list)

    def hourly(
        self, source: WeatherSource, latitude: float, longitude: float, start: date, end: date
    ) -> WeatherResponse:
        self.calls.append((source, latitude, longitude, start, end))
        if self.down:
            raise ServiceUnavailableError("Open-Meteo injoignable (test)")
        first = datetime(start.year, start.month, start.day)
        hours = int((end - start).days + 1) * 24
        times = [(first + timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M") for k in range(hours)]
        raw: dict[str, Any] = {
            "latitude": round(latitude, 2),
            "longitude": round(longitude, 2),
            "elevation": 35.0,
            "utc_offset_seconds": 0,
            "timezone": "GMT",
            "hourly_units": dict.fromkeys(HOURLY_VARIABLES, ""),
            "hourly": {"time": times} | {name: [SAMPLE[name]] * hours for name in HOURLY_VARIABLES},
        }
        return self.parse(source, raw)

    def parse(self, source: WeatherSource, raw: dict[str, Any]) -> WeatherResponse:
        return OpenMeteoClient().parse(source, raw)


@dataclass
class FakeGeocoder:
    down: bool = False
    address_error: Exception | None = None  # raised by the address lookup only
    natural_error: Exception | None = None  # raised by the nearby-feature lookup only
    natural_answer: dict[str, Any] = field(default_factory=lambda: {"error": "Unable to geocode"})
    calls: list[tuple[float, float, str, bool]] = field(default_factory=list)

    def reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str,
        natural: bool = False,
        email: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((latitude, longitude, language, natural))
        if self.down:
            raise ServiceUnavailableError("Nominatim injoignable (test)")
        error = self.natural_error if natural else self.address_error
        if error is not None:
            raise error
        return self.natural_answer if natural else PARIS_ADDRESS
