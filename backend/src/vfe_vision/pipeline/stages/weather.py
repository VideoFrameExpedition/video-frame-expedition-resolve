"""Stage ``weather``: model weather at the capture minute from Open-Meteo.

Only rounded coordinates and a UTC date leave the machine, and only when online services are
enabled. A capture time of low confidence gives no weather at all; a probable one keeps the
surrounding hours so the interface can show the range.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from vfe_vision.adapters.weather.open_meteo import (
    ATTRIBUTION,
    ATTRIBUTION_URL,
    COORDINATE_DECIMALS,
    COPERNICUS_NOTICE,
    rounded,
)
from vfe_vision.core.errors import CancelledError, ServiceUnavailableError, VfeError
from vfe_vision.db.models import ContextWeather
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.service_cache import cache_get, cache_put
from vfe_vision.domain.geo import GeoPoint
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.weather import (
    VARIABLES_VERSION,
    WeatherAt,
    WeatherSource,
    grid_distance_km,
    hours_around,
    is_provisional,
    is_settled,
    request_dates,
    weather_at,
    weather_sources,
)
from vfe_vision.domain.weather_codes import weather_category
from vfe_vision.pipeline.stage import Resource, StageContext, StageFamily, StageOutcome, SyncStage
from vfe_vision.pipeline.stages.capture_facts import CaptureFacts, read_capture_facts
from vfe_vision.ports.weather import WeatherResponse

SCHEMA_VERSION = 1
SERVICE = "open-meteo"
# A cached answer is reused when it was fetched after the data settled, or very recently.
FRESH_FOR = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class _Query:
    utc: datetime
    point: GeoPoint
    confidence: str | None

    @property
    def rounded(self) -> tuple[float, float]:
        return rounded(self.point.latitude), rounded(self.point.longitude)


class WeatherStage(SyncStage):
    name = "weather"
    version = 1
    family = StageFamily.CONTEXT
    after = ("metadata",)
    resource = Resource.NETWORK
    optional = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {"online": prefs.online_services, "variables": VARIABLES_VERSION}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        facts = read_capture_facts(ctx)
        now = datetime.now(UTC)
        return {
            "point": facts.point_key(COORDINATE_DECIMALS),
            **facts.time_key(),
            # Flips once: recent model data is refetched when it has settled.
            "settled": facts.utc is not None and is_settled(facts.utc, now=now),
        }

    def run(self, ctx: StageContext) -> StageOutcome:
        facts = read_capture_facts(ctx)
        reason = _not_applicable(facts)
        if reason is not None:
            _clear(ctx)
            return StageOutcome.skipped(reason, permanent=True)
        if facts.utc is None or facts.point is None:  # already excluded, narrows the types
            return StageOutcome.skipped("Heure ou position de tournage inconnue", permanent=True)
        query = _Query(facts.utc, facts.point, facts.confidence and facts.confidence.value)
        if not ctx.prefs.online_services:
            # What an earlier analysis fetched for this very minute and place stays. The switch
            # is a setting of this stage: the next analysis online fetches what is missing.
            _clear_unless_same(ctx, query)
            return StageOutcome.skipped("Services en ligne désactivés (mode hors-ligne)")
        now = datetime.now(UTC)
        sources = weather_sources(query.utc, now=now)
        if not sources:
            _clear(ctx)
            return StageOutcome.skipped("Date de tournage dans le futur : horloge de l'appareil ?")
        unavailable: list[str] = []
        refused: list[str] = []
        for source in sources:
            try:
                response, fetched_at = self._fetch(ctx, source, query, now=now)
            except CancelledError:
                raise  # a cancellation is not a refusal: the runner records it as such
            except ServiceUnavailableError as exc:
                unavailable.append(exc.detail)
                continue
            except VfeError as exc:
                refused.append(exc.detail)
                continue
            weather = weather_at(response.series, query.utc)
            if weather is None or weather.temperature_c is None:
                refused.append(f"{source.value} : aucune donnée à l'heure du tournage")
                continue
            self._persist(ctx, query, response, weather, fetched_at=fetched_at, now=now)
            return StageOutcome.ok(
                source=source.value,
                weather_code=weather.weather_code,
                temperature_c=weather.temperature_c,
                provisional=is_provisional(query.utc, now=now),
            )
        if unavailable:  # at least one provider may answer later
            _clear_unless_same(ctx, query)
            return StageOutcome.skipped(
                "Open-Meteo indisponible : " + "; ".join(unavailable), retryable=True
            )
        _clear(ctx)
        return StageOutcome.skipped("Pas de météo pour ce tournage : " + "; ".join(refused))

    @staticmethod
    def _fetch(
        ctx: StageContext, source: WeatherSource, query: _Query, *, now: datetime
    ) -> tuple[WeatherResponse, datetime]:
        start, end = request_dates(query.utc)
        lat, lon = query.rounded
        key = f"{SERVICE}:{source.value}:{lat:.2f}:{lon:.2f}:{start}:{end}:v{VARIABLES_VERSION}"
        cached = cache_get(ctx.tools.db, key)
        if cached is not None and (
            is_settled(query.utc, now=cached.fetched_at) or now - cached.fetched_at < FRESH_FOR
        ):
            return ctx.tools.weather.parse(source, cached.response), cached.fetched_at
        ctx.cancel.raise_if_cancelled()
        if not load_preferences(ctx.tools.db).online_services:  # switched off during the job
            raise ServiceUnavailableError("Services en ligne désactivés pendant l'analyse")
        response = ctx.tools.weather.hourly(source, lat, lon, start, end)
        fetched_at = cache_put(ctx.tools.db, key, SERVICE, response.raw)
        return response, fetched_at

    @staticmethod
    def _persist(
        ctx: StageContext,
        query: _Query,
        response: WeatherResponse,
        weather: WeatherAt,
        *,
        fetched_at: datetime,
        now: datetime,
    ) -> None:
        values = {k: v for k, v in dataclasses.asdict(weather).items() if k != "at_utc"}
        lat, lon = query.rounded
        grid = None
        if response.grid_latitude is not None and response.grid_longitude is not None:
            grid = {
                "latitude": response.grid_latitude,
                "longitude": response.grid_longitude,
                "elevation_m": response.elevation_m,
                # From the real position (not the rounded query) to the model cell.
                "distance_km": grid_distance_km(
                    query.point.latitude,
                    query.point.longitude,
                    response.grid_latitude,
                    response.grid_longitude,
                ),
            }
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "values": values,
            "sun_fraction": weather.sun_fraction,
            "diffuse_fraction": weather.diffuse_fraction,
            "units": dict(response.series.units),
            "hours": hours_around(response.series, query.utc),
            "query": {"latitude": lat, "longitude": lon},
            "grid": grid,
            "time_confidence": query.confidence,
            "attribution": ATTRIBUTION,
            "attribution_url": ATTRIBUTION_URL,
            "notice": COPERNICUS_NOTICE if response.source == WeatherSource.ARCHIVE else None,
        }
        category = weather_category(weather.weather_code)
        with ctx.tools.db.write() as session:
            row = session.get(ContextWeather, ctx.video.id) or ContextWeather(video_id=ctx.video.id)
            row.source = response.source.value
            row.at_utc = query.utc
            row.weather_code = weather.weather_code
            row.category = None if category is None else category.value
            row.temperature_c = weather.temperature_c
            row.cloud_cover_pct = weather.cloud_cover_pct
            row.provisional = is_provisional(query.utc, now=now)
            row.data = data
            row.fetched_at = fetched_at
            session.add(row)


def _not_applicable(facts: CaptureFacts) -> str | None:
    if facts.point is None:
        return "Position de tournage inconnue"
    if not facts.time_is_usable:
        return "Heure de tournage trop incertaine pour la météo"
    return None


def _clear_unless_same(ctx: StageContext, query: _Query) -> None:
    """During an outage, the stored weather stays only if it is for this very minute and place,
    with the same confidence in the capture time (it is shown next to the weather)."""
    lat, lon = query.rounded
    with ctx.tools.db.read() as session:
        row = session.get(ContextWeather, ctx.video.id)
        same = row is not None and (
            row.at_utc.replace(second=0, microsecond=0)
            == query.utc.replace(second=0, microsecond=0)
            and row.data.get("query") == {"latitude": lat, "longitude": lon}
            and row.data.get("time_confidence") == query.confidence
        )
    if not same:
        _clear(ctx)


def _clear(ctx: StageContext) -> None:
    """A stale weather row must not survive a new, weather-less analysis."""
    with ctx.tools.db.write() as session:
        session.execute(sa.delete(ContextWeather).where(ContextWeather.video_id == ctx.video.id))
