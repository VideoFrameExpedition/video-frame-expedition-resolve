"""Weather at the capture instant from hourly model data (pure functions).

Open-Meteo returns hourly UTC samples of two kinds. *Instant* variables are valid at the
timestamp and are interpolated linearly to the capture minute. *Preceding-hour* variables
(precipitation, sunshine, radiation means, gusts, and the weather code derived from them) at
timestamp ``T`` describe the interval ``(T - 1 h, T]``: the capture takes the sample of the hour
that covers it, never an interpolation that would mix two intervals. Relative humidity is
recomputed from the interpolated temperature and dew point (Magnus), wind direction is
interpolated as a vector. Every value may be missing: ``None`` means unknown, never zero.

The legacy application sent the video's local time zone while matching a UTC date, which put the
weather one to two hours off; everything here is UTC.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from vfe_vision.domain.geo import GeoPoint, haversine_m

# Bump when the variable list changes: it is part of the cache key of stored responses.
VARIABLES_VERSION = 1
HOURLY_VARIABLES: tuple[str, ...] = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "pressure_msl",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "sunshine_duration",
    "visibility",
    "is_day",
)

HOUR = timedelta(hours=1)
# The historical-forecast archive (high-resolution models, visibility) starts around 2022; older
# dates come from the reanalysis archive (ERA5 / IFS).
HISTORICAL_FORECAST_START = date(2022, 1, 1)
# Recent model data is still being consolidated: flagged provisional, refetched once settled.
PROVISIONAL_FOR = timedelta(hours=48)
SETTLED_AFTER = timedelta(days=7)
CALM_WIND_KMH = 0.5  # below this mean vector, the direction is meaningless
MIN_RADIATION_FOR_RATIO = 20.0  # W/m²: diffuse fraction is noise under a dark sky
WET_GROUND_HOURS = 3


class WeatherSource(StrEnum):
    HISTORICAL_FORECAST = "historical_forecast"
    ARCHIVE = "archive"


@dataclass(frozen=True, slots=True)
class HourlySeries:
    """Hourly UTC samples as returned by the provider (values may be ``None``)."""

    times: tuple[datetime, ...]
    values: Mapping[str, Sequence[float | None]]
    units: Mapping[str, str]

    def index(self, t: datetime) -> int | None:
        try:
            return self.times.index(t)
        except ValueError:
            return None

    def get(self, name: str, i: int | None) -> float | None:
        if i is None or name not in self.values:
            return None
        column = self.values[name]
        if not 0 <= i < len(column):
            return None
        value = column[i]
        return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class WeatherAt:
    """Model weather interpolated to one instant (units: °C, %, hPa, m, km/h, mm, cm, W/m², s)."""

    at_utc: datetime
    temperature_c: float | None
    dew_point_c: float | None
    relative_humidity_pct: float | None
    apparent_temperature_c: float | None
    pressure_hpa: float | None
    cloud_cover_pct: float | None
    cloud_low_pct: float | None
    cloud_mid_pct: float | None
    cloud_high_pct: float | None
    visibility_m: float | None
    wind_speed_kmh: float | None
    wind_direction_deg: float | None
    wind_gusts_kmh: float | None
    precipitation_mm: float | None
    rain_mm: float | None
    showers_mm: float | None
    snowfall_cm: float | None
    precipitation_last_3h_mm: float | None
    sunshine_s: float | None
    shortwave_wm2: float | None
    direct_wm2: float | None
    diffuse_wm2: float | None
    weather_code: int | None
    is_day: bool | None

    @property
    def sun_fraction(self) -> float | None:
        """Share of the covering hour with direct sunshine (WMO: DNI > 120 W/m²)."""
        if self.sunshine_s is None:
            return None
        return max(0.0, min(1.0, self.sunshine_s / 3600))

    @property
    def diffuse_fraction(self) -> float | None:
        """Diffuse / global radiation: close to 1 = soft, overcast light; low = hard sunlight."""
        if self.shortwave_wm2 is None or self.diffuse_wm2 is None:
            return None
        if self.shortwave_wm2 < MIN_RADIATION_FOR_RATIO:
            return None
        return max(0.0, min(1.0, self.diffuse_wm2 / self.shortwave_wm2))


def weather_sources(t_utc: datetime, *, now: datetime) -> tuple[WeatherSource, ...]:
    """Providers to try, in order. A capture in the future (wrong clock) gets none."""
    if t_utc > now + HOUR:
        return ()
    if t_utc.date() >= HISTORICAL_FORECAST_START:
        return (WeatherSource.HISTORICAL_FORECAST, WeatherSource.ARCHIVE)
    return (WeatherSource.ARCHIVE,)


def request_dates(t_utc: datetime) -> tuple[date, date]:
    """UTC dates to request so that both brackets and the covering hours exist around midnight."""
    return (t_utc - WET_GROUND_HOURS * HOUR).date(), (t_utc + 2 * HOUR).date()


def is_provisional(t_utc: datetime, *, now: datetime) -> bool:
    return now - t_utc < PROVISIONAL_FOR


def is_settled(t_utc: datetime, *, now: datetime) -> bool:
    """Old enough for the model data to be final (a refetch would give the same values)."""
    return now - t_utc >= SETTLED_AFTER


def saturation_vapour_pressure(t_c: float) -> float:
    """Magnus formula (Alduchov & Eskridge 1996), hPa."""
    return 6.1094 * math.exp(17.625 * t_c / (t_c + 243.04))


def relative_humidity(t_c: float, dew_point_c: float) -> float:
    return max(
        0.0,
        min(100.0, 100 * saturation_vapour_pressure(dew_point_c) / saturation_vapour_pressure(t_c)),
    )


def lerp(a: float | None, b: float | None, f: float) -> float | None:
    """Linear interpolation; with one bracket missing, only the nearest side is trusted."""
    if a is None and b is None:
        return None
    if a is None:
        return b if f >= 0.5 else None
    if b is None:
        return a if f < 0.5 else None
    return a + (b - a) * f


def wind_direction(
    d0: float | None, s0: float | None, d1: float | None, s1: float | None, f: float
) -> float | None:
    """Interpolate a meteorological direction (where the wind comes FROM) as a vector.

    350° → 10° goes through 0°, and two opposite light winds give no direction at all.
    """
    if d0 is None or d1 is None:
        return lerp(d0, d1, f)  # nearest side only
    w0 = 1.0 if s0 is None else s0
    w1 = 1.0 if s1 is None else s1
    u = (1 - f) * -w0 * math.sin(math.radians(d0)) + f * -w1 * math.sin(math.radians(d1))
    v = (1 - f) * -w0 * math.cos(math.radians(d0)) + f * -w1 * math.cos(math.radians(d1))
    if math.hypot(u, v) < CALM_WIND_KMH:
        return None
    return (math.degrees(math.atan2(-u, -v)) + 360) % 360


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _sum(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known), 2) if len(known) == len(values) else None


def weather_at(series: HourlySeries, t_utc: datetime) -> WeatherAt | None:
    """Model weather at ``t_utc``; ``None`` when the series does not cover that instant."""
    t = t_utc.astimezone(UTC)
    floor = t.replace(minute=0, second=0, microsecond=0)
    cover = floor if t == floor else floor + HOUR
    i0, i1 = series.index(floor), series.index(cover)
    if i0 is None or i1 is None:
        return None
    f = (t - floor).total_seconds() / 3600

    def linear(name: str) -> float | None:
        return lerp(series.get(name, i0), series.get(name, i1), f)

    def covering(name: str) -> float | None:
        return series.get(name, i1)

    temperature, dew_point = linear("temperature_2m"), linear("dew_point_2m")
    humidity = (
        relative_humidity(temperature, dew_point)
        if temperature is not None and dew_point is not None
        else linear("relative_humidity_2m")
    )
    code = covering("weather_code")
    is_day = series.get("is_day", i0 if f < 0.5 else i1)  # instant: nearest sample
    last_hours = [
        series.get("precipitation", series.index(cover - k * HOUR)) for k in range(WET_GROUND_HOURS)
    ]
    return WeatherAt(
        at_utc=t,
        temperature_c=_round(temperature),
        dew_point_c=_round(dew_point),
        relative_humidity_pct=_round(humidity, 0),
        apparent_temperature_c=_round(linear("apparent_temperature")),
        pressure_hpa=_round(linear("pressure_msl")),
        cloud_cover_pct=_round(linear("cloud_cover"), 0),
        cloud_low_pct=_round(linear("cloud_cover_low"), 0),
        cloud_mid_pct=_round(linear("cloud_cover_mid"), 0),
        cloud_high_pct=_round(linear("cloud_cover_high"), 0),
        visibility_m=_round(linear("visibility"), 0),
        wind_speed_kmh=_round(linear("wind_speed_10m")),
        wind_direction_deg=_round(
            wind_direction(
                series.get("wind_direction_10m", i0),
                series.get("wind_speed_10m", i0),
                series.get("wind_direction_10m", i1),
                series.get("wind_speed_10m", i1),
                f,
            ),
            0,
        ),
        wind_gusts_kmh=_round(covering("wind_gusts_10m")),
        precipitation_mm=_round(covering("precipitation"), 2),
        rain_mm=_round(covering("rain"), 2),
        showers_mm=_round(covering("showers"), 2),
        snowfall_cm=_round(covering("snowfall"), 2),
        precipitation_last_3h_mm=_sum(last_hours),
        sunshine_s=_round(covering("sunshine_duration"), 0),
        shortwave_wm2=_round(covering("shortwave_radiation")),
        direct_wm2=_round(covering("direct_radiation")),
        diffuse_wm2=_round(covering("diffuse_radiation")),
        weather_code=None if code is None else int(code),
        is_day=None if is_day is None else bool(is_day),
    )


def hours_around(
    series: HourlySeries, t_utc: datetime, *, span: int = 1
) -> list[dict[str, object]]:
    """Raw samples from ``span`` hours before the capture hour to ``span`` hours after its
    covering hour: shown when the capture time is only probable (± 1 h)."""
    floor = t_utc.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    rows: list[dict[str, object]] = []
    for k in range(-span, span + 2):
        i = series.index(floor + k * HOUR)
        if i is None:
            continue
        row: dict[str, object] = {"t": series.times[i].isoformat()}
        for name in ("temperature_2m", "cloud_cover", "precipitation", "weather_code"):
            row[name] = series.get(name, i)
        rows.append(row)
    return rows


def grid_distance_km(lat: float, lon: float, grid_lat: float, grid_lon: float) -> float:
    """Distance from the requested point to the model grid cell that answered."""
    return round(haversine_m(GeoPoint(lat, lon), GeoPoint(grid_lat, grid_lon)) / 1000, 1)
