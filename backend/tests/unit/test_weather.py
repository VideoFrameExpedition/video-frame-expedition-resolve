"""Weather at the capture minute: interpolation rules, sources, WMO labels, Open-Meteo parsing."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.adapters.weather.open_meteo import OpenMeteoClient
from vfe_vision.core.errors import ServiceUnavailableError, VfeError
from vfe_vision.domain.weather import (
    HourlySeries,
    WeatherSource,
    grid_distance_km,
    hours_around,
    is_provisional,
    is_settled,
    lerp,
    relative_humidity,
    request_dates,
    weather_at,
    weather_sources,
    wind_direction,
)
from vfe_vision.domain.weather_codes import (
    KNOWN_CODES,
    WeatherCategory,
    weather_category,
    weather_label,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "open_meteo"
CAPTURE = datetime(2026, 8, 26, 15, 58, tzinfo=UTC)


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _parse(source: WeatherSource, name: str) -> HourlySeries:
    return OpenMeteoClient().parse(source, _load(name)).series


def _series(hours: int, start: datetime, **columns: list[float | None]) -> HourlySeries:
    times = tuple(start + timedelta(hours=k) for k in range(hours))
    return HourlySeries(times=times, values=columns, units={})


# ------------------------------------------------------------------ real responses (Paris)
def test_historical_forecast_at_capture_minute() -> None:
    weather = weather_at(
        _parse(WeatherSource.HISTORICAL_FORECAST, "historical_forecast_paris_2026-08-26.json"),
        CAPTURE,
    )
    assert weather is not None
    # Instant variables: 15:00 → 16:00 at f = 58/60.
    assert weather.temperature_c == 28.7
    assert weather.dew_point_c == 13.8
    assert weather.relative_humidity_pct == 40  # recomputed from T and Td (Magnus)
    assert weather.pressure_hpa == 1010.4
    assert (weather.cloud_cover_pct, weather.cloud_mid_pct, weather.cloud_high_pct) == (
        100,
        67,
        100,
    )
    assert weather.visibility_m == 42721
    assert weather.wind_speed_kmh == 10.4
    assert weather.wind_direction_deg == 136
    # Preceding-hour variables: the 16:00 sample covers (15:00, 16:00].
    assert weather.sunshine_s == 1142
    assert weather.shortwave_wm2 == 273.0
    assert weather.diffuse_wm2 == 223.0
    assert weather.wind_gusts_kmh == 23.0
    assert weather.weather_code == 3
    assert weather.precipitation_last_3h_mm == 0.0
    assert weather.is_day is True
    assert weather.sun_fraction == pytest.approx(1142 / 3600)
    assert weather.diffuse_fraction == pytest.approx(223 / 273)


def test_archive_has_no_visibility_and_differs_from_the_forecast_model() -> None:
    weather = weather_at(_parse(WeatherSource.ARCHIVE, "archive_paris_2026-08-26.json"), CAPTURE)
    assert weather is not None
    assert weather.visibility_m is None  # never returned by the archive: unknown, not zero
    assert weather.temperature_c == 29.1
    assert weather.relative_humidity_pct == 38
    assert weather.sunshine_s == 3600  # the models disagree: weather is context, not a fact
    assert weather.wind_direction_deg == 159


def test_parse_keeps_grid_cell_and_units() -> None:
    response = OpenMeteoClient().parse(
        WeatherSource.HISTORICAL_FORECAST, _load("historical_forecast_paris_2026-08-26.json")
    )
    assert response.grid_latitude == pytest.approx(48.86)
    assert response.series.units["temperature_2m"] == "°C"
    assert response.series.times[0] == datetime(2026, 8, 26, tzinfo=UTC)
    assert grid_distance_km(48.8584, 2.2945, 48.86, 2.3) == pytest.approx(0.4, abs=0.1)


def test_parse_rejects_local_time_responses() -> None:
    raw = _load("archive_paris_2026-08-26.json") | {"utc_offset_seconds": 7200}
    with pytest.raises(VfeError):
        OpenMeteoClient().parse(WeatherSource.ARCHIVE, raw)


# ------------------------------------------------------------------ interpolation rules
def test_exact_hour_uses_that_sample_for_everything() -> None:
    start = datetime(2026, 1, 1, 10, tzinfo=UTC)
    series = _series(3, start, temperature_2m=[10.0, 20.0, 30.0], precipitation=[1.0, 2.0, 3.0])
    weather = weather_at(series, start + timedelta(hours=1))
    assert weather is not None
    assert weather.temperature_c == 20.0
    assert weather.precipitation_mm == 2.0  # the hour ending at 11:00


def test_covering_hour_is_never_interpolated() -> None:
    start = datetime(2026, 1, 1, 10, tzinfo=UTC)
    series = _series(2, start, precipitation=[0.0, 4.0], weather_code=[0.0, 63.0])
    weather = weather_at(series, start + timedelta(minutes=1))
    assert weather is not None
    assert weather.precipitation_mm == 4.0
    assert weather.weather_code == 63


def test_missing_bracket_uses_nearest_side_only() -> None:
    assert lerp(None, 5.0, 0.7) == 5.0
    assert lerp(None, 5.0, 0.3) is None
    assert lerp(5.0, None, 0.3) == 5.0
    assert lerp(None, None, 0.5) is None


def test_capture_outside_the_series_gives_nothing() -> None:
    series = _series(2, datetime(2026, 1, 1, 10, tzinfo=UTC), temperature_2m=[1.0, 2.0])
    assert weather_at(series, datetime(2026, 1, 1, 12, 30, tzinfo=UTC)) is None


def test_wet_ground_needs_the_three_hours() -> None:
    start = datetime(2026, 1, 1, 8, tzinfo=UTC)
    series = _series(4, start, precipitation=[9.0, 1.0, 0.5, 0.25])
    weather = weather_at(series, start + timedelta(hours=2, minutes=30))
    assert weather is not None
    assert weather.precipitation_last_3h_mm == 1.75  # 11:00 + 10:00 + 09:00
    short = weather_at(series, start + timedelta(minutes=30))
    assert short is not None
    assert short.precipitation_last_3h_mm is None  # 07:00 unknown: no partial sum


def test_wind_direction_goes_through_north() -> None:
    assert wind_direction(350, 10, 10, 10, 0.5) == pytest.approx(0, abs=1e-6)
    assert wind_direction(90, 10, 270, 10, 0.5) is None  # opposite equal winds: calm
    assert wind_direction(None, None, 180, 5, 0.8) == 180


@given(
    d0=st.floats(0, 359.9),
    d1=st.floats(0, 359.9),
    s=st.floats(1, 80),
    f=st.floats(0, 1),
)
def test_wind_direction_stays_on_the_shorter_arc(d0: float, d1: float, s: float, f: float) -> None:
    result = wind_direction(d0, s, d1, s, f)
    gap = abs((d1 - d0 + 180) % 360 - 180)
    u = s * ((1 - f) * math.sin(math.radians(d0)) + f * math.sin(math.radians(d1)))
    v = s * ((1 - f) * math.cos(math.radians(d0)) + f * math.cos(math.radians(d1)))
    if result is None:
        assert math.hypot(u, v) < 0.5 + 1e-9  # only a (near) calm mean wind has no direction
        return
    assert 0 <= result < 360
    to_start = abs((result - d0 + 180) % 360 - 180)
    to_end = abs((result - d1 + 180) % 360 - 180)
    assert to_start <= gap + 1e-6
    assert to_end <= gap + 1e-6


@given(t=st.floats(-30, 45), spread=st.floats(0, 30))
def test_relative_humidity_is_bounded_and_saturates(t: float, spread: float) -> None:
    rh = relative_humidity(t, t - spread)
    assert 0 <= rh <= 100
    assert math.isclose(relative_humidity(t, t), 100)


# ------------------------------------------------------------------ sources and dates
def test_sources_by_capture_date() -> None:
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)
    recent = weather_sources(datetime(2026, 9, 23, tzinfo=UTC), now=now)
    assert recent == (WeatherSource.HISTORICAL_FORECAST, WeatherSource.ARCHIVE)
    assert weather_sources(datetime(2016, 5, 1, tzinfo=UTC), now=now) == (WeatherSource.ARCHIVE,)
    assert weather_sources(datetime(2026, 10, 1, tzinfo=UTC), now=now) == ()  # wrong clock


def test_request_dates_cover_midnight() -> None:
    assert request_dates(datetime(2026, 3, 1, 23, 30, tzinfo=UTC)) == (
        date(2026, 3, 1),
        date(2026, 3, 2),
    )
    assert request_dates(datetime(2026, 3, 1, 1, 30, tzinfo=UTC)) == (
        date(2026, 2, 28),
        date(2026, 3, 1),
    )


def test_provisional_then_settled() -> None:
    t = datetime(2026, 9, 25, tzinfo=UTC)
    assert is_provisional(t, now=t + timedelta(hours=12))
    assert not is_provisional(t, now=t + timedelta(days=3))
    assert not is_settled(t, now=t + timedelta(days=3))
    assert is_settled(t, now=t + timedelta(days=8))


def test_hours_around_lists_raw_samples() -> None:
    start = datetime(2026, 1, 1, 8, tzinfo=UTC)
    series = _series(6, start, temperature_2m=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    rows = hours_around(series, start + timedelta(hours=2, minutes=10))
    assert [row["temperature_2m"] for row in rows] == [2.0, 3.0, 4.0, 5.0]


# ------------------------------------------------------------------ WMO codes
def test_weather_labels_and_categories() -> None:
    assert weather_label(0) == "Ciel dégagé"
    assert weather_label(0, "en") == "Clear sky"
    assert weather_label(97) == "Orage violent"  # documented, absent from the source enum
    assert weather_label(42) == "Code WMO 42"
    assert weather_label(None) is None
    assert weather_category(81) == WeatherCategory.RAIN
    assert weather_category(48) == WeatherCategory.FOG
    assert {95, 96, 97, 99} <= KNOWN_CODES


# ------------------------------------------------------------------ HTTP client
def _client(handler: Any) -> OpenMeteoClient:
    return OpenMeteoClient(min_interval_s=0, transport=httpx.MockTransport(handler))


def test_client_sends_rounded_coordinates_in_utc() -> None:
    seen: list[httpx.Request] = []
    payload = _load("archive_paris_2026-08-26.json")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    client = _client(handler)
    client.hourly(WeatherSource.ARCHIVE, 48.858412, 2.294532, date(2026, 8, 26), date(2026, 8, 26))
    params = seen[0].url.params
    assert (params["latitude"], params["longitude"]) == ("48.86", "2.29")
    assert params["timezone"] == "GMT"
    assert "temperature_2m" in params["hourly"]
    assert "apikey" not in params
    assert seen[0].headers["user-agent"].startswith("vfe-vision/")


@pytest.mark.parametrize("status", [429, 503])
def test_client_rate_limit_and_outage_are_retryable(status: int) -> None:
    client = _client(lambda request: httpx.Response(status, json={"error": True}))
    with pytest.raises(ServiceUnavailableError):
        client.hourly(WeatherSource.ARCHIVE, 1, 2, date(2026, 1, 1), date(2026, 1, 1))


def test_client_out_of_range_is_a_plain_error() -> None:
    body = {"error": True, "reason": "Parameter 'start_date' is out of allowed range"}
    client = _client(lambda request: httpx.Response(400, json=body))
    with pytest.raises(VfeError, match="out of allowed range") as info:
        client.hourly(WeatherSource.ARCHIVE, 1, 2, date(2030, 1, 1), date(2030, 1, 1))
    assert not isinstance(info.value, ServiceUnavailableError)


def test_client_network_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(ServiceUnavailableError):
        _client(handler).hourly(WeatherSource.ARCHIVE, 1, 2, date(2026, 1, 1), date(2026, 1, 1))
