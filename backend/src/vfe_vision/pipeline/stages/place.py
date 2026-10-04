"""Stage ``place``: where the video was shot (Nominatim, or the offline gazetteer).

A place needs no capture time, only a position. Online, the point rounded to a ~100 m grid is
sent to Nominatim (answers cached forever, « not found » included); a nearby beach, peak or island
comes from a second, filtered lookup. Offline, or when Nominatim cannot be reached, the GeoNames
gazetteer gives the nearest locality, worded as approximate.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import sqlalchemy as sa

from vfe_vision.adapters.geo.nominatim import GeocoderBlockedError
from vfe_vision.core.errors import CancelledError, ServiceUnavailableError, VfeError
from vfe_vision.db.models import ContextPlace
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.service_cache import cache_get, cache_put
from vfe_vision.domain.geo import GeoPoint
from vfe_vision.domain.place import (
    GEONAMES_ATTRIBUTION,
    GRID_DECIMALS,
    OSM_ATTRIBUTION,
    Place,
    grid_e3,
    natural_feature,
    parse_nominatim,
)
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.stage import Resource, StageContext, StageFamily, StageOutcome, SyncStage
from vfe_vision.pipeline.stages.capture_facts import CaptureFacts, read_capture_facts

SCHEMA_VERSION = 1
SERVICE = "nominatim"
GRID_PRECISION_M = 70  # worst-case shift of the point sent to the service
OFFLINE = "offline"
FOLDER_DEFAULT = "folder_default"


def _key(kind: str, lat_e3: int, lon_e3: int, language: str) -> str:
    return f"{SERVICE}:{kind}:{lat_e3}:{lon_e3}:{language}"


class PlaceStage(SyncStage):
    name = "place"
    version = 1
    family = StageFamily.CONTEXT
    after = ("metadata",)
    resource = Resource.NETWORK
    optional = True

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        config: dict[str, Any] = {"online": prefs.online_services, "language": prefs.language}
        if not prefs.online_services:  # the offline answer depends on the installed extract
            config["gazetteer"] = ctx.tools.gazetteer.version
        return config

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        facts = read_capture_facts(ctx)
        return {"point": facts.point_key(GRID_DECIMALS), "location_source": facts.location_source}

    def run(self, ctx: StageContext) -> StageOutcome:
        facts = read_capture_facts(ctx)
        if facts.point is None:
            _clear(ctx)
            return StageOutcome.skipped("Position de tournage inconnue", permanent=True)
        point = facts.point
        # A folder's default position is not where this clip was shot: no nearby feature, and
        # the place is shown as approximate.
        folder_default = facts.location_source == FOLDER_DEFAULT
        if not ctx.prefs.online_services:
            place = self._from_cache(ctx, point, with_feature=not folder_default)
            source = SERVICE
            if place is None:  # nothing looked up earlier for this cell: local gazetteer
                place, source = (
                    ctx.tools.gazetteer.nearest(point.latitude, point.longitude),
                    OFFLINE,
                )
            self._persist(ctx, place, source=source, folder_default=folder_default, facts=facts)
            label = place.label(ctx.prefs.language)
            if source == OFFLINE:  # approximate: the precise place is looked up once back online
                return StageOutcome.provisional(source=source, label=label)
            return StageOutcome.ok(source=source, label=label)
        try:
            place, note = self._online(ctx, point, with_feature=not folder_default)
        except CancelledError:
            raise
        except VfeError as exc:  # unreachable, blocked (403), or an unusable answer
            place = ctx.tools.gazetteer.nearest(point.latitude, point.longitude)
            self._persist(ctx, place, source=OFFLINE, folder_default=folder_default, facts=facts)
            return StageOutcome.degraded(f"{exc.detail} : lieu approximatif hors-ligne")
        self._persist(ctx, place, source=SERVICE, folder_default=folder_default, facts=facts)
        if note is not None:
            return StageOutcome.degraded(note, source=SERVICE)
        return StageOutcome.ok(source=SERVICE, label=place.label(ctx.prefs.language))

    def _online(
        self, ctx: StageContext, point: GeoPoint, *, with_feature: bool
    ) -> tuple[Place, str | None]:
        """The address place, and a note when the nearby-feature lookup must be redone."""
        lat_e3, lon_e3 = grid_e3(point.latitude, point.longitude)
        address = self._lookup(ctx, lat_e3, lon_e3, natural=False)
        place = parse_nominatim(address)
        if place is None:  # « Unable to geocode »: open sea, or nothing mapped here
            return self._unmapped(ctx, point), None
        if not with_feature:
            return place, None
        try:
            natural = self._lookup(ctx, lat_e3, lon_e3, natural=True)
        except CancelledError:
            raise
        except (ServiceUnavailableError, GeocoderBlockedError) as exc:
            return place, f"Élément naturel voisin non vérifié : {exc.detail}"
        except VfeError:  # an unusable answer: the address is enough
            return place, None
        return dataclasses.replace(place, feature=natural_feature(point, natural)), None

    def _from_cache(
        self, ctx: StageContext, point: GeoPoint, *, with_feature: bool
    ) -> Place | None:
        """Offline: reuse Nominatim answers already stored for this cell (no network)."""
        lat_e3, lon_e3 = grid_e3(point.latitude, point.longitude)
        language = ctx.prefs.language
        address = cache_get(ctx.tools.db, _key("address", lat_e3, lon_e3, language))
        if address is None:
            return None
        place = parse_nominatim(address.response)
        if place is None:
            return self._unmapped(ctx, point)
        natural = cache_get(ctx.tools.db, _key("natural", lat_e3, lon_e3, language))
        if with_feature and natural is not None:
            place = dataclasses.replace(place, feature=natural_feature(point, natural.response))
        return place

    @staticmethod
    def _unmapped(ctx: StageContext, point: GeoPoint) -> Place:
        offline = ctx.tools.gazetteer.nearest(point.latitude, point.longitude)
        return Place(
            locality=None,
            country=offline.country,
            country_code=offline.country_code,
            at_sea=offline.at_sea,
        )

    @staticmethod
    def _lookup(ctx: StageContext, lat_e3: int, lon_e3: int, *, natural: bool) -> dict[str, Any]:
        language = ctx.prefs.language
        key = _key("natural" if natural else "address", lat_e3, lon_e3, language)
        cached = cache_get(ctx.tools.db, key)
        if cached is not None:
            return cached.response
        ctx.cancel.raise_if_cancelled()
        if not load_preferences(ctx.tools.db).online_services:  # switched off during the job
            raise ServiceUnavailableError("Services en ligne désactivés pendant l'analyse")
        payload = ctx.tools.geocoder.reverse(
            lat_e3 / 1000,
            lon_e3 / 1000,
            language=language,
            natural=natural,
            email=ctx.prefs.nominatim_email,
        )
        cache_put(ctx.tools.db, key, SERVICE, payload)
        return payload

    @staticmethod
    def _persist(
        ctx: StageContext,
        place: Place,
        *,
        source: str,
        folder_default: bool,
        facts: CaptureFacts,
    ) -> None:
        language = ctx.prefs.language
        location_source = facts.location_source
        feature = None if place.feature is None else dataclasses.asdict(place.feature)
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "sublocality": place.sublocality,
            "road": place.road,
            "postcode": place.postcode,
            "county": place.county,
            "state": place.state,
            "iso3166_2": place.iso3166_2,
            "display_name": place.display_name,
            "feature": feature,
            "at_sea": place.at_sea,
            "approximate": place.approximate or folder_default,
            "locality_distance_km": place.locality_distance_km,
            "language": language,
            "location_source": location_source,
            "grid_precision_m": GRID_PRECISION_M if source == SERVICE else None,
            "attribution": OSM_ATTRIBUTION if source == SERVICE else GEONAMES_ATTRIBUTION,
        }
        with ctx.tools.db.write() as session:
            row = session.get(ContextPlace, ctx.video.id) or ContextPlace(video_id=ctx.video.id)
            row.source = source
            row.label = place.label(language)
            row.locality = place.locality
            row.region = place.region
            row.country = place.country
            row.country_code = place.country_code
            row.data = data
            session.add(row)


def _clear(ctx: StageContext) -> None:
    with ctx.tools.db.write() as session:
        session.execute(sa.delete(ContextPlace).where(ContextPlace.video_id == ctx.video.id))
