"""The ``index`` stage and the search service, with a fake embedder."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import date
from functools import partial
from pathlib import Path

import anyio
import pytest
import sqlalchemy as sa

from tests.fakes.embeddings import FakeEmbedder
from tests.fakes.library import Library, build_library, stage_context
from vfe_vision.core.config import Settings
from vfe_vision.db.models import ChunkVector, SearchChunk, StageRun, Video
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import Orientation, StageStatus
from vfe_vision.domain.search_chunks import ChunkKind
from vfe_vision.pipeline.runner import PipelineRunner
from vfe_vision.pipeline.stages import default_registry
from vfe_vision.pipeline.stages.search_index import WORDS_ONLY, IndexStage
from vfe_vision.services import search, videos
from vfe_vision.services.container import AppContainer
from vfe_vision.services.search import SearchFilters


class _Sink:
    def emit(self, type_: str, **_: object) -> None:
        return None


@pytest.fixture
def library(db: Database, tmp_path: Path) -> Library:
    return build_library(db, tmp_path / "Rushs")


@pytest.fixture
def fake() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def container(settings: Settings, db: Database, fake: FakeEmbedder) -> Iterator[AppContainer]:
    c = AppContainer.create(settings)
    c.embedder = lambda: fake
    yield c
    c.db.dispose()


def _index(db: Database, library: Library, fake: FakeEmbedder | None) -> None:
    for video_id in (library.holiday, library.mountain):
        outcome = IndexStage().run(
            stage_context(db, video_id, (lambda: fake) if fake else (lambda: None))
        )
        assert outcome.status == StageStatus.SUCCEEDED


def _chunks(db: Database, video_id: str) -> list[SearchChunk]:
    with db.read() as session:
        return list(
            session.execute(
                sa.select(SearchChunk)
                .where(SearchChunk.video_id == video_id)
                .order_by(SearchChunk.id)
            ).scalars()
        )


def _count(db: Database, table: str) -> int:
    with db.read() as session:
        return int(session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


# ---------------------------------------------------------------- the stage
def test_every_kind_of_passage_is_indexed(
    db: Database, library: Library, fake: FakeEmbedder
) -> None:
    outcome = IndexStage().run(stage_context(db, library.holiday, lambda: fake))
    assert outcome.status == StageStatus.SUCCEEDED
    assert not outcome.summary.get("provisional")
    chunks = _chunks(db, library.holiday)
    kinds = Counter(c.kind for c in chunks)
    # the video, 3 chapters, 6 shots, 6 described keyframes in French and in English,
    # one window of speech (in the language spoken, once)
    assert kinds == {"video": 2, "chapter": 6, "shot": 12, "keyframe": 12, "transcript": 1}
    assert outcome.summary == {
        "passages": 33, "vectors": 33, "model": fake.model_id,
        "kinds": {"chapter": 6, "keyframe": 12, "shot": 12, "transcript": 1, "video": 2},
    }  # fmt: skip
    assert Counter(c.language for c in chunks if c.kind != "transcript") == {"fr": 16, "en": 16}
    whole = next(c for c in chunks if c.kind == "video")
    assert whole.t_start is None
    assert whole.text.splitlines()[:3] == [
        "Vacances à Hyères — Un chat, du riz et un lac.",
        "vacances.mp4",
        "Une journée d'été entre la maison et le lac.",
    ]
    assert "Tourné le 1er juillet 2026 le soir, heure dorée, à Hyères, Var, France" in whole.text
    assert "Météo : ciel dégagé · Appareil : samsung Galaxy S26 Ultra" in whole.text
    assert "Sons : bateau" in whole.text
    assert whole.facets["date"] == "2026-07-01"
    assert whole.facets["place"] == "hyeres, var, france"
    assert whole.facets["light_phase"] == "golden_hour"
    assert "usability" not in whole.facets  # a shot-level facet
    speech = next(c for c in chunks if c.kind == "transcript")
    assert speech.text == "On met le riz dans la casserole."
    assert (speech.t_start, speech.t_end, speech.language) == (45.0, 48.4, "fr")
    assert speech.facets["speech"] is True
    assert speech.shot_id is not None
    assert speech.keyframe_id is not None  # the keyframe shown for it
    boat = next(c for c in chunks if c.kind == "shot" and "barque" in c.text)
    assert boat.text.splitlines()[0].startswith("Une barque traverse le lac")
    assert "Sons : bateau" in boat.text
    assert boat.facets["shot_types"] == ["wide"]
    assert boat.facets["weather"] == ["clear"]
    assert boat.facets["speech"] is False
    assert 0 <= boat.facets["usability"] <= 100
    rice = next(c for c in chunks if c.kind == "shot" and c.t_start == 40.0)
    assert "Texte à l'écran : RIZ BASMATI" in rice.text
    cat = next(c for c in chunks if c.kind == "shot" and c.t_start == 0.0)
    assert cat.facets["subjects"] == ["chat"]  # the vision model's and the box, fused once
    assert cat.facets["subject_keys"] == ["chat"]
    assert _count(db, "search_fts") == 33
    assert _count(db, "chunk_vectors") == 33


def test_passages_are_labelled_in_the_analysis_language(db: Database, library: Library) -> None:
    IndexStage().run(stage_context(db, library.holiday, language="en"))
    whole = next(c for c in _chunks(db, library.holiday) if c.kind == "video")
    assert "Shot on 1 July 2026 in the evening, golden hour, in Hyères" in whole.text
    assert "Weather : clear sky · Device : samsung Galaxy S26 Ultra" in whole.text


def test_without_the_model_words_only_then_complete_adds_the_vectors(
    db: Database, library: Library, fake: FakeEmbedder
) -> None:
    installed: list[FakeEmbedder] = []
    ctx = stage_context(db, library.holiday, lambda: installed[0] if installed else None)
    runner = PipelineRunner(default_registry(), events=_Sink())

    def run() -> StageStatus:  # « Complete » on this stage
        report = anyio.run(partial(runner.run, ctx, job_id=None, stages=["index"]))
        return report.statuses["index"]

    def latest() -> StageRun:
        with db.read() as session:
            return session.execute(
                sa.select(StageRun)
                .where(StageRun.video_id == library.holiday, StageRun.status != StageStatus.CACHED)
                .order_by(StageRun.created_at.desc())
                .limit(1)
            ).scalar_one()

    assert run() == StageStatus.SUCCEEDED
    first = latest()
    assert first.summary["provisional"] is True
    assert first.summary["vectors"] == 0
    assert first.skip_reason == WORDS_ONLY
    assert _count(db, "search_fts") == 33  # the words are there
    assert _count(db, "chunk_vectors") == 0
    gaps = videos.analysis_gaps([first])
    assert gaps.skipped == ["index"]  # « Complete » checks it again…
    assert "index" not in gaps.missing
    assert run() == StageStatus.CACHED  # …and keeps it while nothing changed

    installed.append(fake)  # vfe models search
    assert run() == StageStatus.SUCCEEDED
    second = latest()
    assert not second.summary.get("provisional")
    assert second.summary["vectors"] == 33
    assert _count(db, "chunk_vectors") == 33
    assert videos.analysis_gaps([second]).skipped == []
    assert run() == StageStatus.CACHED  # a result: kept from now on


def test_new_analyses_index_the_video_again(
    db: Database, library: Library, fake: FakeEmbedder
) -> None:
    ctx = stage_context(db, library.holiday, lambda: fake)
    stage = IndexStage()
    before = stage.input_facts(ctx)
    assert stage.input_facts(ctx) == before  # stable
    with db.write() as session:
        session.execute(sa.update(Video).values(title="Été au bord de l'eau"))
    assert stage.input_facts(ctx) != before  # the user's title is in a passage


def test_a_broken_model_still_indexes_the_words(
    db: Database, library: Library, fake: FakeEmbedder
) -> None:
    fake.fail = True
    outcome = IndexStage().run(stage_context(db, library.holiday, lambda: fake))
    assert outcome.status == StageStatus.SUCCEEDED
    assert outcome.retryable  # degraded: done again at the next analysis
    assert "vecteurs non calculés" in (outcome.skip_reason or "")
    assert _count(db, "search_fts") == 33
    assert _count(db, "chunk_vectors") == 0


def test_a_removed_video_takes_its_passages_along(
    db: Database, library: Library, fake: FakeEmbedder
) -> None:
    _index(db, library, fake)
    total = _count(db, "search_chunks")
    mountain = len(_chunks(db, library.mountain))
    with db.write() as session:
        session.execute(sa.delete(Video).where(Video.id == library.mountain))
    assert _count(db, "search_chunks") == total - mountain
    assert _count(db, "chunk_vectors") == total - mountain
    with db.read() as session:
        found: int = session.execute(
            sa.text("SELECT count(*) FROM search_fts WHERE search_fts MATCH '\"neige\"'")
        ).scalar_one()
    assert found == 0  # the words went with the passages (delete trigger on the cascade)


# ---------------------------------------------------------------- search
def test_words_and_meaning_are_fused(
    container: AppContainer, library: Library, fake: FakeEmbedder
) -> None:
    _index(container.db, library, fake)
    result = search.search(container, "riz dans la casserole")
    assert result.retrievers == ("words", "meaning")
    assert result.state.indexed == 2
    assert result.state.meaning_ready
    top = result.hits[0]
    assert top.video_id == library.holiday
    assert top.kind in {ChunkKind.TRANSCRIPT, ChunkKind.SHOT}
    assert set(top.retrievers) == {"words", "meaning"}
    marked = [top.snippet[s:e] for s, e in top.highlights]
    assert {m.lower() for m in marked} >= {"riz", "casserole"}
    assert "<" not in top.snippet  # plain text, never markup
    assert top.thumb_path is not None
    assert top.thumb_path.endswith(".jpg")
    kinds = {hit.kind for hit in result.hits if hit.video_id == library.holiday}
    assert ChunkKind.TRANSCRIPT in kinds
    assert all(hit.video_id != library.mountain for hit in result.hits[:3])


def test_meaning_finds_what_the_words_miss(container: AppContainer, library: Library) -> None:
    _index(container.db, library, FakeEmbedder())
    # « cuisiner » is not in any passage: its stem « cuisi » is (« cuisine »).
    result = search.search(container, "cuisiner")
    assert result.hits
    assert result.hits[0].retrievers == ("meaning",)
    assert result.hits[0].video_id == library.holiday


def test_words_alone_without_vectors(container: AppContainer, library: Library) -> None:
    _index(container.db, library, None)
    result = search.search(container, "neige")
    assert result.retrievers == ("words",)
    assert not result.state.meaning_ready
    assert {hit.video_id for hit in result.hits} == {library.mountain}
    accents = search.search(container, "ETE")  # « été » in the summary
    assert any(hit.kind == ChunkKind.VIDEO for hit in accents.hits)
    prefix = search.search(container, "casser")
    assert prefix.hits
    assert "casserole" in prefix.hits[0].text


@pytest.mark.parametrize(
    "query", ['"', "NEAR(", "AND", "OR OR", "*", "-riz", "riz)", "col:riz", "^", "'", "\\", "  "]
)
def test_query_syntax_never_fails(container: AppContainer, library: Library, query: str) -> None:
    _index(container.db, library, None)
    search.search(container, query)  # no exception, whatever is typed


def test_filters(container: AppContainer, library: Library, fake: FakeEmbedder) -> None:
    _index(container.db, library, fake)

    def videos_of(query: str = "", **filters: object) -> set[str]:
        found = search.search(container, query, SearchFilters(**filters), limit=100)  # type: ignore[arg-type]
        return {hit.video_id for hit in found.hits}

    both = {library.holiday, library.mountain}
    assert videos_of(kinds=(ChunkKind.VIDEO,)) == both
    assert videos_of(weather=("snow",)) == {library.mountain}
    assert videos_of(weather=("clear",)) == {library.holiday}
    assert videos_of(place="HYERES") == {library.holiday}
    assert videos_of(place="france") == both
    assert videos_of(light_phase=("golden_hour",)) == {library.holiday}
    assert videos_of(device="Apple iPhone 15 Pro") == {library.mountain}
    assert videos_of(orientation=Orientation.VERTICAL) == {library.mountain}
    assert videos_of(date_from=date(2026, 1, 1)) == {library.holiday}
    assert videos_of(date_to=date(2025, 12, 31)) == {library.mountain}
    assert videos_of(min_rating=3) == {library.holiday}
    assert videos_of(favorite=True) == {library.holiday}
    assert videos_of(root_id=library.root_id, folder="Sommets") == {library.mountain}
    assert videos_of(root_id=library.root_id) == both
    assert videos_of(subjects=("CHIEN",)) == {library.mountain}
    assert videos_of(subjects=("chat", "chien")) == set()  # every subject asked for
    assert videos_of(shot_types=("extreme_wide",)) == {library.mountain}
    assert videos_of(has_speech=True) == {library.holiday}
    assert videos_of(video_id=library.mountain) == {library.mountain}
    speech = search.search(container, "", SearchFilters(has_speech=True))
    assert {hit.kind for hit in speech.hits} == {ChunkKind.SHOT}  # a shot filter: shots
    assert all(hit.facets["speech"] for hit in speech.hits)
    usable = search.search(container, "", SearchFilters(min_usability=0), limit=100)
    scores = [hit.facets["usability"] for hit in usable.hits]
    assert scores == sorted(scores, reverse=True)  # the most usable first
    assert videos_of("chien", weather=("clear",)) == set()  # text and filters together
    none = search.search(container, "", SearchFilters(place="Tombouctou"))
    assert none.hits == []
    assert none.total == 0


def test_shots_are_found_by_what_is_said_over_them(
    container: AppContainer, library: Library, fake: FakeEmbedder
) -> None:
    _index(container.db, library, fake)
    # « casserole » is said at 45 s (shot 3) and not written in any shot passage.
    result = search.search_shots(container, "casserole")
    top = result.hits[0]
    assert (top.kind, top.shot_idx, top.t_start, top.t_end) == (ChunkKind.SHOT, 2, 40.0, 60.0)
    assert "RIZ BASMATI" in top.text  # the shot's own passage
    assert top.facets["speech"] is True
    assert len({hit.shot_id for hit in result.hits}) == len(result.hits)  # each shot once
    browse = search.search_shots(container, "", SearchFilters(weather=("snow",)))
    assert {hit.video_id for hit in browse.hits} == {library.mountain}
    assert {hit.kind for hit in browse.hits} == {ChunkKind.SHOT}


def test_vectors_are_read_again_only_when_the_index_changed(
    container: AppContainer, library: Library, fake: FakeEmbedder
) -> None:
    _index(container.db, library, fake)
    search.search(container, "lac")
    search.search(container, "chat")
    assert container.vectors.loads == 1
    with container.db.write() as session:
        session.execute(sa.update(Video).where(Video.id == library.holiday).values(title="Le lac"))
    assert search.refresh_video_passage(container, library.holiday)
    search.search(container, "lac")
    assert container.vectors.loads == 2


def test_editing_the_title_refreshes_the_video_passage(
    container: AppContainer, library: Library, fake: FakeEmbedder
) -> None:
    _index(container.db, library, fake)
    before = {c.id for c in _chunks(container.db, library.holiday)}
    videos.update_video(container, library.holiday, {"title": "Baignade du matin"})
    assert search.refresh_video_passage(container, library.holiday)
    after = _chunks(container.db, library.holiday)
    assert len(after) == len(before)
    whole = next(c for c in after if c.kind == "video")
    assert whole.id not in before  # a new id: the vectors' readers know it changed
    assert whole.text.startswith("Baignade du matin")
    with container.db.read() as session:
        vector = session.get(ChunkVector, whole.id)
    assert vector is not None
    assert vector.model == fake.model_id
    hits = search.search(container, "baignade").hits
    assert hits[0].kind == ChunkKind.VIDEO
    assert hits[0].title == "Baignade du matin"
    # A video not indexed yet: nothing written, the stage will do it.
    with container.db.write() as session:
        session.execute(sa.delete(SearchChunk).where(SearchChunk.video_id == library.mountain))
    assert not search.refresh_video_passage(container, library.mountain)


def test_facets_offer_what_the_index_holds(container: AppContainer, library: Library) -> None:
    _index(container.db, library, None)
    facets = search.search_facets(container)
    assert dict(facets.devices) == {"samsung Galaxy S26 Ultra": 1, "Apple iPhone 15 Pro": 1}
    assert dict(facets.places) == {"France": 2, "Hyères": 1, "Chamonix": 1}
    assert facets.places[0] == ("France", 2)
    assert dict(facets.weather) == {"clear": 1, "snow": 1}
    assert dict(facets.light_phases) == {"golden_hour": 1, "day": 1}
    assert {"chat", "chien", "bateau"} <= dict(facets.subjects).keys()
    assert "wide" in dict(facets.shot_types)
    assert dict(facets.orientations) == {"horizontal": 1, "vertical": 1}
    assert (facets.date_min, facets.date_max) == (date(2025, 12, 24), date(2026, 7, 1))
    assert (facets.speech, facets.rated, facets.favorites, facets.usability) == (True,) * 4
    assert [k for k, _ in facets.kinds] == ["video", "chapter", "shot", "keyframe", "transcript"]
    assert facets.state.indexed == 2
    assert not facets.state.semantic or facets.state.vectors == 0


def test_an_empty_index_says_so(container: AppContainer, library: Library) -> None:
    result = search.search(container, "chat")
    assert result.hits == []
    assert result.state.indexed == 0
    assert result.state.videos == 2
    assert search.search_facets(container).devices == []


def test_the_query_vector_failing_leaves_the_words(
    container: AppContainer, library: Library, fake: FakeEmbedder
) -> None:
    _index(container.db, library, fake)
    fake.fail = True
    result = search.search(container, "lac")
    assert result.retrievers == ("words",)
    assert "indisponible" in (result.note or "")
    assert result.hits


def test_vectors_of_another_model_are_never_compared(
    container: AppContainer, library: Library
) -> None:
    _index(container.db, library, FakeEmbedder("fake/old@0"))
    result = search.search(container, "cuisiner")  # the installed fake is another model
    assert result.retrievers == ("words",)
    assert result.state.vectors == 0
