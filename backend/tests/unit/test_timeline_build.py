"""« Create a timeline »: frame rates as editors read them, the order, the format most
videos have, and the FCPXML file."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, get_args

import pytest

from vfe_vision.api.schemas import TimelineRateName
from vfe_vision.domain.editing import Clip
from vfe_vision.domain.markers import chapter_start, highlight_marker, role_marker
from vfe_vision.domain.timeline_build import (
    DEFAULT_RATE,
    RATE_NAMES,
    FrameRate,
    TimelineFormat,
    TimelineOrder,
    TimelineParts,
    TimelineVideo,
    clean_name,
    fcpxml,
    file_name,
    file_uri,
    order_videos,
    rate_counts,
    rate_named,
    size_counts,
    standard_rate,
    suggested_format,
    suggested_name,
    timeline_cues,
    timeline_frames,
    timeline_seconds,
    video_markers,
)
from vfe_vision.domain.transcript import Cue


def _video(name: str, **fields: Any) -> TimelineVideo:
    values: dict[str, Any] = {
        "video_id": name,
        "filename": name,
        "path": "D:\\cats 2026\\" + name,
        "duration_s": 10.0,
        "fps": 30.0,
        "variable_rate": True,  # a Samsung phone file
        "width": 3840,
        "height": 2160,
    }
    return TimelineVideo(**(values | fields))


def _rate(name: str) -> FrameRate:
    rate = rate_named(name)
    assert rate is not None
    return rate


@pytest.mark.parametrize(
    ("fps", "variable", "expected"),
    [
        (30.0, True, "29.97"),  # what Resolve says of a Samsung file (average 30)
        (30.0, False, "30"),
        (29.97, False, "29.97"),
        (29.917, True, "29.97"),
        (23.98, False, "23.976"),
        (60.0, True, "59.94"),
        (120.0, True, "120"),
        (25.0, False, "25"),
        (26.0, False, None),  # an odd rate: no standard one
        (None, False, None),
        (0.0, False, None),
        (float("nan"), False, None),
    ],
)
def test_rates_as_editors_read_them(
    fps: float | None, variable: bool, expected: str | None
) -> None:
    rate = standard_rate(fps, variable=variable)
    assert (rate.name if rate else None) == expected


def test_the_api_offers_every_rate() -> None:
    assert get_args(TimelineRateName) == RATE_NAMES


def test_the_three_orders() -> None:
    paris = timezone(timedelta(hours=2))
    a = _video("clip10.mp4", captured_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC))
    b = _video("clip2.mp4", captured_at=datetime(2026, 9, 15, 11, 30, tzinfo=paris))  # 09:30 UTC
    c = _video("Clip3.mp4")  # no shooting time: at the end
    d = _video("z.mp4", captured_at=datetime(2026, 9, 15, 9, 0))  # stored without zone: UTC
    videos = [a, b, c, d]
    names = {order: [v.filename for v in order_videos(videos, order)] for order in TimelineOrder}
    assert names[TimelineOrder.CAPTURE] == ["z.mp4", "clip2.mp4", "clip10.mp4", "Clip3.mp4"]
    assert names[TimelineOrder.NAME] == ["clip2.mp4", "Clip3.mp4", "clip10.mp4", "z.mp4"]
    assert names[TimelineOrder.SELECTION] == ["clip10.mp4", "clip2.mp4", "Clip3.mp4", "z.mp4"]


def test_the_format_most_videos_have() -> None:
    videos = [
        _video("a", duration_s=10.0),
        _video("b", width=2160, height=3840, duration_s=100.0),
        _video("c", width=2160, height=3840, duration_s=5.0),
        _video("d", fps=25.0, variable_rate=False, duration_s=60.0),
    ]
    fmt = suggested_format(videos)
    # Two sizes twice each: the vertical one has more footage (105 s against 70 s).
    assert (fmt.rate.name, fmt.width, fmt.height) == ("29.97", 2160, 3840)
    assert [(rate.name, count) for rate, count in rate_counts(videos)] == [("29.97", 3), ("25", 1)]
    assert size_counts(videos) == [((2160, 3840), 2), ((3840, 2160), 2)]
    assert suggested_format([]) == TimelineFormat(DEFAULT_RATE, 1920, 1080)


def test_whole_files_fill_the_timeline_at_their_real_speed() -> None:
    phone = _video("a", duration_s=10.0)  # 300 frames, read at 29.97
    assert timeline_frames(phone, _rate("29.97")) == 300
    assert timeline_frames(phone, _rate("25")) == 250  # 300 / 29.97 × 25 = 250.25
    slow = _video("s", duration_s=2.0, fps=120.0, variable_rate=False)
    assert timeline_frames(slow, _rate("29.97")) == 59  # 2 s = 59.94 frames: never past the end
    odd = _video("o", duration_s=4.0, fps=26.0, variable_rate=False)
    assert timeline_frames(odd, _rate("29.97")) == 119
    assert timeline_seconds([phone, phone], _rate("29.97")) == pytest.approx(600 * 1001 / 30000)


def test_names() -> None:
    assert suggested_name([]) == "Video Frame Expedition"
    assert suggested_name([date(2026, 9, 15), date(2026, 9, 15)]) == "VFE 2026-09-15"
    assert suggested_name([date(2026, 9, 15), date(2026, 9, 12)]) == "VFE 2026-09-12 – 2026-09-15"
    assert clean_name("  Été\n à  Paris ") == "Été à Paris"
    assert clean_name("x" * 300) == "x" * 120
    assert file_name('Été: 1/2 "best"?') == "Été_ 1_2 _best__.zip"
    assert file_name(" ... ") == "timeline.zip"


def test_file_urls() -> None:
    assert file_uri("D:\\cats 2026\\été.mp4") == "file:///D:/cats%202026/%C3%A9t%C3%A9.mp4"
    assert file_uri("\\\\nas\\rushs\\a b.mov") == "file://nas/rushs/a%20b.mov"
    assert file_uri("/Volumes/cats 2026/a.mp4") == "file:///Volumes/cats%202026/a.mp4"


def _xml(text: str) -> ET.Element:
    return ET.fromstring(text)  # noqa: S314 - the application's own file


def _seconds(value: str | None) -> Fraction:
    assert value is not None
    assert value.endswith("s")
    return Fraction(value[:-1])


def test_fcpxml() -> None:
    videos = [
        _video("20260915_101010.mp4", duration_s=10.0),
        _video("export.mov", path="D:\\r\\export.mov", fps=29.97, variable_rate=False,
               duration_s=2.0, start_timecode="01:02:01:17", has_audio=False),
        _video("vertical.mp4", width=2160, height=3840, duration_s=0.02),  # 0 frame: left out
    ]  # fmt: skip
    text = fcpxml("Été <1> & co", videos, TimelineFormat(_rate("29.97"), 3840, 2160))
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
    root = _xml(text)
    assert root.get("version") == "1.10"
    fmt = root.find("resources/format")
    assert fmt is not None
    assert (fmt.get("frameDuration"), fmt.get("width"), fmt.get("height")) == (
        "1001/30000s", "3840", "2160",
    )  # fmt: skip

    phone, export, _ = root.findall("resources/asset")
    assert (phone.get("start"), phone.get("duration")) == ("0s", "300300/30000s")
    assert phone.get("hasAudio") == "1"
    media = phone.find("media-rep")
    assert media is not None
    assert media.get("src") == "file:///D:/cats%202026/20260915_101010.mp4"
    # A file whose timecode starts at 01:02:01:17: its first frame is 111,647 frames in.
    assert export.get("start") == f"{111647 * 1001}/30000s"
    assert export.get("hasAudio") is None

    project = root.find("library/event/project")
    assert project is not None
    assert project.get("name") == "Été <1> & co"
    sequence = project.find("sequence")
    assert sequence is not None
    assert sequence.get("tcStart") == "108108000/30000s"  # 01:00:00:00 at 29.97
    clips = sequence.findall("spine/asset-clip")
    assert [clip.get("name") for clip in clips] == ["20260915_101010.mp4", "export.mov"]
    frame = Fraction(1001, 30000)
    offset = _seconds(sequence.get("tcStart"))
    for clip in clips:
        assert _seconds(clip.get("offset")) == offset  # end to end, no gap
        assert _seconds(clip.get("duration")) % frame == 0  # whole timeline frames
        offset += _seconds(clip.get("duration"))
    assert _seconds(clips[1].get("start")) == _seconds(export.get("start"))
    assert _seconds(clips[1].get("duration")) == 59 * frame  # 2 s at 29.97, rounded down
    assert _seconds(sequence.get("duration")) == offset - _seconds(sequence.get("tcStart"))


def test_fcpxml_at_a_whole_rate() -> None:
    text = fcpxml("x", [_video("a.mp4", fps=25.0, variable_rate=False)],
                  TimelineFormat(_rate("25"), 1920, 1080))  # fmt: skip
    sequence = _xml(text).find("library/event/project/sequence")
    assert sequence is not None
    assert (sequence.get("tcStart"), sequence.get("duration")) == ("3600s", "10s")


def test_fcpxml_marks_where_chapters_start() -> None:
    chapters = (
        chapter_start(1, 0.0, "Arrivée au port", "Les bateaux"),
        chapter_start(2, 4.0, "", ""),
        chapter_start(3, 12.0, "Après la fin", ""),  # the clip lasts 10 s
    )
    video = _video("a.mp4", fps=25.0, variable_rate=False, start_timecode="10:00:00:00",
                   chapters=chapters)  # fmt: skip
    fmt = TimelineFormat(_rate("25"), 1920, 1080)
    root = ET.fromstring(fcpxml("x", [video], fmt))  # noqa: S314 - our own file
    clip = root.find(".//asset-clip")
    assert clip is not None
    # In the clip's own time: its file's timecode (10:00:00:00) plus the chapter's start.
    assert [(m.get("start"), m.get("duration"), m.get("value"), m.get("note"))
            for m in clip.findall("marker")] == [
        ("36000s", "1/25s", "Arrivée au port", "Les bateaux"),
        ("36004s", "1/25s", "Chapitre 2", None),
    ]  # fmt: skip
    plain = ET.fromstring(fcpxml("x", [video], fmt, TimelineParts(chapters=False)))  # noqa: S314
    assert plain.find(".//marker") is None


def test_fcpxml_markers_fall_on_the_file_frames() -> None:
    video = _video("a.mp4", chapters=(chapter_start(1, 0.0, "A", ""),
                                      chapter_start(2, 5.0, "B", "")))  # fmt: skip
    root = ET.fromstring(fcpxml("x", [video], TimelineFormat(_rate("25"), 1920, 1080)))  # noqa: S314
    # A phone file read at 29.97: 5 s is its frame 149, whatever the timeline's rate.
    assert [m.get("start") for m in root.iter("marker")] == ["0s", "149149/30000s"]


def _clip(start: float, end: float) -> Clip:
    return Clip(picture_in=start, picture_out=end, sound_in=None, sound_out=None, notes=())


def test_suggested_stretches_become_range_markers() -> None:
    video = _video(
        "a.mp4", fps=25.0, variable_rate=False,
        chapters=(chapter_start(1, 0.0, "Début", ""),),
        suggestions=(
            highlight_marker(1, _clip(2.0, 4.0), "Le bateau part."),
            role_marker("avoid", 1, _clip(8.0, 12.0), 20),  # runs past the clip's 10 s
            role_marker("b_roll", 1, _clip(11.0, 12.0), 70),  # starts after it
        ),
    )  # fmt: skip
    rate = _rate("25")
    markers = video_markers(video, rate, TimelineParts())
    assert [(m.kind, m.t_s, m.duration_s, m.color) for m in markers] == [
        ("chapter", 0.0, 0.0, "Blue"), ("highlight", 2.0, 2.0, "Green"),
        ("avoid", 8.0, 2.0, "Red"),
    ]  # fmt: skip
    assert markers[2].name == "À éviter"
    assert markers[2].note == "Suggestion de montage (indicatif) · utilisable 20/100"
    assert video_markers(video, rate, TimelineParts(suggestions=False)) == markers[:1]
    assert video_markers(video, rate, TimelineParts(chapters=False)) == markers[1:]
    root = ET.fromstring(fcpxml("x", [video], TimelineFormat(rate, 1920, 1080)))  # noqa: S314
    assert [(m.get("start"), m.get("duration"), m.get("value")) for m in root.iter("marker")] == [
        ("0s", "1/25s", "Début"), ("2s", "2s", "Moment fort 1 — Le bateau part."),
        ("8s", "2s", "À éviter"),
    ]  # fmt: skip


def test_subtitles_follow_their_video_on_the_timeline() -> None:
    first = _video("a.mp4", fps=25.0, variable_rate=False, duration_s=10.0,
                   speech=(Cue(1.0, 3.0, ("Bonjour",)), Cue(9.0, 12.0, ("coupé",)),
                           Cue(11.0, 12.0, ("après",))))  # fmt: skip
    second = _video("b.mp4", fps=25.0, variable_rate=False, duration_s=5.0,
                    speech=(Cue(0.5, 2.0, ("Suite",)),))  # fmt: skip
    cues = timeline_cues([first, second], _rate("25"), lambda v: v.speech)
    # The second video starts 10 s in; a cue running past its video's end is cut there.
    assert [(c.start, c.end, c.text) for c in cues] == [
        (1.0, 3.0, "Bonjour"), (9.0, 10.0, "coupé"), (10.5, 12.0, "Suite"),
    ]  # fmt: skip
