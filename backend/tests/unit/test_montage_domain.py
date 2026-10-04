"""Editing helpers: timecodes, exports, markers, cut points and framing."""

from __future__ import annotations

import csv
import io
import itertools
import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from vfe_vision.domain.cut_points import safe_cut
from vfe_vision.domain.editing import Clip
from vfe_vision.domain.exports import (
    EdlMarker,
    csv_cell,
    csv_text,
    decimal,
    marker_edl,
    marker_name,
    md_table,
    md_text,
    youtube_chapters,
)
from vfe_vision.domain.framing import (
    MAX_UPSCALE,
    Crop,
    fill_size,
    framing_segments,
    resolve_transform,
    static_framing,
)
from vfe_vision.domain.markers import (
    chapter_marker,
    highlight_marker,
    shot_marker,
    speech_markers,
    speech_sections,
)
from vfe_vision.domain.synthesis_input import Segment, Shot, Video, Word
from vfe_vision.domain.timecode import (
    frames_timecode,
    is_drop_frame,
    timecode_at,
    timecode_frames,
)
from vfe_vision.domain.transcript import build_cues


# ---------------------------------------------------------------- timecodes
class TestTimecodes:
    def test_drop_frame_skips_two_numbers_a_minute_but_every_tenth(self) -> None:
        assert frames_timecode(1799, 29.97, drop_frame=True) == "00:00:59;29"
        assert frames_timecode(1800, 29.97, drop_frame=True) == "00:01:00;02"
        assert frames_timecode(17982, 29.97, drop_frame=True) == "00:10:00;00"
        assert frames_timecode(107892, 29.97, drop_frame=True) == "01:00:00;00"
        assert frames_timecode(3600, 59.94, drop_frame=True) == "00:01:00;04"
        assert timecode_frames("01:00:00;00", 29.97) == 107892
        assert timecode_frames("01:00:00:00", 29.97) == 108000  # written non-drop-frame
        assert timecode_frames("10:00:00:00", 25.0) == 900000

    def test_drop_frame_only_at_drop_frame_rates(self) -> None:
        assert is_drop_frame("01:00:00;00", 29.97)
        assert not is_drop_frame("01:00:00;00", 25.0)
        assert not is_drop_frame("01:00:00:00", 29.97)
        assert not is_drop_frame(None, 29.97)
        assert frames_timecode(1800, 25.0, drop_frame=True) == "00:01:12:00"
        with pytest.raises(ValueError, match="hors limite"):
            timecode_frames("00:00:00:30", 29.97)

    @given(st.integers(min_value=0, max_value=24 * 3600 * 30 - 1), st.sampled_from([29.97, 59.94]))
    def test_drop_frame_round_trip(self, frames: int, fps: float) -> None:
        frames %= 2589408 * round(fps / 30)  # a day of drop-frame frames
        assert timecode_frames(frames_timecode(frames, fps, drop_frame=True), fps) == frames

    @given(
        st.integers(min_value=0, max_value=24 * 3600 * 25 - 1),
        st.sampled_from([23.976, 25.0, 30.0, 50.0]),
    )
    def test_non_drop_frame_round_trip(self, frames: int, fps: float) -> None:
        frames %= 24 * 3600 * round(fps)
        assert timecode_frames(frames_timecode(frames, fps), fps) == frames

    def test_source_timecode_from_the_files_own_start(self) -> None:
        assert timecode_at("10:00:00:00", 12.5, 25.0) == "10:00:12:12"
        assert timecode_at("00:00:59;28", 0.1, 29.97) == "00:01:00;02"  # 2 frames on, DF
        assert timecode_at("00:59:59;28", 0.1, 29.97) == "01:00:00;00"  # a tenth minute: kept
        assert timecode_at(None, 1.0, 29.97) == "00:00:00:29"  # no own timecode: non-drop


# ---------------------------------------------------------------- CSV, chapters, Markdown
class TestCsv:
    def test_french_excel_dialect(self) -> None:
        text = csv_text(["plan", "début (s)", "légende"],
                        [[1, 12.5, "Un chat; « dort »\nsur le canapé"], [2, 0.0, None]])  # fmt: skip
        assert text.startswith("﻿")
        assert "\r\n" in text
        assert "\n" not in text.replace("\r\n", "")
        rows = list(csv.reader(io.StringIO(text[1:]), delimiter=";"))
        assert rows == [["plan", "début (s)", "légende"],
                        ["1", "12,5", "Un chat; « dort » sur le canapé"], ["2", "0", ""]]  # fmt: skip

    @pytest.mark.parametrize(
        ("value", "cell"),
        [('=HYPERLINK("x")', "'=HYPERLINK(\"x\")"), ("+33 6", "'+33 6"), ("-2", "'-2"),
         ("@SUM(A1)", "'@SUM(A1)"), (-2.5, "-2,5"), (True, "oui"), (False, "non"), (3, "3"),
         (1 / 3, "0,333"), (math.nan, ""), ("\x1b[31mrouge", "[31mrouge")],
    )  # fmt: skip
    def test_formulas_are_text_numbers_stay_numbers(self, value: object, cell: str) -> None:
        assert csv_cell(value) == cell  # type: ignore[arg-type]

    def test_decimal_comma(self) -> None:
        assert (decimal(12.0), decimal(0.1 + 0.2), decimal(-0.0001), decimal(2.5, 1)) == (
            "12", "0,3", "0", "2,5")  # fmt: skip


class TestChapters:
    def test_youtube_lines_first_at_zero(self) -> None:
        text = youtube_chapters([(3.7, "Arrivée"), (65.9, "La plage\n(soir)"), (3725.0, "")])
        assert text == "00:00 Arrivée\n01:05 La plage (soir)\n1:02:05 Chapitre 3\n"
        assert youtube_chapters([]) == ""


class TestMarkdown:
    def test_untrusted_text_cannot_make_markup(self) -> None:
        text = md_text("![x](http://evil) <script>|#titre_*gras*`code`\n- liste")
        assert text == (r"\!\[x\](http://evil) \<script\>\|\#titre\_\*gras\*\`code\` - liste")
        assert md_table(["a", "b"], [["1", ""]]) == ["| a | b |", "|---|---|", "| 1 |   |"]


# ---------------------------------------------------------------- marker EDL
class TestEdl:
    def test_resolve_marker_edl(self) -> None:
        markers = [
            EdlMarker(50, 250, "Green", "Moment fort 1 | chat"),
            EdlMarker(0, 1, "Blue", "1. Arrivée"),
            EdlMarker(12, 1, "Plaid", "x"),
        ]
        text = marker_edl("Vacances", markers, fps=25.0, source_start="10:00:00:00")
        assert text.split("\r\n") == [
            "TITLE: Vacances",
            "FCM: NON-DROP FRAME",
            "",
            "001  001      V     C        10:00:00:00 10:00:00:01 01:00:00:00 01:00:00:01  ",
            " |C:ResolveColorBlue |M:1. Arrivée |D:1",
            "",
            "002  001      V     C        10:00:00:12 10:00:00:13 01:00:00:12 01:00:00:13  ",
            " |C:ResolveColorBlue |M:x |D:1",
            "",
            "003  001      V     C        10:00:02:00 10:00:02:01 01:00:02:00 01:00:02:01  ",
            " |C:ResolveColorGreen |M:Moment fort 1 / chat |D:250",
            "",
        ]

    def test_drop_frame_file_and_another_timeline_start(self) -> None:
        text = marker_edl("t", [EdlMarker(1800, 30, "Red", "m")], fps=29.97,
                          source_start="00:59:00;02", record_start="00:00:00:00")  # fmt: skip
        lines = text.split("\r\n")
        assert lines[1] == "FCM: DROP FRAME"
        assert lines[3].split()[4:8] == ["01:00:00;02", "01:00:00;03", "00:01:00;02", "00:01:00;03"]
        plain = marker_edl("t", [EdlMarker(0, 1, "Red", "m")], fps=29.97, source_start=None)
        assert "FCM: NON-DROP FRAME" in plain
        assert "00:00:00:00 00:00:00:01" in plain

    def test_names_stay_on_their_line(self) -> None:
        assert marker_name("a|b\nc\x00d") == "a/b cd"
        assert marker_name("") == "Marqueur"
        assert len(marker_name("mot " * 40)) <= 60


# ---------------------------------------------------------------- markers
class TestMarkers:
    def test_kinds_colours_and_custom_data(self) -> None:
        chapter = chapter_marker(2, 10.0, 40.0, "La plage", "Des vagues.")
        assert (chapter.color, chapter.custom_data, chapter.name, chapter.duration_s) == (
            "Blue", "vfe:chapter:2", "2. La plage", 30.0)  # fmt: skip
        clip = Clip(12.0, 18.0, 11.2, 18.0, ("le son commence 0,8 s avant l'image (J-cut)",))
        moment = highlight_marker(1, clip, "Le chat saute")
        assert (moment.color, moment.t_s, moment.duration_s) == ("Green", 12.0, 6.0)
        assert moment.note == ("Le chat saute — son 00:11.200 → 00:18.000 (le son commence 0,8 s "
                               "avant l'image (J-cut))")  # fmt: skip
        shot = shot_marker(3, 5.0, "fixe", 80, "Un chat\x00 dort")
        assert (shot.name, shot.note, shot.duration_s, shot.color) == (
            "Plan 3", "fixe · utilisable 80/100 · Un chat dort", 0.0, "Sand")  # fmt: skip

    def test_speech_sections_join_short_pauses(self) -> None:
        segments = (Segment(1.0, 3.0, "Bonjour."), Segment(4.0, 6.0, "Ça va ?"),
                    Segment(9.0, 10.0, "Oui."))  # fmt: skip
        assert speech_sections(segments) == [(1.0, 6.0), (9.0, 10.0)]
        first, second = speech_markers(segments)
        assert (first.note, first.duration_s, first.custom_data) == (
            "Bonjour. Ça va ?",
            5.0,
            "vfe:speech:1",
        )
        assert (second.color, second.note) == ("Lavender", "Oui.")


# ---------------------------------------------------------------- cut points
def _video(words: list[Word], shots: list[tuple[float, float]], duration: float = 30.0) -> Video:
    return Video(
        id="v", filename="v.mp4", duration=duration, orientation="horizontal", capture_local=None,
        place_label=None, place_feature=None, light_phase=None, day_part=None, weather=None,
        presence={}, heard=(), instruments=(), transcript_language="fr" if words else None,
        segments=(Segment(words[0].start, words[-1].end, "", tuple(words)),) if words else (),
        silences=(),
        shots=tuple(Shot(i, a, b, "static", 1.0, "cut", {}) for i, (a, b) in enumerate(shots)),
        frames=(), fps=25.0,
    )  # fmt: skip


def _talk(start: float, end: float) -> list[Word]:
    """A word every 0.5 s (0.4 s long), a sentence end on the last one."""
    words = [Word(start + k * 0.5, start + k * 0.5 + 0.4, f" mot{k}")
             for k in range(int((end - start) / 0.5))]  # fmt: skip
    words[-1] = Word(words[-1].start, words[-1].end, words[-1].text + ".")
    return words


class TestCutPoints:
    def test_edges_near_a_cut_go_to_it(self) -> None:
        video = _video([], [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)])
        early = safe_cut(video, 10.3, 19.8)  # just after a cut, just before one
        assert (early.clip.picture_in, early.clip.picture_out) == (10.0, 20.0)
        assert early.notes == (
            "entrée calée sur le début du plan 2",
            "sortie calée sur la fin du plan 2",
        )
        late = safe_cut(video, 9.7, 20.2)  # just before a cut, just after one
        assert (late.clip.picture_in, late.clip.picture_out) == (10.0, 20.0)
        assert late.notes[0] == "entrée calée sur le début du plan 2 (pas d'éclair du plan 1)"
        assert late.notes[1] == "sortie calée sur la fin du plan 2 (pas d'éclair du plan 3)"
        across = safe_cut(video, 5.0, 25.0)
        assert (across.shots, across.cuts) == ((0, 1, 2), (10.0, 20.0))
        assert across.notes == ()

    def test_an_edge_inside_a_word_moves_between_words(self) -> None:
        video = _video(_talk(2.0, 8.0), [(0.0, 30.0)])
        cut = safe_cut(video, 3.2, 6.1)  # both inside words
        for edge in (
            cut.clip.sound_in or cut.clip.picture_in,
            cut.clip.sound_out or cut.clip.picture_out,
        ):
            assert not any(w.start < edge < w.end for w in _talk(2.0, 8.0))
        assert cut.notes

    def test_a_sentence_across_a_cut_gets_an_l_cut(self) -> None:
        video = _video(_talk(8.0, 11.5), [(0.0, 10.0), (10.0, 30.0)])
        cut = safe_cut(video, 4.0, 10.1)  # out snaps back to the cut at 10 s, speech goes on
        assert cut.clip.picture_out == 10.0
        assert cut.clip.sound_out is not None
        assert cut.clip.sound_out > 10.0
        assert any("L-cut" in n for n in cut.notes)

    def test_clamped_and_refused(self) -> None:
        video = _video([], [(0.0, 30.0)])
        assert safe_cut(video, -3.0, 99.0).clip.picture_out == 30.0
        with pytest.raises(ValueError, match="intervalle vide"):
            safe_cut(video, 12.0, 12.0)
        no_shots = _video([], [])
        assert safe_cut(no_shots, 1.0, 2.0).shots == ()


# ---------------------------------------------------------------- framing
BOX = st.tuples(
    st.floats(0, 0.9), st.floats(0, 0.9), st.floats(0.01, 0.1), st.floats(0.01, 0.1)
).map(lambda b: (b[0], b[1], min(1.0, b[0] + b[2]), min(1.0, b[1] + b[3])))
SIZES = st.sampled_from([(1920, 1080), (1080, 1920), (3840, 2160), (1440, 1080), (1080, 1080)])


def _forward(crop: Crop, width: int, height: int, tw: int, th: int) -> list[tuple[float, float]]:
    """Where the crop's corners land on the timeline with Resolve's model (fit, zoom about the
    centre, Pan right, Tilt up)."""
    move = resolve_transform(crop, width, height, tw, th)
    scale = min(tw / width, th / height) * move.zoom
    corners = [(crop.x, crop.y), (crop.x + crop.width, crop.y + crop.height)]
    return [(tw / 2 + (x - width / 2) * scale + move.pan, th / 2 + (y - height / 2) * scale - move.tilt)
            for x, y in corners]  # fmt: skip


class TestFraming:
    @given(st.lists(BOX, max_size=6), SIZES, SIZES, st.none() | st.floats(0, 0.5))
    def test_the_crop_fills_the_timeline_and_stays_in_the_picture(
        self,
        boxes: list[tuple[float, float, float, float]],
        source: tuple[int, int],
        timeline: tuple[int, int],
        headroom: float | None,
    ) -> None:
        (width, height), (tw, th) = source, timeline
        framing = static_framing(boxes, width, height, tw, th, headroom=headroom)
        crop = framing.crop
        assert crop.x >= -1e-6
        assert crop.x + crop.width <= width + 1e-6
        assert crop.y >= -1e-6
        assert crop.y + crop.height <= height + 1e-6
        assert crop.width / crop.height == pytest.approx(tw / th)
        assert math.isclose(crop.width, width) or math.isclose(crop.height, height)  # largest
        (x0, y0), (x1, y1) = _forward(crop, width, height, tw, th)
        assert (x0, y0, x1, y1) == pytest.approx((0, 0, tw, th), abs=1e-6)
        assert 0.0 <= framing.coverage <= 1.0 + 1e-9
        assert framing.upscale == pytest.approx(tw / crop.width)

    @given(st.lists(BOX, min_size=1, max_size=6), SIZES, SIZES)
    def test_a_subject_that_fits_is_wholly_kept(
        self,
        boxes: list[tuple[float, float, float, float]],
        source: tuple[int, int],
        timeline: tuple[int, int],
    ) -> None:
        (width, height), (tw, th) = source, timeline
        crop_w, crop_h = fill_size(width, height, tw / th)
        union = (min(b[0] for b in boxes) * width, min(b[1] for b in boxes) * height,
                 max(b[2] for b in boxes) * width, max(b[3] for b in boxes) * height)  # fmt: skip
        assume(union[2] - union[0] <= crop_w and union[3] - union[1] <= crop_h)
        assert static_framing(boxes, width, height, tw, th).coverage == pytest.approx(1.0)

    def test_vertical_crop_of_a_horizontal_video(self) -> None:
        # A cat on the right of a 1920×1080 video, for a 1080×1920 timeline.
        framing = static_framing([(0.80, 0.4, 0.95, 0.9), (0.82, 0.45, 0.97, 0.9)],
                                 1920, 1080, 1080, 1920)  # fmt: skip
        crop = framing.crop
        assert (round(crop.width, 2), crop.height) == (607.5, 1080)
        assert crop.x + crop.width == pytest.approx(1920)  # held at the right edge
        assert framing.centred_on == "union"
        move = framing.transform
        assert move.zoom == pytest.approx((1080 / 607.5) / (1080 / 1920))  # 3.16 on a fit clip
        assert move.pan == pytest.approx(-(crop.center[0] - 960) * 1080 / 607.5)
        assert move.pan < 0
        assert move.tilt == 0
        assert framing.upscale > MAX_UPSCALE - 0.3

    def test_horizontal_crop_of_a_vertical_video_with_headroom(self) -> None:
        framing = static_framing([(0.3, 0.2, 0.7, 0.9)], 1080, 1920, 1920, 1080, headroom=0.1)
        crop = framing.crop
        assert (crop.width, round(crop.height, 2)) == (1080, 607.5)
        assert crop.y == pytest.approx(0.2 * 1920 - 0.1 * 607.5)
        assert framing.centred_on == "top"
        assert framing.transform.tilt < 0  # the picture moves down: its upper part shows

    def test_no_subject_same_shape_and_moving_subject(self) -> None:
        centred = static_framing([], 1920, 1080, 1080, 1920)
        assert (centred.centred_on, centred.coverage, centred.union) == ("picture", 1.0, None)
        assert centred.transform.pan == pytest.approx(0.0)
        same = static_framing([(0.1, 0.1, 0.3, 0.3)], 3840, 2160, 1920, 1080)
        assert (same.centred_on, same.transform.zoom, same.transform.pan) == ("picture", 1.0, 0.0)
        moving = [(0.0, 0.3, 0.1, 0.6), (0.05, 0.3, 0.15, 0.6), (0.85, 0.3, 0.95, 0.6)]
        wide = static_framing(moving, 1920, 1080, 1080, 1920)
        assert wide.centred_on == "median"
        assert wide.coverage < 1.0
        assert framing_segments(moving, 1920, 1080, 1080, 1920) == [(0, 1), (2, 2)]
        assert framing_segments([], 1920, 1080, 1080, 1920) == []
        with pytest.raises(ValueError, match="dimensions"):
            static_framing([], 0, 1080, 1080, 1920)


# ---------------------------------------------------------------- subtitles
WORD = st.tuples(
    st.floats(0, 3), st.floats(0.05, 1.2), st.text("abcdéèà,.?! ", min_size=1, max_size=14)
)


class _Seg:
    def __init__(self, words: list[tuple[float, float, str]]) -> None:
        self.words = [_W(a, b, t) for a, b, t in words]
        self.start = words[0][0] if words else 0.0
        self.end = words[-1][1] if words else 0.0
        self.text = "".join(t for _a, _b, t in words)
        self.suspect = False


class _W:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start, self.end, self.text, self.probability = start, end, text, 0.9


@given(st.lists(WORD, min_size=1, max_size=40))
def test_subtitle_cues_are_short_readable_and_never_overlap(
    raw: list[tuple[float, float, str]],
) -> None:
    t = 0.0
    words = []
    for gap, length, text in raw:
        t += gap
        words.append((t, t + length, " " + text.strip() if text.strip() else " x"))
        t += length
    cues = build_cues([_Seg(words)])
    for cue, following in itertools.pairwise(cues):
        assert cue.end <= following.start + 1e-9
    for cue in cues:
        assert 1 <= len(cue.lines) <= 2
        assert all(len(line) <= 42 for line in cue.lines)
        assert cue.end > cue.start
        assert cue.end - cue.start <= 7.0 + 1.5 + 1e-6  # max cue length, a last word may overhang
