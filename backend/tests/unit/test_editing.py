"""Editing suggestions: roles, highlights and where to cut each clip (pure functions)."""

from __future__ import annotations

import bisect
import itertools
import math
import random
from collections.abc import Sequence

import pytest

from vfe_vision.domain.editing import (
    MIN_CLIP_S,
    SOUND_REACH_S,
    Clip,
    Roles,
    allowed_blocks,
    best_frame,
    clip_for,
    highlight_count,
    pick_highlights,
    roles,
    salience,
)
from vfe_vision.domain.synthesis_input import (
    Block,
    Frame,
    Segment,
    Shot,
    Video,
    Word,
    speech_seconds,
)

RICE = "Une main verse du riz dans la poêle"
CHAPTERS = ((1, 3), (4, 6), (7, 9))
USABLE = {1: 60, 2: 90, 3: 70, 4: 100, 5: 80, 6: 55, 7: 75, 8: 95, 9: 65}


def frame(
    idx: int,
    t: float,
    *,
    sharpness: float | None = 100.0,
    caption: str = RICE,
    shot_type: str = "medium",
    values: Sequence[str] = (),
    issues: Sequence[str] = (),
    actions: Sequence[str] = (),
    described: bool = True,
) -> Frame:
    data = {
        "caption": caption,
        "shot_type": shot_type,
        "editing_value": list(values),
        "quality_issues": list(issues),
        "actions": list(actions),
    }
    return Frame(idx, f"k{idx}", t, 0, sharpness, {}, data if described else None)


def block(
    no: int,
    start: float,
    end: float,
    *,
    frames: Sequence[Frame] = (),
    speech_s: float = 0.0,
) -> Block:
    return Block(no, start, end, (no - 1,), tuple(frames), speech_s, ())


def talk(start: float, end: float, *, step: float = 0.4, length: float = 0.3) -> Segment:
    """Continuous speech: a word every ``step`` seconds, the last one ending a sentence."""
    count = int((end - start - length) / step + 1e-9) + 1
    words = [Word(start + k * step, start + k * step + length, f" mot{k}") for k in range(count)]
    words[-1] = Word(words[-1].start, words[-1].end, words[-1].text + ".")
    return said(words)


def said(words: Sequence[Word]) -> Segment:
    return Segment(words[0].start, words[-1].end, "".join(w.text for w in words), tuple(words))


def video(
    duration: float,
    *,
    segments: Sequence[Segment] = (),
    silences: Sequence[tuple[float, float]] = (),
) -> Video:
    return Video(
        id="v",
        filename="v.mp4",
        duration=duration,
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
        segments=tuple(segments),
        silences=tuple(silences),
        shots=(Shot(0, 0.0, duration, "static", 1.0, "start", {}),),
        frames=(),
    )


def speaking_block(facts: Video, no: int, start: float, end: float, keyframe: float) -> Block:
    speech = speech_seconds(facts.segments, start, end)
    return block(no, start, end, frames=[frame(no, keyframe)], speech_s=speech)


def inside(t: float, spans: Sequence[tuple[float, float]]) -> bool:
    """Whether ``t`` falls strictly inside one of the words (sorted, not overlapping)."""
    i = bisect.bisect_right(spans, (t, math.inf)) - 1
    return i >= 0 and spans[i][0] < t < spans[i][1]


class TestSalience:
    def test_the_measured_formula(self) -> None:
        frames = [
            frame(0, 1.0, values=["hero", "action"]),
            frame(1, 3.0, values=["hero"], actions=["verse du riz"]),
            frame(2, 5.0, values=["hero"]),
        ]
        b = block(1, 0.0, 6.0, frames=frames, speech_s=3.0)
        # 50 × 1 + 20 × 3/3 hero + 15 × 2/3 action + 10 × 3 alike / 3 + 5 × 3 s / 6 s
        assert salience(b, 100) == pytest.approx(50 + 20 + 10 + 10 + 2.5)

    def test_usability_hero_and_length_order_the_blocks(self) -> None:
        strong = block(1, 0.0, 6.0, frames=[frame(0, 1.0, values=["hero"])])
        plain = block(2, 6.0, 12.0, frames=[frame(1, 7.0)])
        short = block(3, 12.0, 13.8, frames=[frame(2, 12.5)])
        scores = {
            "strong": salience(strong, 100),
            "plain": salience(plain, 100),
            "weak": salience(plain, 60),
            "short": salience(short, 100),
        }
        assert sorted(scores, key=scores.__getitem__, reverse=True) == [
            "strong",
            "plain",
            "weak",
            "short",
        ]

    def test_under_2_s_costs_30_points(self) -> None:
        frames = [frame(0, 0.5)]
        long_enough = salience(block(1, 0.0, 2.0, frames=frames), 90)
        assert long_enough - salience(block(1, 0.0, 1.9, frames=frames), 90) == pytest.approx(30)

    def test_descriptions_that_agree_count_as_consensus(self) -> None:
        alike = [
            frame(0, 1.0, caption=RICE),
            frame(1, 2.0, caption="Une main verse le riz dans une poêle"),
            frame(2, 3.0, caption="Une main verse du riz dans la poêle chaude"),
        ]
        distinct = [
            frame(0, 1.0, caption="Un chat dort au soleil"),
            frame(1, 2.0, caption="Une voiture rouge passe"),
            frame(2, 3.0, caption="Des montagnes enneigées"),
        ]
        gain = salience(block(1, 0.0, 4.0, frames=alike), 80) - salience(
            block(1, 0.0, 4.0, frames=distinct), 80
        )
        assert gain == pytest.approx(10 * (1 - 1 / 3))

    def test_without_descriptions_only_usability_and_speech_count(self) -> None:
        undescribed = block(1, 0.0, 4.0, frames=[frame(0, 1.0, described=False)], speech_s=2.0)
        assert salience(undescribed, 80) == pytest.approx(40 + 2.5)
        assert salience(block(1, 0.0, 4.0, speech_s=2.0), 80) == pytest.approx(42.5)


class TestHighlightCount:
    @pytest.mark.parametrize(
        ("duration", "usable_blocks", "expected"),
        [
            (138.0, 8, 2),  # RIZ, 2:18
            (263.0, 13, 4),  # the talk, 4:23
            (480.0, 88, 8),  # the Nepal archive, 8 min
            (3600.0, 284, 8),  # an hour: still 8
            (20.0, 3, 1),  # a short clip gets one
            (600.0, 6, 2),  # at most a third of the usable blocks
            (600.0, 2, 1),  # but one when there is any
            (600.0, 0, 0),  # none without a usable block
            (150.0, 30, 3),  # half a minute rounds up
            (math.nan, 30, 1),  # an unknown duration does not raise
            (math.inf, 30, 1),
        ],
    )
    def test_one_a_minute_capped(self, duration: float, usable_blocks: int, expected: int) -> None:
        assert highlight_count(duration, usable_blocks) == expected


class TestAllowedBlocks:
    def test_usable_and_long_enough(self) -> None:
        blocks = [block(1, 0, 4), block(2, 4, 8), block(3, 8, 9.2), block(4, 9.2, 15)]
        assert allowed_blocks(blocks, {1: 80, 2: 40, 3: 90}) == [1]  # 4 has no score

    def test_every_block_when_none_is_usable(self) -> None:
        blocks = [block(1, 0, 4), block(2, 4, 8)]
        assert allowed_blocks(blocks, {1: 20, 2: 30}) == [1, 2]
        assert allowed_blocks([], {}) == []


class TestPickHighlights:
    @staticmethod
    def blocks(count: int = 9, seconds: float = 4.0) -> list[Block]:
        return [block(n, (n - 1) * seconds, n * seconds) for n in range(1, count + 1)]

    def test_the_best_of_each_chapter_first(self) -> None:
        picks = pick_highlights(self.blocks(), CHAPTERS, USABLE, 3)
        assert picks == {0: [2], 1: [4], 2: [8]}

    def test_then_the_second_bests_the_strongest_first(self) -> None:
        picks = pick_highlights(self.blocks(), CHAPTERS, USABLE, 5)
        assert picks == {0: [2], 1: [4, 5], 2: [8, 7]}  # 5 (80) and 7 (75) before 3 (70)

    def test_more_chapters_than_picks_the_strongest_chapters(self) -> None:
        picks = pick_highlights(self.blocks(), CHAPTERS, USABLE, 2)
        assert picks == {0: [], 1: [4], 2: [8]}  # 4 (100) and 8 (95), not 2 (90) for coming first

    def test_never_more_than_the_candidates(self) -> None:
        picks = pick_highlights(self.blocks(), CHAPTERS, USABLE, 20)
        assert picks == {0: [2, 3, 1], 1: [4, 5, 6], 2: [8, 7, 9]}
        assert pick_highlights(self.blocks(), CHAPTERS, USABLE, 0) == {
            0: [],
            1: [],
            2: [],
        }

    def test_a_chapter_without_usable_block_gives_its_turn(self) -> None:
        usable = USABLE | {4: 40, 5: 30, 6: 45}
        picks = pick_highlights(self.blocks(), CHAPTERS, usable, 4)
        assert picks == {0: [2, 3], 1: [], 2: [8, 7]}

    def test_a_tie_goes_to_the_longer_block(self) -> None:
        blocks = [block(1, 0.0, 3.0), block(2, 3.0, 8.0)]
        assert pick_highlights(blocks, [(1, 2)], {1: 80, 2: 80}, 1) == {0: [2]}

    def test_one_chapter_can_hold_all_the_picks(self) -> None:
        blocks = self.blocks(10)
        usable = {n: 50 + 5 * n for n in range(1, 11)}
        picks = pick_highlights(blocks, [(1, 10)], usable, 8)
        assert picks == {0: [10, 9, 8, 7, 6, 5, 4, 3]}

    def test_one_shot_and_nothing(self) -> None:
        assert pick_highlights([block(1, 0.0, 12.0)], [(1, 1)], {1: 90}, 1) == {0: [1]}
        assert pick_highlights([], [], {}, 3) == {}


class TestRoles:
    def test_each_role_from_the_majority_of_keyframes(self) -> None:
        wide = [frame(0, 1.0, shot_type="wide"), frame(1, 3.0, shot_type="extreme_wide")]
        flagged = [frame(2, 6.0, values=["establishing"]), frame(3, 8.0)]
        half_of_twelve = [
            frame(4 + k, 10.2 + k * 0.2, values=["b_roll"] if k < 6 else []) for k in range(12)
        ]
        interview = [frame(10, 14.0, values=["b_roll", "interview"])]
        blocks = [
            block(1, 0.0, 5.0, frames=wide),
            block(2, 5.0, 10.0, frames=flagged),
            block(3, 10.0, 13.0, frames=half_of_twelve),  # 6 of 12: summed twelfths fell short
            block(4, 13.0, 16.0, frames=interview),
            block(5, 16.0, 20.0, frames=[frame(11, 17.0, values=["b_roll"])]),
            block(6, 20.0, 24.0, frames=[frame(12, 21.0, values=["detail"])]),
            block(7, 24.0, 25.8, frames=[frame(13, 24.5, values=["b_roll"])]),
            block(8, 25.8, 27.0, frames=[frame(14, 26.0, shot_type="wide")]),
            block(9, 27.0, 31.0, frames=[frame(15, 28.0, shot_type="wide")]),
        ]
        usable = {1: 90, 2: 80, 3: 80, 4: 90, 5: 40, 6: 65, 7: 90, 8: 90}
        assert roles(blocks, usable) == Roles(establishing=(1, 2), b_roll=(3,), avoid=(5,))

    def test_at_most_three_opening_shots_the_first_ones(self) -> None:
        blocks = [
            block(n, n * 4.0, n * 4.0 + 4, frames=[frame(n, n * 4.0, shot_type="wide")])
            for n in range(1, 6)
        ]
        assert roles(blocks, dict.fromkeys(range(1, 6), 90)).establishing == (1, 2, 3)

    def test_without_descriptions_only_avoid(self) -> None:
        blocks = [
            block(1, 0.0, 5.0, frames=[frame(0, 1.0, described=False)]),
            block(2, 5.0, 10.0),
        ]
        assert roles(blocks, {1: 90, 2: 30}) == Roles((), (), (2,))
        assert roles([], {}) == Roles((), (), ())


class TestBestFrame:
    def test_sharpest_halved_by_a_defect_raised_by_hero(self) -> None:
        plain, sharper = frame(0, 1.0, sharpness=50), frame(1, 2.0, sharpness=80)
        assert best_frame(block(1, 0.0, 5.0, frames=[plain, sharper])) == sharper
        flawed = frame(1, 2.0, sharpness=80, issues=["blur"])
        assert best_frame(block(1, 0.0, 5.0, frames=[plain, flawed])) == plain
        hero = frame(2, 3.0, sharpness=60, values=["action"])
        assert best_frame(block(1, 0.0, 5.0, frames=[plain, sharper, hero])) == hero

    def test_its_own_keyframes_before_the_one_in_force(self) -> None:
        before, own = frame(0, 1.0, sharpness=200), frame(1, 6.0, sharpness=10)
        assert best_frame(block(2, 5.0, 9.0, frames=[before, own])) == own
        assert best_frame(block(2, 5.0, 9.0, frames=[before])) == before
        assert best_frame(block(2, 5.0, 9.0)) is None
        assert best_frame(block(2, 5.0, 9.0, frames=[frame(3, 6.0, sharpness=None)])) is not None


class TestClipPicture:
    def test_6_s_around_the_best_keyframe(self) -> None:
        clip = clip_for(video(60.0), block(1, 0.0, 20.0, frames=[frame(0, 9.0)]))
        assert clip == Clip(7.0, 13.0, None, None, ())

    def test_snapped_to_the_block_edges(self) -> None:
        clip = clip_for(video(60.0), block(1, 10.0, 16.4, frames=[frame(0, 13.0)]))
        assert (clip.picture_in, clip.picture_out) == (10.0, 16.4)
        assert clip_for(video(60.0), block(1, 3.0, 5.0)) == Clip(3.0, 5.0, None, None, ())

    def test_10_s_with_speech_8_s_for_b_roll(self) -> None:
        talky = block(1, 0.0, 30.0, frames=[frame(0, 15.0)], speech_s=20.0)
        clip = clip_for(video(60.0), talky)
        assert clip.picture_in == pytest.approx(15 - 10 / 3)
        assert clip.picture_out - clip.picture_in == pytest.approx(10.0)
        clip = clip_for(video(60.0), talky, broll=True)
        assert clip.picture_out - clip.picture_in == pytest.approx(8.0)

    def test_empty_and_degenerate_blocks(self) -> None:
        assert clip_for(video(60.0), block(1, 0.0, 20.0)) == Clip(0.0, 6.0, None, None, ())
        assert clip_for(video(60.0), block(1, 4.0, 4.0)) == Clip(4.0, 4.0, None, None, ())


class TestClipSound:
    def test_l_cut_when_a_sentence_crosses_the_out_edge(self) -> None:
        # The sentence ends at 16.9 s, 0.9 s into the next shot; the next one starts at 20.1 s.
        facts = video(40.0, segments=[talk(11.0, 16.95), talk(20.1, 25.0)])
        b = speaking_block(facts, 2, 10.0, 16.0, 12.0)
        clip = clip_for(facts, b)
        assert (clip.picture_in, clip.picture_out) == (10.0, 16.0)  # never into the next shot
        assert clip.sound_in == 10.0
        assert clip.sound_out == pytest.approx(18.5)  # the pause after the sentence
        assert clip.notes == ("le son continue 2,5 s après la coupe (L-cut)",)

    def test_j_cut_when_speech_starts_before_the_shot(self) -> None:
        facts = video(40.0, segments=[talk(12.0, 17.2), talk(17.9, 24.0)])
        b = speaking_block(facts, 3, 20.0, 26.0, 22.0)
        clip = clip_for(facts, b)
        assert (clip.picture_in, clip.picture_out) == (20.0, 26.0)
        assert clip.sound_in == pytest.approx(17.5)  # after « mot12. », before « mot0 »
        assert clip.sound_out == 26.0
        assert clip.notes == ("le son commence 2,5 s avant l'image (J-cut)",)

    def test_no_sound_range_for_b_roll_or_without_speech(self) -> None:
        facts = video(40.0, segments=[talk(11.0, 16.95), talk(20.1, 25.0)])
        b = speaking_block(facts, 2, 10.0, 16.0, 12.0)
        assert clip_for(facts, b, broll=True) == Clip(10.0, 16.0, None, None, ())
        far = video(40.0, segments=[talk(30.0, 35.0)])
        assert clip_for(far, b) == Clip(10.0, 16.0, None, None, ())

    def test_a_pause_in_a_measured_silence_is_preferred(self) -> None:
        words = [
            Word(13.9, 14.6, " un"),
            Word(15.0, 16.9, " deux"),  # the out edge (16 s) falls inside
            Word(17.3, 17.8, " trois"),
        ]
        segments = [talk(1.0, 5.0), said(words), talk(22.0, 26.0)]
        b = block(2, 10.0, 16.0, frames=[frame(0, 12.0)])
        # Two pauses of 0.4 s: 17.1 s is nearer, so the sound runs on past the cut...
        clip = clip_for(video(40.0, segments=segments), b)
        assert clip.sound_out == pytest.approx(17.1)
        assert clip.notes == ("le son continue 1,1 s après la coupe (L-cut)",)
        # ...unless the other one is a measured silence: the cut goes into it, inside the shot.
        quiet = video(40.0, segments=segments, silences=[(14.7, 14.95)])
        clip = clip_for(quiet, b)
        assert clip.picture_out == pytest.approx(14.825)
        assert (clip.sound_in, clip.sound_out) == (None, None)
        assert clip.notes == ("sortie déplacée entre deux mots",)

    def test_a_silence_more_than_3_s_away_gets_no_bonus(self) -> None:
        # The in edge (30 s) falls in « c »; a short pause follows it, a long one lies 4.5 s back.
        words = [
            Word(24.0, 25.0, " a"),
            *(Word(26.0 + k * 0.5, 26.5 + k * 0.5, f" m{k}") for k in range(7)),  # to 29.5
            Word(29.5, 29.8, " b"),
            Word(29.8, 30.3, " c"),
            Word(30.5, 31.0, " d"),
            Word(31.0, 33.0, " e."),
        ]
        segments = [talk(1.0, 3.0), said(words), talk(50.0, 54.0)]
        b = block(2, 20.0, 40.0, frames=[frame(0, 32.0)])  # the window: 30 s to 36 s
        plain = clip_for(video(60.0, segments=segments), b)
        assert plain.picture_in == pytest.approx(30.4)
        # With the long pause a measured silence, it still loses: the bonus pulled this clip
        # 4.5 s back, beyond the 3 s the silences were measured within (a talk's 10 s
        # highlight grew to 17.5 s).
        quiet = clip_for(video(60.0, segments=segments, silences=[(25.2, 25.8)]), b)
        assert quiet == plain
        assert quiet.notes == ("entrée déplacée entre deux mots",)

    def test_a_short_block_with_a_j_cut_keeps_its_l_cut_short(self) -> None:
        words = [
            Word(7.0, 8.0, " un."),
            Word(8.6, 9.3, " trois"),
            Word(9.3, 10.7, " quatre"),  # holds the whole block
            Word(10.9, 11.5, " cinq"),
            Word(11.5, 12.4, " six"),
            Word(12.6, 13.0, " sept."),
        ]
        segments = [talk(1.0, 3.0), said(words), talk(30.0, 34.0)]
        clip = clip_for(video(40.0, segments=segments), block(2, 10.0, 10.6))
        assert (clip.picture_in, clip.picture_out) == (10.0, 10.6)
        assert clip.sound_in == pytest.approx(8.3)
        # 2 s from the sound's in point, not the picture's: the pause right after the block.
        assert clip.sound_out == pytest.approx(10.8)
        assert clip.notes == (
            "le son commence 1,7 s avant l'image (J-cut)",
            "le son continue 0,2 s après la coupe (L-cut)",
        )

    def test_a_pause_that_goes_back_to_the_edge_is_no_move(self) -> None:
        # The block starts 10 ms before « après »: the pause 45 ms back is the block's edge.
        words = [
            Word(9.0, 9.9, " avant"),
            Word(10.01, 10.5, " après"),
            *(Word(10.5 + k * 0.5, 11.0 + k * 0.5, f" m{k}") for k in range(4)),  # to 12.5
            Word(12.5, 13.0, " fin."),
        ]
        segments = [talk(1.0, 3.0), said(words), talk(30.0, 34.0)]
        clip = clip_for(
            video(40.0, segments=segments), block(2, 10.0, 30.0, frames=[frame(0, 12.0)])
        )
        assert clip == Clip(10.0, 16.0, None, None, ())

    def test_a_sound_edge_just_past_the_cut_reads_in_hundredths(self) -> None:
        words = [Word(14.0, 16.02, " long"), Word(16.1, 16.5, " mot")]
        segments = [talk(1.0, 5.0), said(words), talk(30.0, 34.0)]
        clip = clip_for(
            video(40.0, segments=segments), block(2, 10.0, 16.0, frames=[frame(0, 12.0)])
        )
        assert clip.sound_out == pytest.approx(16.06)
        assert clip.notes == ("le son continue 0,06 s après la coupe (L-cut)",)

    def test_a_word_in_two_whisper_pieces_is_never_split(self) -> None:
        words = [
            Word(14.0, 15.3, " Alors"),
            Word(15.5, 15.7, " J"),
            Word(16.3, 16.8, "'ajoute"),  # the gap inside « J'ajoute » holds the out edge
            Word(17.0, 17.2, " le"),
            Word(17.3, 17.8, " riz."),
        ]
        segments = [talk(1.0, 5.0), said(words), talk(30.0, 34.0)]
        clip = clip_for(
            video(40.0, segments=segments), block(2, 10.0, 16.0, frames=[frame(0, 12.0)])
        )
        assert clip.picture_out == pytest.approx(15.4)  # before « J'ajoute »
        assert clip.sound_out is None
        assert clip.notes == ("sortie déplacée entre deux mots",)

    def test_subtitles_without_word_times_cut_between_segments(self) -> None:
        segments = [Segment(9.0, 11.0, "Bonjour à tous."), Segment(11.5, 13.0, "On commence.")]
        clip = clip_for(
            video(40.0, segments=segments), block(2, 10.0, 16.0, frames=[frame(0, 12.0)])
        )
        assert clip.picture_in == 10.0
        assert clip.sound_in == pytest.approx(8.85)  # before the first line
        assert clip.notes[0].startswith("le son commence")


class TestClipInvariants:
    @staticmethod
    def random_speech(
        rng: random.Random, duration: float
    ) -> tuple[list[Segment], list[tuple[float, float]]]:
        """Segments of words, some split in two Whisper pieces; with the whole-word spans."""
        segments: list[Segment] = []
        spans: list[tuple[float, float]] = []
        t = rng.uniform(0.0, 3.0)
        while t < duration - 2.0:
            pieces: list[Word] = []
            for _ in range(rng.randint(3, 25)):
                start, end = t, t + rng.uniform(0.1, 0.7)
                spans.append((start, end))
                if rng.random() < 0.2:  # « J » + « 'ajoute »
                    cut = rng.uniform(start + 0.02, end - 0.02)
                    pieces += [Word(start, cut, " J"), Word(cut + 0.01, end, "'ajoute")]
                else:
                    pieces.append(Word(start, end, " mot" + ("." if rng.random() < 0.2 else "")))
                t = end + rng.choice([0.0, 0.03, 0.1, 0.25, 0.6, 1.2])
            segments.append(said(pieces))
            t += rng.uniform(0.2, 6.0)
        return segments, spans

    @pytest.mark.parametrize("seed", range(40))
    def test_picture_inside_the_block_sound_between_words(self, seed: int) -> None:
        rng = random.Random(seed)
        duration = 120.0
        segments, spans = self.random_speech(rng, duration)
        silences = [
            (a, a + rng.uniform(0.1, 1.0))
            for a in sorted(rng.uniform(0, duration) for _ in range(12))
        ]
        facts = video(duration, segments=segments, silences=silences)
        cuts = sorted({0.0, duration, *(round(rng.uniform(0, duration), 2) for _ in range(12))})
        for no, (lo, hi) in enumerate(itertools.pairwise(cuts), 1):
            b = speaking_block(facts, no, lo, hi, rng.uniform(lo - 1.0, hi))
            for broll in (False, True):
                clip = clip_for(facts, b, broll=broll)
                assert lo <= clip.picture_in <= clip.picture_out <= hi
                assert clip.picture_out - clip.picture_in >= min(MIN_CLIP_S, hi - lo) - 1e-9
                if clip.sound_in is None or clip.sound_out is None:
                    assert clip.sound_in is None
                    assert clip.sound_out is None
                    sound = clip.picture_in, clip.picture_out
                else:
                    assert not broll
                    sound = clip.sound_in, clip.sound_out
                    assert clip.picture_in - SOUND_REACH_S - 1e-9 <= sound[0] <= clip.picture_in
                    assert clip.picture_out <= sound[1] <= clip.picture_out + SOUND_REACH_S + 1e-9
                if not broll and not any("inévitable" in note for note in clip.notes):
                    assert not inside(sound[0], spans)
                    assert not inside(sound[1], spans)

    def test_a_one_hour_talk(self) -> None:
        # One shot of an hour cut into 10 s blocks, sentences of 8 s with 0.5 s pauses.
        segments = [talk(t, t + 8.0) for t in (k * 8.5 for k in range(423))]
        facts = video(3600.0, segments=segments)
        blocks = [
            speaking_block(facts, n, (n - 1) * 10.0, n * 10.0, (n - 1) * 10.0 + 5.0)
            for n in range(1, 361)
        ]
        spans = [(w.start, w.end) for s in segments for w in s.words]
        for b in blocks:
            clip = clip_for(facts, b)
            assert b.start <= clip.picture_in <= clip.picture_out <= b.end
            for t in (clip.sound_in, clip.sound_out):
                assert t is None or not inside(t, spans)
        usable = dict.fromkeys(range(1, 361), 90)
        count = highlight_count(facts.duration, len(allowed_blocks(blocks, usable)))
        chapters = [(k * 30 + 1, k * 30 + 30) for k in range(12)]
        picks = pick_highlights(blocks, chapters, usable, count)
        assert count == 8
        assert sorted(len(p) for p in picks.values()) == [0] * 4 + [1] * 8  # 8 chapters, 1 each
