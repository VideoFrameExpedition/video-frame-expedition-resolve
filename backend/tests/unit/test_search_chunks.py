"""Passages of the search index: windows of speech, texts, facets."""

from __future__ import annotations

from datetime import date
from itertools import pairwise

from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.domain.search_chunks import (
    MAX_CHARS,
    TRANSCRIPT_OVERLAP_S,
    TRANSCRIPT_WINDOW_S,
    Chapter,
    ChunkKind,
    IndexFacts,
    ScreenText,
    Story,
    build_chunks,
    digest,
    spoken_date,
    transcript_windows,
)
from vfe_vision.domain.synthesis_input import Frame, Segment, Shot, Video, Word


def _words(count: int, step: float = 0.5, *, sentence_every: int = 0) -> list[Word]:
    return [
        Word(i * step, i * step + step * 0.8,
             f" mot{i}{'.' if sentence_every and (i + 1) % sentence_every == 0 else ''}")
        for i in range(count)
    ]  # fmt: skip


def test_windows_are_about_thirty_seconds_and_overlap() -> None:
    words = _words(300)  # 150 s of speech
    windows = transcript_windows(words)
    assert windows[0][0] == 0
    assert windows[-1][1] == len(words)  # every word is in a window
    for (first, last), (nxt, _) in pairwise(windows):
        assert words[last - 1].end - words[first].start <= TRANSCRIPT_WINDOW_S
        assert nxt < last  # the next one starts a little earlier…
        assert words[last - 1].end - words[nxt].start <= TRANSCRIPT_OVERLAP_S + 0.5  # …by ~5 s
    assert len(windows) == 6


def test_a_window_ends_at_a_sentence_or_a_pause() -> None:
    words = _words(100, sentence_every=45)  # a sentence ends at 22.5 s
    _, last = transcript_windows(words)[0]
    assert words[last - 1].text.endswith(".")
    paused = [*_words(50), *(Word(w.start + 2.0, w.end + 2.0, w.text) for w in _words(60)[50:])]
    _, last = transcript_windows(paused)[0]
    assert last == 50  # the 2 s pause at 25 s ends the window


def test_one_word_longer_than_a_window_is_its_own() -> None:
    words = [Word(0.0, 45.0, " long"), Word(45.0, 46.0, " court")]
    assert transcript_windows(words) == [(0, 1), (1, 2)]
    assert transcript_windows([]) == []


@given(
    st.lists(
        st.tuples(st.floats(0.01, 3.0), st.floats(0.0, 2.0), st.booleans()),
        min_size=1,
        max_size=200,
    )
)
def test_windows_cover_every_word_and_always_progress(
    spans: list[tuple[float, float, bool]],
) -> None:
    words, t = [], 0.0
    for length, gap, ends in spans:
        words.append(Word(t, t + length, " m." if ends else " m"))
        t += length + gap
    windows = transcript_windows(words)
    assert windows[0][0] == 0
    assert windows[-1][1] == len(words)
    covered: set[int] = set()
    for first, last in windows:
        assert first < last
        covered.update(range(first, last))
        if last - first > 1:
            assert words[last - 1].end - words[first].start <= TRANSCRIPT_WINDOW_S
    assert covered == set(range(len(words)))
    firsts = [first for first, _ in windows]
    assert firsts == sorted(set(firsts))


def _frame(idx: int, t: float, shot: int, **data: object) -> Frame:
    return Frame(idx=idx, keyframe_id=f"k{idx}", t=t, shot=shot, sharpness=10.0 + idx,
                 metrics={}, data=dict(data) or None)  # fmt: skip


def _facts(**extra: object) -> IndexFacts:
    video = Video(
        id="v", filename="plage.mp4", duration=40.0, orientation="horizontal",
        capture_local=None, place_label="Hyères, Var, France", place_feature=None,
        light_phase="blue_hour", day_part="evening", weather=None, presence={}, heard=(),
        instruments=(), transcript_language="en",
        segments=(Segment(22.0, 25.0, "Look at the waves.", ()),),
        silences=(),
        shots=(
            Shot(0, 0.0, 20.0, "static", 0.9, "start", {}),
            Shot(1, 20.0, 40.0, "pan_left", 0.8, "cut", {}),
        ),
        frames=(
            _frame(0, 5.0, 0, caption="Une plage déserte.", description="Le sable est blanc.",
                   shot_type="wide", weather="overcast", tags=["plage", "sable"],
                   subjects=[{"label": "Mouette"}]),
            _frame(1, 25.0, 1),  # not described
        ),
    )  # fmt: skip
    values: dict[str, object] = {
        "video": video,
        "language": "fr",
        "capture_date": date(2026, 8, 1),
        "shot_ids": {0: "s0", 1: "s1"},
        "stories": (Story(1, 20.0, 40.0, "Les vagues montent sur la plage.", "elles déferlent"),),
        "screen": (ScreenText(26.0, "DANGER"),),
        "beings": {"k0": ("personne",)},
        "chapters": (Chapter(0.0, 40.0, "Une seule partie", "Tout."),),
        **extra,
    }
    return IndexFacts(**values)  # type: ignore[arg-type]


def test_passages_of_a_small_video() -> None:
    chunks = build_chunks(_facts())
    kinds = [c.kind for c in chunks]
    # One chapter is the whole video: no chapter passage; the undescribed frame has none.
    assert kinds == [ChunkKind.VIDEO, ChunkKind.SHOT, ChunkKind.SHOT, ChunkKind.KEYFRAME,
                     ChunkKind.TRANSCRIPT]  # fmt: skip
    whole, beach, waves, frame, speech = chunks
    assert whole.text.splitlines() == [
        "plage.mp4",
        # the frames alone tell the weather here (no model weather): a wide frame is enough
        "Tourné le 1er août 2026 le soir, heure bleue, à Hyères, Var, France · Météo : couvert",
    ]
    assert whole.facets == {
        "date": "2026-08-01", "light_phase": "blue_hour", "place": "hyeres, var, france",
        "shot_types": ["wide"], "subjects": ["mouette", "personne"],
        "subject_keys": ["mouette", "personne"], "weather": ["overcast"], "speech": True,
    }  # fmt: skip
    assert beach.text == ("Une plage déserte.\nSujets : mouette, personne\nLe sable est blanc.")
    assert (beach.t_start, beach.t_end, beach.shot_id, beach.keyframe_id) == (0.0, 20.0, "s0", "k0")
    assert beach.facets["speech"] is False
    assert beach.facets["usability"] >= 0
    assert waves.text == (
        "Les vagues montent sur la plage. elles déferlent.\nTexte à l'écran : DANGER"
    )
    assert waves.keyframe_id == "k1"  # its own keyframe, even undescribed
    assert waves.facets["speech"] is True
    assert frame.text == "Une plage déserte.\nLe sable est blanc.\nMots-clés : plage, sable"
    assert (frame.t_start, frame.t_end) == (5.0, 20.0)  # until its shot ends
    assert speech.text == "Look at the waves."
    assert speech.language == "en"  # the speech's own language
    assert (speech.shot_id, speech.keyframe_id) == ("s1", "k1")


def test_passages_stay_short() -> None:
    long = " ".join(["Une phrase assez longue pour remplir un passage entier."] * 60)
    facts = _facts(user_notes=long, synthesis_summary=long)
    for chunk in build_chunks(facts):
        assert len(chunk.text) <= (1500 if chunk.kind == ChunkKind.VIDEO else MAX_CHARS)
    whole = build_chunks(facts)[0]
    assert whole.text.endswith("…")


def test_the_digest_follows_the_passages() -> None:
    first = digest(build_chunks(_facts()))
    assert digest(build_chunks(_facts())) == first
    assert digest(build_chunks(_facts(user_title="Mouettes"))) != first
    assert digest(build_chunks(_facts(language="en"))) != first  # labels in English


def test_dates_are_spoken_in_the_analysis_language() -> None:
    assert spoken_date(date(2026, 8, 1), "fr") == "1er août 2026"
    assert spoken_date(date(2026, 8, 12), "fr") == "12 août 2026"
    assert spoken_date(date(2026, 8, 12), "en") == "12 August 2026"
