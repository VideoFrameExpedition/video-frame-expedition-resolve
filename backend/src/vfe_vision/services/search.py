"""Search the library, for the web interface and the MCP server alike.

Two retrievers, fused by reciprocal rank (k = 60):
- **words**: SQLite FTS5 and its BM25 over the passages, from a query turned into quoted
  prefix terms (``domain.search_text.fts_query``): FTS syntax never comes from the user;
- **meaning**: the cosine of the query's EmbeddingGemma vector with every passage's, by brute
  force over a matrix the process keeps (``VectorCache``); only when the model is installed and
  the passages have vectors of that very model.

Without the model or its vectors, the words alone answer. Without text, the filters alone list
passages (shots when a filter is about shots, else videos), the most usable first.

Filters are the stored data of each passage (facets written by the ``index`` stage) or of its
video, read now (rating, favourite, folder, device, orientation): a filter whose data does not
exist for a passage leaves that passage out, and a filter left empty is not applied.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

import numpy as np
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from vfe_vision.core.errors import NotFoundError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.db.models import (
    ChunkVector,
    ContextPlace,
    Keyframe,
    SearchChunk,
    Shot,
    TimelineBinItem,
    Video,
    VideoSynthesis,
)
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.timeline_bins import ITEM_KEY
from vfe_vision.domain.enums import Orientation
from vfe_vision.domain.search_chunks import ChunkKind
from vfe_vision.domain.search_text import fold, fts_query, query_terms, rrf, snippet
from vfe_vision.domain.translation import Dictionary
from vfe_vision.pipeline.search_store import replace_video_chunk
from vfe_vision.pipeline.stages.search_index import passages
from vfe_vision.services.container import AppContainer
from vfe_vision.services.reading import texts_in

log = get_logger(__name__)

CANDIDATES = 200  # passages each retriever hands to the fusion
BROWSE_MAX = 500  # passages listed by the filters alone
MAX_QUERY = 500
WORDS, MEANING = "words", "meaning"
USER_TEXT = ("title", "summary", "user_notes")  # the user's fields written in a passage

_FTS = sa.table("search_fts", sa.column("rowid", sa.Integer))


@dataclass(frozen=True, slots=True)
class SearchFilters:
    kinds: tuple[ChunkKind, ...] = ()
    video_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    place: str | None = None  # a part of the place's name or country (accents ignored)
    weather: tuple[str, ...] = ()  # clear, partly_cloudy, overcast, fog, drizzle, rain, snow…
    light_phase: tuple[str, ...] = ()
    device: str | None = None  # as listed by the facets (« samsung Galaxy S26 Ultra »)
    orientation: Orientation | None = None
    has_speech: bool | None = None
    subjects: tuple[str, ...] = ()  # every one of them (word starts, accents ignored)
    shot_types: tuple[str, ...] = ()
    min_rating: int | None = None
    favorite: bool | None = None
    root_id: str | None = None
    folder: str | None = None  # a folder of ``root_id`` and its sub-folders
    min_usability: int | None = None
    timeline_bins: tuple[str, ...] = ()  # the videos of these Resolve timelines
    # The passages' language; None: the analysis language of the application.
    language: str | None = None

    @property
    def about_shots(self) -> bool:
        return bool(
            self.subjects
            or self.shot_types
            or self.min_usability is not None
            or self.has_speech is not None
        )


@dataclass(frozen=True, slots=True)
class IndexState:
    videos: int  # in the library
    indexed: int  # with passages
    passages: int
    vectors: int  # passages with a vector of the installed model
    semantic: bool  # the embedding model is installed
    model: str | None = None

    @property
    def meaning_ready(self) -> bool:
        return self.semantic and self.vectors > 0


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: int
    video_id: str
    filename: str
    path: str
    title: str | None  # the user's title, else the synthesis'
    kind: ChunkKind
    t_start: float | None
    t_end: float | None
    shot_id: str | None
    shot_idx: int | None  # 0-based
    keyframe_id: str | None
    thumb_path: str | None  # the passage's keyframe, else the video's poster
    text: str  # the passage, whole (plain text from the footage and the models)
    snippet: str
    highlights: tuple[tuple[int, int], ...]  # [start, end) in code points of ``snippet``
    score: float
    retrievers: tuple[str, ...]  # words, meaning
    captured_at: datetime | None
    duration_s: float | None
    fps: float | None
    is_vfr: bool
    start_timecode: str | None
    facets: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResult:
    query: str
    hits: list[SearchHit]
    total: int  # passages found, before the page
    state: IndexState
    retrievers: tuple[str, ...]  # those that answered this query
    note: str | None = None  # why the search by meaning did not take part, when it should have


# ---------------------------------------------------------------- state
def index_state(c: AppContainer) -> IndexState:
    embedder = c.embedder()
    model = embedder.model_id if embedder is not None else None
    with c.db.read() as session:
        videos = session.execute(sa.select(sa.func.count()).select_from(Video)).scalar_one()
        indexed, passages = session.execute(
            sa.select(sa.func.count(sa.distinct(SearchChunk.video_id)), sa.func.count())
        ).one()
        vectors = (
            session.execute(
                sa.select(sa.func.count()).where(ChunkVector.model == model)
            ).scalar_one()
            if model
            else 0
        )
    return IndexState(
        videos=int(videos), indexed=int(indexed), passages=int(passages), vectors=int(vectors),
        semantic=embedder is not None, model=model,
    )  # fmt: skip


# ---------------------------------------------------------------- filters
def _facet(key: str) -> ColumnElement[Any]:
    return sa.func.json_extract(SearchChunk.facets, f"$.{key}")


def _any_of(key: str, values: Sequence[str]) -> ColumnElement[bool]:
    """The passage's ``key`` list holds one of ``values``."""
    each = sa.func.json_each(SearchChunk.facets, f"$.{key}").table_valued("value")
    return sa.select(sa.literal(1)).select_from(each).where(each.c.value.in_(values)).exists()


def _word_start(key: str, wanted: str) -> ColumnElement[bool]:
    """An item of the passage's ``key`` list has a word starting with ``wanted`` (folded)."""
    each = sa.func.json_each(SearchChunk.facets, f"$.{key}").table_valued("value")
    return (
        sa.select(sa.literal(1))
        .select_from(each)
        .where(sa.func.instr(" " + each.c.value, " " + wanted) > 0)
        .exists()
    )


def _conditions(filters: SearchFilters) -> list[ColumnElement[bool]]:  # noqa: PLR0912 - one per filter
    where: list[ColumnElement[bool]] = []
    if filters.kinds:
        where.append(SearchChunk.kind.in_([k.value for k in filters.kinds]))
    if filters.video_id:
        where.append(SearchChunk.video_id == filters.video_id)
    if filters.date_from:
        where.append(_facet("date") >= filters.date_from.isoformat())
    if filters.date_to:
        where.append(_facet("date") <= filters.date_to.isoformat())
    if filters.place and fold(filters.place).strip():
        where.append(sa.func.instr(_facet("place"), fold(filters.place).strip()) > 0)
    if filters.weather:
        where.append(_any_of("weather", filters.weather))
    if filters.light_phase:
        where.append(_facet("light_phase").in_(filters.light_phase))
    if filters.device:
        where.append(_device() == filters.device.strip())
    if filters.orientation:
        where.append(Video.orientation == filters.orientation)
    if filters.has_speech is not None:
        where.append(_facet("speech") == int(filters.has_speech))
    subjects = [key for key in (fold(s).strip() for s in filters.subjects) if key]
    where += [_word_start("subject_keys", key) for key in subjects]
    if filters.shot_types:
        where.append(_any_of("shot_types", filters.shot_types))
    if filters.min_rating is not None:
        where.append(Video.rating >= filters.min_rating)
    if filters.favorite is not None:
        where.append(Video.favorite.is_(filters.favorite))
    if filters.root_id:
        where.append(Video.root_id == filters.root_id)
        folder = (filters.folder or "").strip("/")
        if folder:
            where.append(Video.rel_path.istartswith(folder + "/", autoescape=True))
    if filters.min_usability is not None:
        where.append(_facet("usability") >= filters.min_usability)
    if filters.timeline_bins:
        in_bins = sa.select(ITEM_KEY).where(TimelineBinItem.bin_id.in_(filters.timeline_bins))
        where.append(Video.path_key.in_(in_bins))
    if filters.language:
        where.append(in_language(filters.language))
    return where


def in_language(language: str) -> ColumnElement[bool]:
    """The passages to read in ``language``: those written in it, what is said in
    any language, and a video indexed before it had passages in both, in the one it has."""
    written = sa.select(SearchChunk.video_id).where(
        SearchChunk.language == language, SearchChunk.kind != ChunkKind.TRANSCRIPT.value
    )
    return sa.or_(
        SearchChunk.language == language,
        SearchChunk.kind == ChunkKind.TRANSCRIPT.value,
        SearchChunk.video_id.not_in(written),
    )


def with_language(c: AppContainer, filters: SearchFilters) -> SearchFilters:
    """The filters with a language: the one asked for, else the analysis language."""
    if filters.language:
        return filters
    return replace(filters, language=load_preferences(c.db).language)


def _device() -> ColumnElement[str]:
    make = sa.func.trim(sa.func.coalesce(Video.camera_make, ""))
    model = sa.func.trim(sa.func.coalesce(Video.camera_model, ""))
    return sa.func.trim(make + " " + model)


# ---------------------------------------------------------------- search
def search(
    c: AppContainer,
    query: str,
    filters: SearchFilters | None = None,
    *,
    limit: int = 30,
    offset: int = 0,
) -> SearchResult:
    """The passages that answer ``query`` within ``filters``, best first, in the language of the
    filters."""
    filters = with_language(c, filters or SearchFilters())
    query = " ".join(query.split())[:MAX_QUERY]
    state = index_state(c)
    where = _conditions(filters)
    note: str | None = None
    rankings: dict[str, list[int]] = {}
    if query:
        expression = fts_query(query)
        if expression is not None:
            rankings[WORDS] = _by_words(c, expression, where)
        meaning, note = _by_meaning(c, query, where)
        if meaning is not None:
            rankings[MEANING] = meaning
        fused = rrf(rankings)
    else:
        fused = [(chunk_id, 0.0, ()) for chunk_id in _browse(c, where, filters)]
    page = fused[offset : offset + limit]
    hits = _hits(c, page, query, texts_in(c, filters.language))
    return SearchResult(
        query=query,
        hits=hits,
        total=len(fused),
        state=state,
        retrievers=tuple(rankings),
        note=note,
    )


def _select_ids(where: Sequence[ColumnElement[bool]]) -> sa.Select[int]:
    return sa.select(SearchChunk.id).join(Video, Video.id == SearchChunk.video_id).where(*where)


def _by_words(c: AppContainer, expression: str, where: Sequence[ColumnElement[bool]]) -> list[int]:
    stmt = (
        sa.select(_FTS.c.rowid)
        .select_from(
            _FTS.join(SearchChunk, SearchChunk.id == _FTS.c.rowid).join(
                Video, Video.id == SearchChunk.video_id
            )
        )
        .where(sa.text("search_fts MATCH :expression").bindparams(expression=expression), *where)
        .order_by(sa.text("bm25(search_fts)"))
        .limit(CANDIDATES)
    )
    try:
        with c.db.read() as session:
            found: list[Any] = list(session.execute(stmt).scalars())
        return [int(chunk_id) for chunk_id in found]
    except sa.exc.OperationalError as exc:  # never the user's syntax: it is quoted, but safe
        log.warning("full-text search failed", expression=expression, error=str(exc))
        return []


def _by_meaning(
    c: AppContainer, query: str, where: Sequence[ColumnElement[bool]]
) -> tuple[list[int] | None, str | None]:
    """Passages by cosine with the query (None: no search by meaning), and why it was left out
    when the index has vectors it could have used."""
    embedder = c.embedder()
    if embedder is None:
        return None, None
    vectors = c.vectors.get(c.db, embedder.model_id)
    if not len(vectors):
        return None, None
    try:
        wanted = embedder.embed_query(query)
    except VfeError as exc:
        log.warning("query not embedded", error=exc.detail)
        return None, f"Recherche par le sens indisponible : {exc.detail}"
    similarity = vectors.matrix @ wanted
    keep = similarity >= embedder.min_similarity  # the others have nothing to do with it
    if where:
        with c.db.read() as session:
            allowed = np.fromiter(session.execute(_select_ids(where)).scalars(), dtype=np.int64)
        keep &= np.isin(vectors.ids, allowed)
    chosen = np.flatnonzero(keep)
    best = chosen[np.argsort(-similarity[chosen], kind="stable")][:CANDIDATES]
    return [int(vectors.ids[i]) for i in best], None


def _browse(c: AppContainer, where: list[ColumnElement[bool]], filters: SearchFilters) -> list[int]:
    """Passages listed by the filters alone: shots when a filter is about shots, else videos
    (unless kinds are chosen), the most usable and the most recent first."""
    if not filters.kinds:
        kind = ChunkKind.SHOT if filters.about_shots else ChunkKind.VIDEO
        where = [*where, SearchChunk.kind == kind.value]
    stmt = (
        _select_ids(where)
        .order_by(
            _facet("usability").desc().nulls_last(),
            Video.captured_at.desc().nulls_last(),
            Video.filename,
            SearchChunk.t_start.nulls_first(),
        )
        .limit(BROWSE_MAX)
    )
    with c.db.read() as session:
        return [int(chunk_id) for chunk_id in session.execute(stmt).scalars()]


def _hits(
    c: AppContainer,
    page: Sequence[tuple[int, float, tuple[str, ...]]],
    query: str,
    tr: Dictionary,
) -> list[SearchHit]:
    if not page:
        return []
    ids = [chunk_id for chunk_id, _, _ in page]
    title = sa.func.json_extract(VideoSynthesis.data, "$.title")
    stmt = (
        sa.select(SearchChunk, Video, Keyframe.thumb_path, Shot.idx, title)
        .join(Video, Video.id == SearchChunk.video_id)
        .outerjoin(Keyframe, Keyframe.id == SearchChunk.keyframe_id)
        .outerjoin(Shot, Shot.id == SearchChunk.shot_id)
        .outerjoin(VideoSynthesis, VideoSynthesis.video_id == SearchChunk.video_id)
        .where(SearchChunk.id.in_(ids))
    )
    with c.db.read() as session:
        rows = {row[0].id: row for row in session.execute(stmt).all()}
    parsed = query_terms(query)
    hits: list[SearchHit] = []
    for chunk_id, score, found in page:
        row = rows.get(chunk_id)
        if row is None:  # removed meanwhile
            continue
        chunk, video, thumb, shot_idx, generated = row
        cut = snippet(chunk.text, parsed)
        hits.append(
            SearchHit(
                chunk_id=chunk.id, video_id=video.id, filename=video.filename, path=video.path,
                title=video.title or (tr(str(generated)) if generated else None),
                kind=ChunkKind(chunk.kind), t_start=chunk.t_start, t_end=chunk.t_end,
                shot_id=chunk.shot_id, shot_idx=shot_idx, keyframe_id=chunk.keyframe_id,
                thumb_path=thumb or video.poster_path, text=chunk.text, snippet=cut.text,
                highlights=cut.highlights, score=round(score, 6), retrievers=found,
                captured_at=video.captured_at, duration_s=video.duration_s, fps=video.fps,
                is_vfr=bool(video.is_vfr), start_timecode=video.start_timecode,
                facets=dict(chunk.facets or {}),
            )
        )  # fmt: skip
    return hits


# ---------------------------------------------------------------- shots
TIMED = (ChunkKind.SHOT, ChunkKind.KEYFRAME, ChunkKind.TRANSCRIPT)


def search_shots(
    c: AppContainer, text: str, filters: SearchFilters | None = None, *, limit: int = 20
) -> SearchResult:
    """Shots for an edit: those whose passages answer ``text`` (the shot's own, one of its
    keyframes, or the speech over it: a word said in the shot finds it), each once, at its
    best rank; without text, the shots the filters keep, the most usable first. Each hit is
    the shot itself: its cut points, its passage and facets."""
    filters = with_language(c, filters or SearchFilters())
    if not " ".join(text.split()):
        return search(c, "", replace(filters, kinds=(ChunkKind.SHOT,)), limit=limit)
    found = search(c, text, replace(filters, kinds=TIMED), limit=CANDIDATES)
    best: dict[str, SearchHit] = {}
    for hit in found.hits:
        if hit.shot_id is not None and hit.shot_id not in best:
            best[hit.shot_id] = hit
    chosen = list(best.items())[:limit]
    with c.db.read() as session:
        shots = {
            row.id: row
            for row in session.execute(
                sa.select(Shot).where(Shot.id.in_([shot_id for shot_id, _ in chosen]))
            ).scalars()
        }
        own = {
            row.shot_id: row
            for row in session.execute(
                sa.select(SearchChunk).where(
                    SearchChunk.shot_id.in_(list(shots)),
                    SearchChunk.kind == ChunkKind.SHOT.value,
                    in_language(filters.language or ""),
                )
            ).scalars()
        }
    hits: list[SearchHit] = []
    for shot_id, hit in chosen:
        shot = shots.get(shot_id)
        if shot is None:
            continue
        passage = own.get(shot_id)
        hits.append(
            replace(
                hit, kind=ChunkKind.SHOT, t_start=shot.start_s, t_end=shot.end_s,
                shot_idx=shot.idx,
                chunk_id=passage.id if passage else hit.chunk_id,
                text=passage.text if passage else hit.text,
                facets=dict(passage.facets or {}) if passage else hit.facets,
            )
        )  # fmt: skip
    return replace(found, hits=hits, total=len(best))


# ---------------------------------------------------------------- facets
@dataclass(frozen=True, slots=True)
class SearchFacets:
    """What the filters can offer: the values found in the index, with how many passages (or
    videos) hold them. An empty list: nothing in the library to filter on."""

    state: IndexState
    kinds: list[tuple[str, int]]
    devices: list[tuple[str, int]]  # videos
    places: list[tuple[str, int]]  # localities and countries, videos
    subjects: list[tuple[str, int]]  # shots
    shot_types: list[tuple[str, int]]  # shots
    weather: list[tuple[str, int]]  # videos
    light_phases: list[tuple[str, int]]  # videos
    orientations: list[tuple[str, int]]  # videos
    date_min: date | None
    date_max: date | None
    speech: bool  # some passages have speech
    rated: bool  # some indexed videos have a rating
    favorites: bool
    usability: bool  # shots have a usability


def _facets_of(session: Session, kind: ChunkKind, language: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any] | None] = list(
        session.execute(
            sa.select(SearchChunk.facets).where(
                SearchChunk.kind == kind.value, in_language(language)
            )
        ).scalars()
    )
    return [dict(row or {}) for row in rows]


def _counted(values: Sequence[Any], limit: int | None = None) -> list[tuple[str, int]]:
    counts = Counter(str(v) for v in values if v not in (None, ""))
    return sorted(counts.items(), key=lambda kv: (-kv[1], fold(kv[0])))[:limit]


def search_facets(
    c: AppContainer, *, max_subjects: int = 40, language: str | None = None
) -> SearchFacets:
    """The values of the filters found in the index, in ``language`` (default: the analysis
    language)."""
    tr = texts_in(c, language)
    language = tr.language or load_preferences(c.db).language
    state = index_state(c)
    with c.db.read() as session:
        indexed = sa.select(SearchChunk.video_id).distinct().scalar_subquery()
        videos = session.execute(
            sa.select(_device(), Video.orientation, Video.rating, Video.favorite).where(
                Video.id.in_(indexed)
            )
        ).all()
        places = session.execute(
            sa.select(ContextPlace.locality, ContextPlace.country).where(
                ContextPlace.video_id.in_(indexed)
            )
        ).all()
        video_facets = _facets_of(session, ChunkKind.VIDEO, language)
        shots = _facets_of(session, ChunkKind.SHOT, language)
        kinds = session.execute(
            sa.select(SearchChunk.kind, sa.func.count()).group_by(SearchChunk.kind)
        ).all()
        speech = session.execute(
            sa.select(SearchChunk.id).where(_facet("speech") == 1).limit(1)
        ).first()
    dates = sorted(str(f["date"]) for f in video_facets if f.get("date"))
    order = {k.value: i for i, k in enumerate(ChunkKind)}
    return SearchFacets(
        state=state,
        kinds=sorted(((str(k), int(n)) for k, n in kinds), key=lambda kv: order.get(kv[0], 99)),
        devices=_counted([row[0] for row in videos]),
        places=_counted([tr(p) for row in places for p in dict.fromkeys(row) if p]),
        subjects=_counted([s for f in shots for s in f.get("subjects") or []], max_subjects),
        shot_types=_counted([t for f in shots for t in f.get("shot_types") or []]),
        weather=_counted([w for f in video_facets for w in f.get("weather") or []]),
        light_phases=_counted([f.get("light_phase") for f in video_facets]),
        orientations=_counted([row[1].value if row[1] else None for row in videos]),
        date_min=date.fromisoformat(dates[0]) if dates else None,
        date_max=date.fromisoformat(dates[-1]) if dates else None,
        speech=speech is not None,
        rated=any(row[2] for row in videos),
        favorites=any(row[3] for row in videos),
        usability=any(f.get("usability") is not None for f in shots),
    )


# ---------------------------------------------------------------- the user's own fields
def refresh_video_passage(c: AppContainer, video_id: str) -> bool:
    """Write again the passage about the whole video after the user edited its title or notes:
    its words at once, and its vector when the model is installed (one passage, a fraction of a
    second). False when the video is not indexed yet: the ``index`` stage will do it."""
    with c.db.read() as session:
        if session.get(Video, video_id) is None:
            raise NotFoundError(f"Vidéo introuvable : {video_id}")
    language = load_preferences(c.db).language
    chunks = passages(c.db, video_id, language, video_only=True)  # one per language
    embedder = c.embedder()
    vectors = None
    if embedder is not None:
        try:
            vectors = embedder.embed_documents([chunk.text for chunk in chunks])
        except VfeError as exc:  # the words are enough until the next analysis
            log.warning("video passage not embedded", video_id=video_id, error=exc.detail)
    model = embedder.model_id if embedder is not None and vectors is not None else None
    return replace_video_chunk(c.db, video_id, chunks, vectors, model)
