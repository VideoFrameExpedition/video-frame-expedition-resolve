"""Context stages (place, weather, sun): gating, offline mode, outages, caches."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import structlog

from tests.fakes.context import FakeGeocoder, FakeWeather
from vfe_vision.adapters.geo.nominatim import GeocoderBlockedError
from vfe_vision.api.schemas import ContextOut
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, ServiceUnavailableError, VfeError
from vfe_vision.db.models import (
    ContextPlace,
    ContextSun,
    ContextWeather,
    LibraryRoot,
    ServiceCache,
    Video,
)
from vfe_vision.db.preferences import update_preferences
from vfe_vision.db.session import Database
from vfe_vision.db.translations import TranslationCache
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.place import Place
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.mcp.context_text import context_summary, context_text
from vfe_vision.pipeline.stage import StageContext, StageOutcome, Toolbox, VideoRef
from vfe_vision.pipeline.stages.place import PlaceStage
from vfe_vision.pipeline.stages.sun import SunStage
from vfe_vision.pipeline.stages.weather import WeatherStage
from vfe_vision.services import videos as videos_service
from vfe_vision.services.container import AppContainer
from vfe_vision.services.context import get_context

CAPTURE = datetime(2025, 7, 14, 16, 30, tzinfo=UTC)  # 18:30 in Paris


@dataclass
class FakeGazetteer:
    calls: list[tuple[float, float]] = field(default_factory=list)
    version: str | None = "geonames-test"

    def nearest(self, latitude: float, longitude: float) -> Place:
        self.calls.append((latitude, longitude))
        return Place(
            locality="Paris 16 Passy",
            county="Paris",
            state="Île-de-France",
            country="France",
            country_code="FR",
            approximate=True,
            locality_distance_km=1.4,
        )


@dataclass
class Services:
    weather: FakeWeather = field(default_factory=FakeWeather)
    geocoder: FakeGeocoder = field(default_factory=FakeGeocoder)
    gazetteer: FakeGazetteer = field(default_factory=FakeGazetteer)


def _video(db: Database, tmp_path: Path, **facts: Any) -> VideoRef:
    values: dict[str, Any] = {
        "captured_at": CAPTURE,
        "captured_at_confidence": "high",
        "capture_timezone": "Europe/Paris",
        "latitude": 48.8584,
        "longitude": 2.2945,
        "location_source": "QuickTime:GPSCoordinates",
        "duration_s": 12.0,
    } | facts
    with db.write() as session:
        root = LibraryRoot(path=str(tmp_path), path_key=str(tmp_path).lower(), label="t")
        session.add(root)
        session.flush()
        row = Video(
            root_id=root.id, path=str(tmp_path / "a.mp4"), path_key=str(tmp_path / "a.mp4"),
            rel_path="a.mp4", filename="a.mp4", size_bytes=1, mtime=0.0, fingerprint="f" * 16,
            **values,
        )  # fmt: skip
        session.add(row)
        session.flush()
        return VideoRef(id=row.id, path=Path(row.path), filename=row.filename, fingerprint="f" * 16)


def _ctx(db: Database, video: VideoRef, services: Services, **prefs: Any) -> StageContext:
    tools = cast(
        Toolbox,
        type(
            "T",
            (),
            {
                "db": db,
                "weather": services.weather,
                "geocoder": services.geocoder,
                "gazetteer": services.gazetteer,
            },
        )(),
    )
    return StageContext(
        video=video,
        prefs=AnalysisPreferences(**prefs),
        tools=tools,
        cancel=CancelToken(),
        progress=lambda _f, _m: None,
        log=structlog.get_logger("test"),
    )


def _row(db: Database, model: type[Any], video_id: str) -> Any:
    with db.read() as session:
        return session.get(model, video_id)


# ------------------------------------------------------------------ place
def test_place_online_is_cached_per_grid_cell(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, services)
    outcome = PlaceStage().run(ctx)
    assert outcome.status == StageStatus.SUCCEEDED
    assert services.geocoder.calls == [
        (48.858, 2.295, "fr", False),  # rounded to the ~100 m grid before leaving
        (48.858, 2.295, "fr", True),  # nearby natural feature
    ]
    place = _row(db, ContextPlace, video.id)
    assert place.label == "Paris, Île-de-France, France"
    assert place.data["feature"] is None  # « Unable to geocode » on the natural layer
    PlaceStage().run(ctx)
    assert len(services.geocoder.calls) == 2  # answers (not-found included) are cached
    with db.read() as session:
        assert session.query(ServiceCache).count() == 2


def test_place_offline_uses_the_gazetteer(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    outcome = PlaceStage().run(_ctx(db, video, services, online_services=False))
    assert outcome.status == StageStatus.SUCCEEDED
    assert services.geocoder.calls == []
    place = _row(db, ContextPlace, video.id)
    assert (place.source, place.label) == (
        "offline",
        "près de Paris 16 Passy (≈ 1,4 km), Paris, France",
    )
    assert place.data["approximate"]


@pytest.mark.parametrize(
    "error",
    [
        ServiceUnavailableError("Nominatim injoignable"),
        GeocoderBlockedError("Nominatim a refusé la requête (HTTP 403)"),
        VfeError("Nominatim a refusé la requête (HTTP 400)"),
    ],
)
def test_place_failures_fall_back_and_retry_later(
    db: Database, tmp_path: Path, error: VfeError
) -> None:
    """Outage, 403 (blocked for this session only) or an unusable answer: an approximate place
    is shown now, and the next analysis asks again (a « degraded » success, never cached)."""
    services = Services(geocoder=FakeGeocoder(address_error=error))
    video = _video(db, tmp_path)
    outcome = PlaceStage().run(_ctx(db, video, services))
    assert (outcome.status, outcome.retryable) == (StageStatus.SUCCEEDED, True)
    assert "approximatif" in (outcome.skip_reason or "")
    assert _row(db, ContextPlace, video.id).source == "offline"


def test_place_natural_outage_keeps_the_address_and_retries(db: Database, tmp_path: Path) -> None:
    services = Services(geocoder=FakeGeocoder(natural_error=ServiceUnavailableError("429")))
    video = _video(db, tmp_path)
    outcome = PlaceStage().run(_ctx(db, video, services))
    assert (outcome.status, outcome.retryable) == (StageStatus.SUCCEEDED, True)
    assert _row(db, ContextPlace, video.id).source == "nominatim"
    with db.read() as session:  # the failed natural lookup is not cached
        assert session.query(ServiceCache).count() == 1


def test_place_cancellation_is_not_swallowed(db: Database, tmp_path: Path) -> None:
    services = Services(geocoder=FakeGeocoder(natural_error=CancelledError("Annulé")))
    video = _video(db, tmp_path)
    with pytest.raises(CancelledError):
        PlaceStage().run(_ctx(db, video, services))


def test_place_offline_reuses_cached_answers_without_network(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    PlaceStage().run(_ctx(db, video, services))
    calls = len(services.geocoder.calls)
    offline = _ctx(db, video, services, online_services=False)
    outcome = PlaceStage().run(offline)
    assert outcome.status == StageStatus.SUCCEEDED
    assert len(services.geocoder.calls) == calls  # nothing left the machine
    assert _row(db, ContextPlace, video.id).label == "Paris, Île-de-France, France"
    assert "gazetteer" in PlaceStage().cache_config(offline.prefs, offline)
    assert "gazetteer" not in PlaceStage().cache_config(AnalysisPreferences(), offline)


def test_place_from_a_folder_default_is_approximate(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path, location_source="folder_default")
    PlaceStage().run(_ctx(db, video, services))
    assert [call[3] for call in services.geocoder.calls] == [False]  # no nearby-feature lookup
    assert _row(db, ContextPlace, video.id).data["approximate"] is True


def test_place_without_position_clears_old_rows(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    PlaceStage().run(_ctx(db, video, services))
    with db.write() as session:
        row = session.get_one(Video, video.id)
        row.latitude = row.longitude = None
    outcome = PlaceStage().run(_ctx(db, video, services))
    assert outcome.status == StageStatus.SKIPPED
    assert _row(db, ContextPlace, video.id) is None


# ------------------------------------------------------------------ weather
def test_weather_interpolated_and_cached(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, services)
    outcome = WeatherStage().run(ctx)
    assert outcome.status == StageStatus.SUCCEEDED
    source, lat, lon, start, end = services.weather.calls[0]
    assert (source.value, lat, lon) == ("historical_forecast", 48.86, 2.29)
    assert (start.isoformat(), end.isoformat()) == ("2025-07-14", "2025-07-14")
    row = _row(db, ContextWeather, video.id)
    assert (row.category, row.temperature_c, row.provisional) == ("clear", 24.0, False)
    assert row.data["attribution"].startswith("Weather data by Open-Meteo")
    assert row.data["grid"]["distance_km"] < 2
    assert len(row.data["hours"]) >= 3
    WeatherStage().run(ctx)
    assert len(services.weather.calls) == 1  # settled data: served from the cache


def test_weather_needs_a_probable_time(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path, captured_at_confidence="low")
    outcome = WeatherStage().run(_ctx(db, video, services))
    assert outcome.status == StageStatus.SKIPPED
    assert "incertaine" in (outcome.skip_reason or "")
    assert services.weather.calls == []


def test_weather_offline_and_future_send_nothing(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    offline = WeatherStage().run(_ctx(db, video, services, online_services=False))
    assert offline.status == StageStatus.SKIPPED
    future = _video(db, tmp_path / "f", captured_at=datetime.now(UTC) + timedelta(days=30))
    later = WeatherStage().run(_ctx(db, future, services))
    assert later.status == StageStatus.SKIPPED
    assert "futur" in (later.skip_reason or "")
    assert services.weather.calls == []


def test_going_offline_keeps_the_weather_already_fetched(db: Database, tmp_path: Path) -> None:
    """An update run offline must not destroy what an online analysis fetched."""
    services = Services()
    video = _video(db, tmp_path)
    WeatherStage().run(_ctx(db, video, services))
    offline = WeatherStage().run(_ctx(db, video, services, online_services=False))
    assert offline.status == StageStatus.SKIPPED
    assert _row(db, ContextWeather, video.id).temperature_c == 24.0
    with db.write() as session:  # re-dated while offline: the old values no longer apply
        session.get_one(Video, video.id).captured_at = datetime(2025, 7, 14, 9, 0, tzinfo=UTC)
    WeatherStage().run(_ctx(db, video, services, online_services=False))
    assert _row(db, ContextWeather, video.id) is None


def test_offline_weather_is_dropped_when_the_time_confidence_changed(
    db: Database, tmp_path: Path
) -> None:
    services = Services()
    video = _video(db, tmp_path)
    WeatherStage().run(_ctx(db, video, services))
    assert _row(db, ContextWeather, video.id).data["time_confidence"] == "high"
    with db.write() as session:  # same minute, but the time is now only probable
        session.get_one(Video, video.id).captured_at_confidence = "medium"
    WeatherStage().run(_ctx(db, video, services, online_services=False))
    assert _row(db, ContextWeather, video.id) is None  # its confidence label would be stale


def test_weather_cancellation_is_not_a_refusal(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    WeatherStage().run(_ctx(db, video, services))
    with db.write() as session:
        session.query(ServiceCache).delete()
    ctx = _ctx(db, video, services)
    ctx.cancel.cancel()
    with pytest.raises(CancelledError):
        WeatherStage().run(ctx)
    assert _row(db, ContextWeather, video.id) is not None  # nothing was cleared


def test_weather_outage_drops_values_of_another_capture_time(db: Database, tmp_path: Path) -> None:
    video = _video(db, tmp_path)
    WeatherStage().run(_ctx(db, video, Services()))
    with db.write() as session:
        session.get_one(Video, video.id).captured_at = CAPTURE + timedelta(hours=5)
        session.query(ServiceCache).delete()
    down = Services(weather=FakeWeather(down=True))
    outcome = WeatherStage().run(_ctx(db, video, down))
    assert (outcome.status, outcome.retryable) == (StageStatus.SKIPPED, True)
    assert _row(db, ContextWeather, video.id) is None


def test_switching_offline_during_a_job_stops_requests(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, services)  # the job started online…
    update_preferences(db, {"online_services": False})  # …then the user switched off
    weather = WeatherStage().run(ctx)
    place = PlaceStage().run(ctx)
    assert services.weather.calls == []
    assert services.geocoder.calls == []
    assert (weather.status, weather.retryable) == (StageStatus.SKIPPED, True)
    assert (place.status, place.retryable) == (StageStatus.SUCCEEDED, True)


def test_weather_outage_is_retryable_and_keeps_old_values(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    WeatherStage().run(_ctx(db, video, services))
    with db.write() as session:
        session.query(ServiceCache).delete()
    down = Services(weather=FakeWeather(down=True))
    outcome: StageOutcome = WeatherStage().run(_ctx(db, video, down))
    assert (outcome.status, outcome.retryable) == (StageStatus.SKIPPED, True)
    assert len(down.weather.calls) == 2  # primary, then the archive fallback
    assert _row(db, ContextWeather, video.id) is not None


# ------------------------------------------------------------------ sun
def test_sun_uses_the_weather_regime(db: Database, tmp_path: Path) -> None:
    services = Services()
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, services)
    WeatherStage().run(ctx)
    outcome = SunStage().run(ctx)
    assert outcome.status == StageStatus.SUCCEEDED
    sun = _row(db, ContextSun, video.id)
    assert sun.light_phase == "day"
    assert sun.day_part == "afternoon"
    assert 20 < sun.elevation_deg < 35
    assert sun.data["light"]["regime"] == "sunlit"
    assert "clear_sky" not in sun.data["assumptions"]
    events = sun.data["events"]
    assert events["local_date"] == "2025-07-14"
    assert events["setting"]["sun"].startswith("2025-07-14T19:")  # ~21:5x in Paris


def test_sun_ignores_weather_of_another_minute(db: Database, tmp_path: Path) -> None:
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, Services())
    WeatherStage().run(ctx)
    with db.write() as session:
        session.get_one(ContextWeather, video.id).at_utc = CAPTURE - timedelta(hours=3)
    SunStage().run(ctx)
    assert _row(db, ContextSun, video.id).data["light"]["regime"] == "clear_assumed"


def test_sun_without_weather_assumes_clear_sky(db: Database, tmp_path: Path) -> None:
    video = _video(db, tmp_path)
    SunStage().run(_ctx(db, video, Services()))
    sun = _row(db, ContextSun, video.id)
    assert sun.data["light"]["regime"] == "clear_assumed"
    assert "clear_sky" in sun.data["assumptions"]


def test_sun_probable_time_near_sunset_states_no_phase(db: Database, tmp_path: Path) -> None:
    # 21:10 in Paris on 14 July (golden hour from ~20:55): ± 30 min spans day and golden hour.
    video = _video(
        db, tmp_path, captured_at=datetime(2025, 7, 14, 19, 10, tzinfo=UTC),
        captured_at_confidence="medium",
    )  # fmt: skip
    SunStage().run(_ctx(db, video, Services()))
    sun = _row(db, ContextSun, video.id)
    assert sun.light_phase is None
    assert sun.day_part is None
    assert sun.data["window"]["phases"] == ["day", "golden_hour"]
    assert sun.data["phase_at_instant"] == "golden_hour"


def test_sun_long_take_reports_crossed_phases(db: Database, tmp_path: Path) -> None:
    video = _video(
        db, tmp_path, captured_at=datetime(2025, 7, 14, 19, 0, tzinfo=UTC), duration_s=3600.0
    )
    SunStage().run(_ctx(db, video, Services()))
    sun = _row(db, ContextSun, video.id)
    assert sun.light_phase == "day"  # at the start of the take
    assert sun.data["take"]["phases"][:2] == ["day", "golden_hour"]
    assert "capture_start" in sun.data["assumptions"]
    assert sun.data["light"]["comparable"] is False  # no single expected light for the take


@pytest.mark.parametrize("confidence", ["low", None])
def test_sun_needs_a_probable_time(db: Database, tmp_path: Path, confidence: str | None) -> None:
    video = _video(db, tmp_path, captured_at_confidence=confidence)
    outcome = SunStage().run(_ctx(db, video, Services()))
    assert outcome.status == StageStatus.SKIPPED
    assert _row(db, ContextSun, video.id) is None


def test_stored_context_matches_the_api_contract(db: Database, tmp_path: Path) -> None:
    """Stored JSON must validate against the typed API models, otherwise the API hides it."""
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, Services())
    for stage in (PlaceStage(), WeatherStage(), SunStage()):
        assert stage.run(ctx).status == StageStatus.SUCCEEDED
    container = cast(AppContainer, type("C", (), {"db": db, "translations": TranslationCache()})())
    out = ContextOut.of(get_context(container, video.id))
    assert out.place is not None
    assert out.place.label == "Paris, Île-de-France, France"
    assert out.weather is not None
    assert out.weather.values.temperature_c == 24.0
    assert out.sun is not None
    assert out.sun.events.setting["sun"] is not None
    assert out.sun.light is not None
    assert out.sun.light.regime == "sunlit"
    assert out.notes == {}


def test_context_notes_explain_missing_parts(db: Database, tmp_path: Path) -> None:
    from vfe_vision.db.models import StageRun

    video = _video(db, tmp_path, captured_at_confidence="low")
    with db.write() as session:
        session.add(
            StageRun(
                video_id=video.id, stage="sun", stage_version=1, cache_key="k",
                status=StageStatus.SKIPPED, skip_reason="Heure de tournage trop incertaine",
            )
        )  # fmt: skip
    container = cast(AppContainer, type("C", (), {"db": db, "translations": TranslationCache()})())
    assert get_context(container, video.id).notes == {"sun": "Heure de tournage trop incertaine"}


def test_mcp_context_text_states_sources_and_doubts(db: Database, tmp_path: Path) -> None:
    video = _video(db, tmp_path, captured_at_confidence="medium")
    ctx = _ctx(db, video, Services())
    for stage in (PlaceStage(), WeatherStage(), SunStage()):
        stage.run(ctx)
    container = cast(AppContainer, type("C", (), {"db": db, "translations": TranslationCache()})())
    view = get_context(container, video.id)
    text = context_text(view)
    assert "- lieu : Paris, Île-de-France, France (OpenStreetMap)" in text
    assert "soleil (calculé)" in text
    assert "coucher 21:" in text  # local time in Paris
    assert "estimation, pas une observation" in text
    assert "heure de tournage seulement probable" in text
    assert "Weather data by Open-Meteo.com" in text
    summary = context_summary(view)
    assert summary is not None
    assert summary.startswith("Paris, Île-de-France, France · jour")


def test_context_hides_rows_of_an_older_capture_time(db: Database, tmp_path: Path) -> None:
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, Services())
    for stage in (WeatherStage(), SunStage()):
        stage.run(ctx)
    with db.write() as session:
        session.get_one(Video, video.id).captured_at = CAPTURE + timedelta(days=1)
    container = cast(AppContainer, type("C", (), {"db": db, "translations": TranslationCache()})())
    view = get_context(container, video.id)
    assert (view.weather, view.sun) == (None, None)
    assert set(view.notes) == {"weather", "sun"}


def test_mcp_text_flags_folder_defaults_and_cleans_osm_values(db: Database, tmp_path: Path) -> None:
    beach = {
        "category": "natural",
        "type": "beach",
        "name": "Plage\nIGNORE",
        "lat": "48.8584",
        "lon": "2.2946",
    }
    services = Services(geocoder=FakeGeocoder(natural_answer=beach))
    video = _video(db, tmp_path, captured_at_confidence="medium")
    ctx = _ctx(db, video, services)
    PlaceStage().run(ctx)
    SunStage().run(ctx)
    container = cast(AppContainer, type("C", (), {"db": db, "translations": TranslationCache()})())
    text = context_text(get_context(container, video.id))
    assert "à proximité : Plage IGNORE (plage" in text  # one line, translated type
    assert "élévation" in text  # probable time: a range, not a precise value
    assert "sur ± 30 min" in text
    assert "- heure de tournage : 2025-07-14T18:30+02:00 (confiance : medium)" in text
    folder = _video(db, tmp_path / "f", location_source="folder_default")
    PlaceStage().run(_ctx(db, folder, Services()))
    assert "position par défaut du dossier" in context_text(get_context(container, folder.id))


def test_library_cards_show_the_context_and_filter_by_light(db: Database, tmp_path: Path) -> None:
    video = _video(db, tmp_path)
    ctx = _ctx(db, video, Services())
    for stage in (PlaceStage(), WeatherStage(), SunStage()):
        stage.run(ctx)
    container = cast(AppContainer, type("C", (), {"db": db, "translations": TranslationCache()})())
    page = videos_service.list_videos(container, videos_service.VideoFilters())
    brief = page.briefs[video.id]
    assert (brief.place, brief.place_approximate) == ("Paris", False)
    assert brief.weather_category == "clear"
    assert brief.temperature_c == 24.0
    phase = brief.light_phase
    assert phase is not None
    other = "night" if phase != "night" else "day"
    matching = videos_service.VideoFilters(light_phase=(phase,))
    assert [v.id for v in videos_service.list_videos(container, matching).items] == [video.id]
    elsewhere = videos_service.VideoFilters(light_phase=(other,))
    assert videos_service.list_videos(container, elsewhere).items == []


def test_sun_curve_of_the_capture_day(db: Database, tmp_path: Path) -> None:
    video = _video(db, tmp_path)
    SunStage().run(_ctx(db, video, Services()))
    container = cast(AppContainer, type("C", (), {"db": db, "translations": TranslationCache()})())
    curve = ContextOut.of(get_context(container, video.id)).sun_curve
    assert curve is not None
    assert curve.step_min == 10
    assert len(curve.elevations_deg) == 24 * 6 + 1
    # Paris in July: highest around 13:50 local (solar noon), below the horizon at midnight.
    peak = max(range(len(curve.elevations_deg)), key=curve.elevations_deg.__getitem__)
    local_peak = curve.start_utc + timedelta(minutes=peak * 10, hours=2)
    assert 13 <= local_peak.hour <= 14
    assert 60 < curve.elevations_deg[peak] < 66
    assert curve.elevations_deg[0] < 0
