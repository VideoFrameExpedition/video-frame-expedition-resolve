"""Synthesis blocks and chapters (pure functions): hand-built videos, no database."""

from __future__ import annotations

import itertools
import random

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vfe_vision.domain.blocks import (
    MAX_PARTS,
    MERGE_ABOVE_BLOCKS,
    MIN_PART_S,
    speech_spans,
    split_shot,
    synthesis_blocks,
)
from vfe_vision.domain.chapters import (
    CHAPTER_RULES_VERSION,
    DP_MAX_BLOCKS,
    MIN_CHAPTER_S,
    WEIGHT_CAP_S,
    _greedy_spans,
    _spreads,
    _vectors,
    block_signature,
    chapter_count,
    chapter_ranges,
    tokens,
)
from vfe_vision.domain.synthesis_input import (
    Block,
    Frame,
    Segment,
    Shot,
    Video,
    Word,
    speech_seconds,
    speech_text,
)

# What each topic of a hand-built video looks like: caption, kind of place, setting.
TOPICS = {
    "lac": ("Un lac de montagne reflète les sommets enneigés", "Lac", "outdoor"),
    "cuisine": ("Une casserole de riz mijote sur la cuisinière", "Cuisine", "indoor"),
    "temple": ("Des moines prient devant la pagode dorée", "Temple", "outdoor"),
    "concert": ("Un guitariste joue sur la scène du festival", "Salle de concert", "indoor"),
}
VIEWS = ["de près", "au loin", "vu de côté", "en plongée", "au ralenti", "en contre-jour"]
# The vision model sometimes answers in English: the same topics, as it may write them.
ENGLISH = {
    "lac": ("A mountain lake reflects the snowy peaks", "lake", "outdoor"),
    "cuisine": ("A pot of rice simmers on the stove", "kitchen", "indoor"),
    "temple": ("Monks pray in front of the golden pagoda", "temple", "outdoor"),
}
ENGLISH_VIEWS = ["up close", "from afar", "from the side", "from above", "in slow motion"]


def make_video(
    shots: list[Shot],
    frames: list[Frame] | None = None,
    segments: list[Segment] | None = None,
    duration: float | None = None,
) -> Video:
    return Video(
        id="v1",
        filename="clip.mp4",
        duration=duration if duration is not None else (shots[-1].end if shots else 0.0),
        orientation="horizontal",
        capture_local=None,
        place_label=None,
        place_feature=None,
        light_phase=None,
        day_part=None,
        weather=None,
        presence={},
        heard=(),
        instruments=(),
        transcript_language="fr" if segments else None,
        segments=tuple(segments or ()),
        silences=(),
        shots=tuple(shots),
        frames=tuple(frames or ()),
    )


def make_shots(durations: list[float], heard: list[tuple[str, ...]] | None = None) -> list[Shot]:
    """Consecutive shots of these durations from 0 s, with the sounds heard in each."""
    starts = list(itertools.accumulate(durations, initial=0.0))
    sounds = heard or [()] * len(durations)
    return [
        Shot(i, a, b, "static", 1.0, "start" if i == 0 else "cut", {}, sounds[i])
        for i, (a, b) in enumerate(itertools.pairwise(starts))
    ]


def make_frame(
    idx: int,
    t: float,
    shot: int | None,
    caption: str | None = None,
    *,
    place: str = "",
    setting: str = "",
) -> Frame:
    """A keyframe, described by the vision model when it has a caption."""
    data = None
    if caption is not None:
        data = {
            "caption": caption,
            "subjects": [{"label": "sujet principal"}],
            "place_type": place,
            "setting": setting,
        }
    return Frame(idx, f"k{idx}", t, shot, 100.0, {}, data)


def sentence(start: float, words: list[str], word_s: float = 0.3, gap_s: float = 0.05) -> Segment:
    """A transcript segment whose words carry their leading space, as Whisper stores them."""
    timed = []
    t = start
    for text in words:
        timed.append(Word(t, t + word_s, f" {text}"))
        t += word_s + gap_s
    return Segment(start, timed[-1].end, "".join(w.text for w in timed).strip(), tuple(timed))


def topic_video(
    runs: list[tuple[str, float]],
    shot_s: float = 7.5,
    *,
    topics: dict[str, tuple[str, str, str]] = TOPICS,
    views: list[str] = VIEWS,
) -> tuple[Video, list[float]]:
    """Runs of shots on one topic each (one described keyframe per shot); the run starts."""
    rng = random.Random(4)
    durations: list[float] = []
    captions: list[tuple[str, str, str]] = []
    run_starts: list[float] = []
    t = 0.0
    for topic, seconds in runs:
        run_starts.append(t)
        caption, place, setting = topics[topic]
        count = round(seconds / shot_s)
        durations += [shot_s] * count
        captions += [(f"{caption} {rng.choice(views)}", place, setting) for _ in range(count)]
        t += count * shot_s
    shots = make_shots(durations)
    frames = [
        make_frame(i, shot.start + 0.5, i, caption, place=place, setting=setting)
        for i, (shot, (caption, place, setting)) in enumerate(zip(shots, captions, strict=True))
    ]
    return make_video(shots, frames), run_starts


def chapter_starts(blocks: list[Block], ranges: list[tuple[int, int]]) -> list[float]:
    by_no = {b.no: b for b in blocks}
    return [by_no[first].start for first, _ in ranges]


def assert_covers(ranges: list[tuple[int, int]], n: int) -> None:
    """The chapters cover blocks 1..n in order, without gap or overlap."""
    assert ranges
    assert ranges[0][0] == 1
    assert ranges[-1][1] == n
    assert all(first <= last for first, last in ranges)
    assert all(a[1] + 1 == b[0] for a, b in itertools.pairwise(ranges))


class TestSplitShot:
    def test_the_default_still_caps_at_four_parts(self) -> None:
        assert MAX_PARTS == 4
        assert split_shot(0.0, 100.0) == [(0.0, 25.0), (25.0, 50.0), (50.0, 75.0), (75.0, 100.0)]
        assert split_shot(0.0, 100.0) == split_shot(0.0, 100.0, max_parts=MAX_PARTS)

    @pytest.mark.parametrize("duration", [12.0, 30.0, 31.0, 45.0, 69.9, 89.0])
    def test_no_cap_changes_nothing_below_four_parts(self, duration: float) -> None:
        speech = speech_spans([(9.0, 12.5), (21.0, 23.0), (40.0, 44.0)])
        keyframes = [5.0, 19.0, 24.0, 41.0]
        uncapped = split_shot(
            0.0, duration, keyframe_times=keyframes, speech=speech, max_parts=None
        )
        assert uncapped == split_shot(0.0, duration, keyframe_times=keyframes, speech=speech)

    def test_without_a_cap_a_long_shot_gets_a_part_every_20_s(self) -> None:
        parts = split_shot(10.0, 273.0, max_parts=None)
        assert len(parts) == 13  # round(263 / 20)
        assert parts[0][0] == 10.0
        assert parts[-1][1] == 273.0
        assert all(a[1] == b[0] for a, b in itertools.pairwise(parts))
        assert all(b - a >= MIN_PART_S for a, b in parts)


class TestSynthesisBlocks:
    def test_no_shot_no_block(self) -> None:
        assert synthesis_blocks(make_video([])) == []

    def test_a_short_shot_is_one_block_with_its_keyframes_speech_and_sounds(self) -> None:
        shots = [Shot(0, 0.0, 12.0, "static", 1.0, "start", {}, ("Speech", "Bird"))]
        frames = [make_frame(0, 0.0, 0, "Un merle chante"), make_frame(1, 6.0, 0, "Un merle")]
        video = make_video(shots, frames, [sentence(2.0, ["bonjour", "à", "tous"])])
        (block,) = synthesis_blocks(video)
        assert (block.no, block.start, block.end, block.shots) == (1, 0.0, 12.0, (0,))
        assert [f.idx for f in block.frames] == [0, 1]
        assert block.heard == ("Speech", "Bird")
        assert block.speech_s == pytest.approx(0.9)

    def test_a_block_without_keyframe_borrows_the_one_in_force_at_its_start(self) -> None:
        shots = make_shots([4.0, 3.0, 5.0])
        frames = [make_frame(0, 0.0, 0, "a"), make_frame(1, 7.0, 2, "b")]  # none during shot 1
        blocks = synthesis_blocks(make_video(shots, frames))
        assert [[f.idx for f in b.frames] for b in blocks] == [[0], [0], [1]]

    def test_a_keyframe_on_the_first_image_belongs_to_the_block(self) -> None:
        shots = make_shots([4.0, 4.0])
        frames = [make_frame(0, 0.0, 0), make_frame(1, 4.0 - 1e-9, 1)]  # float noise at the cut
        blocks = synthesis_blocks(make_video(shots, frames))
        assert [[f.idx for f in b.frames] for b in blocks] == [[0], [1]]

    def test_a_talk_in_one_263_s_shot_is_cut_into_13_blocks_out_of_speech(self) -> None:
        segments = []
        t = 0.4
        while t < 260.0:  # 8 words (2.75 s), then a 0.65 s pause
            segments.append(sentence(t, ["mot"] * 7 + ["fin."]))
            t = segments[-1].end + 0.65
        shots = [Shot(0, 0.0, 263.0, "static", 0.9, "start", {}, ("Speech",))]
        frames = [make_frame(i, i * 8.0, 0, "Un homme parle") for i in range(33)]
        video = make_video(shots, frames, segments)
        blocks = synthesis_blocks(video)
        assert len(blocks) == 13
        assert [b.no for b in blocks] == list(range(1, 14))
        assert all(a.end == b.start for a, b in itertools.pairwise(blocks))
        spans = speech_spans((w.start, w.end) for w in video.words)
        for cut in (b.start for b in blocks[1:]):
            assert not any(a < cut < b for a, b in spans), cut  # in a pause, not in a sentence
        # Every word said lands in exactly one block.
        said = [speech_text(video.segments, b.start, b.end) for b in blocks]
        assert " ".join(said) == speech_text(video.segments, 0.0, 263.0)
        total = speech_seconds(video.segments, 0.0, 263.0)
        assert sum(b.speech_s for b in blocks) == pytest.approx(total)
        assert all(b.heard == ("Speech",) for b in blocks)

    def test_an_hour_in_one_shot_keeps_its_20_s_blocks(self) -> None:
        video = make_video([Shot(0, 0.0, 3600.0, "static", 1.0, "start", {})])
        blocks = synthesis_blocks(video)
        assert len(blocks) == 180  # past 150, but none is short enough to merge
        assert all(b.duration == pytest.approx(20.0) for b in blocks)
        ranges = chapter_ranges(video, blocks)
        assert_covers(ranges, 180)
        assert len(ranges) == 12  # nothing tells the blocks apart: the count still holds

    def test_up_to_150_blocks_nothing_is_merged(self) -> None:
        blocks = synthesis_blocks(make_video(make_shots([2.0] * MERGE_ABOVE_BLOCKS)))
        assert len(blocks) == MERGE_ABOVE_BLOCKS
        assert all(len(b.shots) == 1 for b in blocks)

    def test_past_150_blocks_short_shots_are_merged_up_to_30_s(self) -> None:
        blocks = synthesis_blocks(make_video(make_shots([2.0] * (MERGE_ABOVE_BLOCKS + 1))))
        assert [b.duration for b in blocks] == [30.0] * 10 + [2.0]
        assert blocks[0].shots == tuple(range(15))
        assert [b.no for b in blocks] == list(range(1, 12))

    def test_the_merge_keeps_shots_of_4_s_and_more_apart(self) -> None:
        tail = [2.0, 10.0, 10.0, 1.0, 1.0, 1.0, 28.0, 5.0, 3.0]
        heard = [("Music",)] * 145 + [("Speech",), (), ("Wind",), ("Wind", "Bird")] + [()] * 5
        shots = make_shots([10.0] * 145 + tail, heard)
        blocks = synthesis_blocks(make_video(shots))
        assert len(blocks) == 149
        assert [b.shots for b in blocks[-5:]] == [
            (144, 145),  # the 2 s shot joins the block before it
            (146,),
            (147, 148, 149, 150),  # three 1 s flashes after a 10 s shot
            (151,),
            (152, 153),
        ]
        assert blocks[-5].heard == ("Music", "Speech")
        assert blocks[-3].heard == ("Wind", "Bird")
        assert all(b.duration <= 30.0 for b in blocks)


class TestChapterCount:
    @pytest.mark.parametrize(
        ("duration", "expected"),
        [
            (0.0, 1),
            (89.9, 1),
            (90.0, 2),
            (135.0, 3),
            (263.0, 4),
            (300.0, 4),
            (301.0, 4),
            (1000.0, 6),
            (3000.0, 10),
            (7200.0, 12),
        ],
    )
    def test_count(self, duration: float, expected: int) -> None:
        assert chapter_count(duration) == expected


class TestSignature:
    def test_tokens_skip_short_and_stop_words(self) -> None:
        assert tokens("Le chat boit de l'eau dans la Cuisine, avec un bol") == {
            "chat",
            "boit",
            "l'eau",
            "cuisine",
            "bol",
        }

    def test_a_block_is_its_captions_subjects_place_setting_and_longer_words_said(self) -> None:
        shots = make_shots([10.0, 10.0])
        frames = [make_frame(0, 0.0, 0, "Un chat noir dort", place="Salon", setting="indoor")]
        # « fromage » straddles the cut at 10 s: it belongs to the block holding its midpoint.
        said = Segment(
            8.0,
            11.0,
            "Le riz au fromage",
            (Word(8.0, 8.4, " Le"), Word(8.5, 8.9, " riz"), Word(9.8, 10.4, " fromage")),
        )
        video = make_video(shots, frames, [said])
        first, second = synthesis_blocks(video)  # the second borrows the keyframe at 0 s
        seen = {"chat", "noir", "dort", "sujet", "principal", "@place:salon", "@setting:indoor"}
        assert block_signature(video, first) == seen  # « riz » is too short to count as said
        assert block_signature(video, second) == seen | {"#fromage"}

    def test_english_stop_words_are_skipped_too(self) -> None:
        assert tokens("A man walks on the beach with his dog, in the rain") == {
            "man",
            "walks",
            "beach",
            "his",
            "dog",
            "rain",
        }

    def test_an_undescribed_keyframe_adds_nothing(self) -> None:
        video = make_video(make_shots([10.0]), [make_frame(0, 0.0, 0)])
        (block,) = synthesis_blocks(video)
        assert block_signature(video, block) == set()


class TestChapters:
    def test_rules_version(self) -> None:
        assert CHAPTER_RULES_VERSION == 1

    def test_no_block_no_chapter(self) -> None:
        assert chapter_ranges(make_video([]), []) == []

    def test_one_chapter_under_90_s(self) -> None:
        video, _ = topic_video([("lac", 40.0), ("cuisine", 40.0)])
        blocks = synthesis_blocks(video)
        assert chapter_ranges(video, blocks) == [(1, len(blocks))]

    def test_one_block_one_chapter(self) -> None:
        video = make_video(make_shots([200.0]), duration=200.0)
        blocks = synthesis_blocks(video)[:1]
        assert chapter_ranges(video, blocks, 4) == [(1, 1)]

    def test_the_chapters_start_where_the_topic_changes(self) -> None:
        video, run_starts = topic_video([("lac", 45.0), ("cuisine", 45.0), ("temple", 45.0)])
        blocks = synthesis_blocks(video)
        ranges = chapter_ranges(video, blocks)  # 135 s: 3 chapters
        assert_covers(ranges, len(blocks))
        assert chapter_starts(blocks, ranges) == run_starts

    def test_english_captions_are_chaptered_the_same_way(self) -> None:
        runs = [("lac", 45.0), ("cuisine", 45.0), ("temple", 45.0)]
        video, run_starts = topic_video(runs, topics=ENGLISH, views=ENGLISH_VIEWS)
        blocks = synthesis_blocks(video)
        assert chapter_starts(blocks, chapter_ranges(video, blocks)) == run_starts

    def test_the_costs_are_the_weighted_spread_around_the_mean(self) -> None:
        # The fast Gram-matrix sums (deviation from the research's prefix sums) against the
        # definition: Σ w·|x − weighted mean|² over blocks i..j-1, infinite unless i < j.
        rng = np.random.default_rng(3)
        n = 12
        x = rng.random((n, 6)) * (rng.random((n, 6)) < 0.5)
        x[4] = 0.0  # a block with nothing to tell it apart
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        x = x / np.where(norms == 0, 1.0, norms)
        w = rng.uniform(1.0, WEIGHT_CAP_S + 1.0, n)
        spread = _spreads(x, w)
        for i, j in itertools.combinations(range(n + 1), 2):
            mean = np.average(x[i:j], axis=0, weights=w[i:j])
            expected = float((w[i:j] * ((x[i:j] - mean) ** 2).sum(axis=1)).sum())
            assert spread[i, j] == pytest.approx(expected, abs=1e-9)
        assert np.isinf(spread[np.tril_indices(n + 1)]).all()

    @pytest.mark.parametrize("seed", range(25))
    def test_the_programme_finds_the_best_split(self, seed: int) -> None:
        # Exhaustive search on small videos: the most chapters (≤ k) that can each last the
        # minimum length, and among their splits the one with the least spread.
        rng = random.Random(seed)
        shots = make_shots([rng.choice([3.0, 8.0, 15.0, 25.0, 40.0]) for _ in range(9)])
        frames = []
        for i, shot in enumerate(shots):
            caption, place, setting = TOPICS[rng.choice(["lac", "cuisine", "temple"])]
            caption = f"{caption} {rng.choice(VIEWS)}"
            frames.append(make_frame(i, shot.start, i, caption, place=place, setting=setting))
        video = make_video(shots, frames)
        blocks = synthesis_blocks(video)
        k = rng.randint(2, 4)
        n = len(blocks)
        x = _vectors([block_signature(video, b) for b in blocks])
        w = np.array([min(b.duration, WEIGHT_CAP_S) + 1.0 for b in blocks])
        starts = [b.start for b in blocks] + [video.duration]
        min_len = max(MIN_CHAPTER_S, 0.5 * video.duration / k)

        def spread(a: int, z: int) -> float:
            mean = np.average(x[a:z], axis=0, weights=w[a:z])
            return float((w[a:z] * ((x[a:z] - mean) ** 2).sum(axis=1)).sum())

        def best(m: int) -> float | None:
            totals = []
            for cuts in itertools.combinations(range(1, n), m - 1):
                chapters = list(itertools.pairwise((0, *cuts, n)))
                if all(starts[z] - starts[a] >= min_len for a, z in chapters):
                    totals.append(sum(spread(a, z) for a, z in chapters))
            return min(totals, default=None)

        ranges = chapter_ranges(video, blocks, k)
        optimum = {m: total for m in range(2, k + 1) if (total := best(m)) is not None}
        if not optimum:
            assert ranges == [(1, n)]
            return
        count = max(optimum)
        assert len(ranges) == count
        assert sum(spread(a - 1, z) for a, z in ranges) == pytest.approx(optimum[count], abs=1e-9)

    def test_the_greedy_pick_prefers_a_pause_even_without_word_times(self) -> None:
        # Deviation from the research, which gave every boundary the pause bonus when the
        # transcript had no word times: only the silence between the two segments gets it.
        shots = make_shots([10.0] * 20)
        said = [Segment(0.0, 95.0, "Première partie"), Segment(105.0, 200.0, "Seconde partie")]
        video = make_video(shots, segments=said)
        blocks = synthesis_blocks(video)
        assert _greedy_spans(video, blocks, 2, video.duration) == [(0, 10), (10, 20)]

    def test_uneven_topics_with_a_given_count(self) -> None:
        runs = [("lac", 60.0), ("cuisine", 150.0), ("temple", 60.0), ("concert", 120.0)]
        video, run_starts = topic_video(runs)
        blocks = synthesis_blocks(video)
        assert chapter_starts(blocks, chapter_ranges(video, blocks, 4)) == run_starts
        # A topic shorter than half an even share (48.75 s here) takes a block of its neighbour.
        runs[2] = ("temple", 45.0)
        video, run_starts = topic_video(runs)
        blocks = synthesis_blocks(video)
        assert chapter_starts(blocks, chapter_ranges(video, blocks, 4)) == [0.0, 60.0, 202.5, 255.0]

    def test_the_words_said_chapter_a_talk_filmed_in_one_shot(self) -> None:
        rng = random.Random(2)
        editing = ["montage", "timeline", "raccourci", "clavier", "étalonnage", "séquence"]
        camera = ["caméra", "objectif", "lumière", "diaphragme", "capteur", "trépied"]
        change = 121.0
        segments = []
        t = 0.3
        while t < 258.0:
            topic = editing if t < change else camera
            segments.append(sentence(t, ["alors", *rng.sample(topic, 4), "voilà"]))
            t = segments[-1].end + 0.6
        shots = [Shot(0, 0.0, 263.0, "static", 1.0, "start", {}, ("Speech",))]
        frames = [
            make_frame(
                i, i * 10.0, 0, "Un homme parle devant un micro", place="Studio", setting="indoor"
            )
            for i in range(27)
        ]
        video = make_video(shots, frames, segments)
        blocks = synthesis_blocks(video)
        ranges = chapter_ranges(video, blocks, 2)
        assert len(ranges) == 2
        assert abs(chapter_starts(blocks, ranges)[1] - change) < 5.0

    def test_fewer_chapters_when_each_cannot_last_20_s(self) -> None:
        video, _ = topic_video([("lac", 30.0), ("cuisine", 30.0), ("temple", 40.0)], shot_s=10.0)
        blocks = synthesis_blocks(video)
        ranges = chapter_ranges(video, blocks, 10)
        assert 1 < len(ranges) <= 5
        edges = [*chapter_starts(blocks, ranges), video.duration]
        assert all(b - a >= MIN_CHAPTER_S for a, b in itertools.pairwise(edges))

    def test_one_chapter_when_none_can_be_long_enough(self) -> None:
        video, _ = topic_video([("lac", 15.0), ("cuisine", 15.0)])
        blocks = synthesis_blocks(video)
        assert chapter_ranges(video, blocks, 3) == [(1, len(blocks))]

    def test_nothing_described_and_nothing_said_still_covers_the_video(self) -> None:
        shots = make_shots([12.0] * 20)
        video = make_video(shots, [make_frame(i, s.start, i) for i, s in enumerate(shots)])
        blocks = synthesis_blocks(video)
        ranges = chapter_ranges(video, blocks)
        assert_covers(ranges, len(blocks))
        assert len(ranges) <= chapter_count(video.duration)

    def test_a_duration_left_unknown_ends_at_the_last_block(self) -> None:
        video, run_starts = topic_video([("lac", 45.0), ("cuisine", 45.0), ("temple", 45.0)])
        blocks = synthesis_blocks(video)
        unknown = make_video(list(video.shots), list(video.frames), duration=0.0)
        assert chapter_starts(blocks, chapter_ranges(unknown, blocks)) == run_starts

    def test_past_500_blocks_the_greedy_pick_still_finds_the_topics(self) -> None:
        runs = [("lac", 650.0), ("cuisine", 650.0), ("temple", 650.0), ("concert", 650.0)]
        video, run_starts = topic_video(runs, shot_s=5.0)
        blocks = synthesis_blocks(video)
        assert len(blocks) > DP_MAX_BLOCKS
        assert chapter_starts(blocks, chapter_ranges(video, blocks, 4)) == run_starts
        ranges = chapter_ranges(video, blocks)  # 2,600 s: 10 chapters at most
        assert_covers(ranges, len(blocks))
        assert len(ranges) <= chapter_count(video.duration)
        assert set(run_starts) <= set(chapter_starts(blocks, ranges))

    @settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        st.lists(
            st.tuples(
                st.floats(min_value=0.5, max_value=60.0),
                st.sampled_from([None, *TOPICS]),
            ),
            min_size=1,
            max_size=60,
        ),
        st.one_of(st.none(), st.integers(min_value=1, max_value=15)),
    )
    def test_the_chapters_always_cover_the_blocks_in_order(
        self, pieces: list[tuple[float, str | None]], k: int | None
    ) -> None:
        shots = make_shots([seconds for seconds, _ in pieces])
        frames = []
        for i, (shot, (_, topic)) in enumerate(zip(shots, pieces, strict=True)):
            caption, place, setting = TOPICS[topic] if topic else (None, "", "")
            frames.append(make_frame(i, shot.start, i, caption, place=place, setting=setting))
        video = make_video(shots, frames)
        blocks = synthesis_blocks(video)
        ranges = chapter_ranges(video, blocks, k)
        assert_covers(ranges, len(blocks))
        assert len(ranges) <= (k or chapter_count(video.duration))
        if len(ranges) > 1:  # each chapter lasts the minimum length
            wanted = k or chapter_count(video.duration)
            min_len = max(MIN_CHAPTER_S, 0.5 * video.duration / min(wanted, len(blocks)))
            starts = [*chapter_starts(blocks, ranges), video.duration]
            assert all(b - a >= min_len - 1e-9 for a, b in itertools.pairwise(starts))
