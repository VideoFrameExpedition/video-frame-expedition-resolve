"""The files of a timeline to import by hand: the OTIO written as DaVinci Resolve
writes it, one SubRip file per subtitle track, and how to import them."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from vfe_vision.domain.editing import Clip
from vfe_vision.domain.markers import chapter_start, highlight_marker
from vfe_vision.domain.timeline_build import (
    TimelineFormat,
    TimelineParts,
    TimelineVideo,
    rate_named,
)
from vfe_vision.domain.timeline_files import (
    otio,
    read_me,
    shot_cue,
    subtitle_tracks,
    timeline_bundle,
)
from vfe_vision.domain.transcript import Cue


def _video(name: str, **fields: Any) -> TimelineVideo:
    values: dict[str, Any] = {"video_id": name, "filename": name, "path": "D:\\r\\" + name,
                              "duration_s": 10.0, "fps": 25.0}  # fmt: skip
    return TimelineVideo(**(values | fields))


def _format() -> TimelineFormat:
    rate = rate_named("25")
    assert rate is not None
    return TimelineFormat(rate, 1920, 1080)


def test_otio_as_resolve_writes_it() -> None:
    first = _video(
        "a.mp4", start_timecode="10:00:00:00",
        chapters=(chapter_start(1, 2.0, "Arrivée", "Le port"),),
        suggestions=(highlight_marker(1, Clip(8.0, 14.0, None, None, ()), "Le départ"),),
    )  # fmt: skip
    silent = _video("b.mp4", has_audio=False, duration_s=4.0)
    document = json.loads(otio("Été", [first, silent], _format()))
    assert document["name"] == "Été"
    assert document["global_start_time"] == {
        "OTIO_SCHEMA": "RationalTime.1", "rate": 25.0, "value": 90000,
    }  # fmt: skip
    video, audio = document["tracks"]["children"]
    assert (video["kind"], audio["kind"]) == ("Video", "Audio")
    clip = video["children"][0]
    # The clip's source starts at its file's timecode (10:00:00:00 = 900000 frames at 25).
    assert clip["source_range"]["start_time"]["value"] == 900000
    assert clip["source_range"]["duration"]["value"] == 250
    assert clip["media_references"]["DEFAULT_MEDIA"]["target_url"] == "D:\\r\\a.mp4"
    marks = [(m["name"], m["color"], m["marked_range"]["start_time"]["value"],
              m["marked_range"]["duration"]["value"], m["metadata"]["Resolve_OTIO"]["Note"])
             for m in clip["markers"]]  # fmt: skip
    # The highlight runs past the clip's end: cut there (250 − 200 frames).
    assert marks == [
        ("Arrivée", "BLUE", 900050, 1, "Le port"),
        ("Moment fort 1 — Le départ", "GREEN", 900200, 50, "Le départ"),
    ]
    # Picture and sound linked; a video without sound leaves a gap of its length.
    assert audio["children"][0]["markers"] == clip["markers"]
    assert (
        audio["children"][0]["metadata"]
        == clip["metadata"]
        == {"Resolve_OTIO": {"Link Group ID": 1}}
    )
    assert audio["children"][1]["OTIO_SCHEMA"] == "Gap.1"
    assert audio["children"][1]["source_range"]["duration"]["value"] == 100
    plain = json.loads(otio("x", [first], _format(), TimelineParts(chapters=False)))
    assert [m["name"] for m in plain["tracks"]["children"][0]["children"][0]["markers"]] == [
        "Moment fort 1 — Le départ"
    ]


def test_one_subtitle_file_per_track() -> None:
    video = _video(
        "a.mp4",
        speech=(Cue(1.0, 2.0, ("Bonjour",)),),
        shot_texts=(Cue(0.0, 10.0, ("Un port",)),),
    )
    parts = TimelineParts(shots=True)
    assert [(t.label, len(t.cues)) for t in subtitle_tracks([video], _format(), parts)] == [
        ("Transcription", 1), ("Plans", 1),
    ]  # fmt: skip
    files = dict(timeline_bundle("Été", [video], _format(), parts))
    # The language at the end: the shots' is the timeline's; the speech's unknown.
    assert set(files) == {
        "LISEZ-MOI.txt", "Été_SHOTS_FR.srt", "Été.srt", "Été.fcpxml", "Été.otio",
    }  # fmt: skip
    assert files["Été_SHOTS_FR.srt"].decode() == "1\n00:00:00,000 --> 00:00:10,000\nUn port\n"
    only_speech = dict(timeline_bundle("Été", [video], _format(), TimelineParts()))
    assert "Été_SHOTS_FR.srt" not in only_speech


def test_the_files_of_a_timeline_in_english() -> None:
    french = _video("a.mp4", speech=(Cue(1.0, 2.0, ("Bonjour",)),),
                    shot_texts=(Cue(0.0, 10.0, ("A harbour",)),))  # fmt: skip
    video = replace(french, speech_language="fr")
    parts = TimelineParts(shots=True)
    tracks = subtitle_tracks([video], _format(), parts, "en")
    assert [(t.label, t.language) for t in tracks] == [("Transcript", "fr"), ("Shots", "en")]
    files = dict(timeline_bundle("Summer", [video], _format(), parts, "en"))
    assert set(files) == {
        "README.txt", "Summer_FR.srt", "Summer_SHOTS_EN.srt", "Summer.fcpxml", "Summer.otio",
    }  # fmt: skip
    read_me = files["README.txt"].decode()
    assert "File › Import › Timeline" in read_me
    assert "“Summer_SHOTS_EN.srt” (Shots)" in read_me
    other = replace(video, video_id="b", speech_language="en")
    assert subtitle_tracks([video, other], _format(), parts)[0].language is None  # two spoken


def test_read_me_says_how_to_import() -> None:
    text = read_me("Été", [])
    assert "Fichier › Importer › Timeline… : choisissez « Été.otio »" in text
    assert "Sous-titres" not in text
    assert text.endswith("\r\n")


def test_a_shot_description_as_subtitle() -> None:
    cue = shot_cue(0.0, 5.0, "Un très long plan " * 20)
    assert cue is not None
    assert len(cue.lines) <= 3
    assert all(len(line) <= 42 for line in cue.lines)
    assert cue.lines[-1].endswith("…")
    assert shot_cue(0.0, 5.0, "  ") is None
    assert shot_cue(5.0, 5.0, "Un port") is None
