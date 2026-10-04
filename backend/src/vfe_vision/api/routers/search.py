"""Search: passages of the library by words and by meaning, and the values of its filters."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from vfe_vision.api.deps import Container, DisplayLanguage
from vfe_vision.api.schemas import ChunkKindName, SearchFacetsOut, SearchOut
from vfe_vision.domain.enums import Orientation
from vfe_vision.domain.search_chunks import ChunkKind
from vfe_vision.domain.sun import LightPhase
from vfe_vision.domain.vision import ShotType
from vfe_vision.domain.weather_codes import WeatherCategory
from vfe_vision.services import search

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def search_library(
    c: Container,
    language: DisplayLanguage,
    q: Annotated[str, Query(max_length=500, description="Words or phrase, no syntax.")] = "",
    kind: Annotated[list[ChunkKindName] | None, Query()] = None,
    video_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    place: Annotated[
        str | None, Query(max_length=200, description="Part of the name of the place or country.")
    ] = None,
    weather: Annotated[list[WeatherCategory] | None, Query()] = None,
    light_phase: Annotated[list[LightPhase] | None, Query()] = None,
    device: Annotated[str | None, Query(max_length=200)] = None,
    orientation: Orientation | None = None,
    has_speech: bool | None = None,
    subject: Annotated[
        list[str] | None,
        Query(description="Subjects seen, all required (word start, no accents).", max_length=80),
    ] = None,
    shot_type: Annotated[list[ShotType] | None, Query()] = None,
    min_rating: Annotated[int | None, Query(ge=1, le=5)] = None,
    favorite: bool | None = None,
    root_id: str | None = None,
    folder: Annotated[
        str | None, Query(max_length=1000, description="Folder of root_id and its sub-folders.")
    ] = None,
    min_usability: Annotated[int | None, Query(ge=0, le=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> SearchOut:
    """Search the library: the words (BM25) and the meaning (vectors, when the model is
    installed), fused. Without text, the filters alone list videos (or shots when a filter is
    about shots). The snippets come from the videos: plain text, never HTML."""
    filters = search.SearchFilters(
        kinds=tuple(ChunkKind(k) for k in kind or ()),
        video_id=video_id,
        date_from=date_from,
        date_to=date_to,
        place=place,
        weather=tuple(w.value for w in weather or ()),
        light_phase=tuple(p.value for p in light_phase or ()),
        device=device,
        orientation=orientation,
        has_speech=has_speech,
        subjects=tuple(s for s in subject or () if s.strip()),
        shot_types=tuple(t.value for t in shot_type or ()),
        min_rating=min_rating,
        favorite=favorite,
        root_id=root_id,
        folder=folder if root_id else None,
        min_usability=min_usability,
        language=language,
    )
    result = search.search(c, q, filters, limit=limit, offset=offset)
    return SearchOut.of(result, limit=limit, offset=offset)


@router.get("/facets")
def search_facets(c: Container, language: DisplayLanguage) -> SearchFacetsOut:
    """The filter values present in the index (devices, places, subjects, weather…), and the
    state of the index."""
    return SearchFacetsOut.of(search.search_facets(c, language=language))
