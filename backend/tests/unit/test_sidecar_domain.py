"""The analysis file next to each video: name, format and pure helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from vfe_vision.domain.sidecar import (
    FORMAT,
    FORMAT_VERSION,
    import_problem,
    is_ours,
    iso_utc,
    language_suffix,
    localized,
    other_name,
    paired,
    parse,
    parse_utc,
    render,
    sidecar_name,
    story_keyframes,
    synthesis_data,
    without_folder,
    written_language,
)
from vfe_vision.domain.translation import Dictionary, text_key


class TestName:
    def test_the_stem_with_txt(self) -> None:
        siblings = ["20260812_201337.mp4", "20260812_201337.SRT", "autre.mov"]
        assert sidecar_name("20260812_201337.mp4", siblings) == "20260812_201337.txt"

    def test_two_videos_with_one_stem_keep_their_extension(self) -> None:
        siblings = ["clip.mp4", "Clip.MOV", "clip.txt"]
        assert sidecar_name("clip.mp4", siblings) == "clip.mp4.txt"
        assert sidecar_name("Clip.MOV", siblings) == "Clip.MOV.txt"

    def test_only_videos_collide(self) -> None:
        # A subtitle, a GPS track, our own file or a macOS resource file are no videos.
        siblings = ["clip.mp4", "clip.srt", "clip.gpx", "clip.txt", "clip.mp4.txt", "._clip.mov"]
        assert sidecar_name("clip.mp4", siblings) == "clip.txt"
        assert sidecar_name("clip.mp4", ["CLIP.MP4"]) == "clip.txt"  # itself, another spelling

    def test_the_other_spelling_is_looked_for_too(self) -> None:
        assert other_name("clip.mp4", "clip.txt") == "clip.mp4.txt"
        assert other_name("clip.mp4", "clip.mp4.txt") == "clip.txt"


def _document(**video: object) -> dict[str, object]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "video": {"fingerprint": "abc", "size_bytes": 10, **video},
    }


class TestReading:
    def test_ours_and_describing_this_video(self) -> None:
        assert import_problem(_document(), fingerprint="abc", size_bytes=10) is None

    @pytest.mark.parametrize(
        ("document", "reason"),
        [
            ({"notes": "mes notes"}, "pas été écrit par l'application"),
            ([1, 2], "pas été écrit par l'application"),
            (_document() | {"format_version": FORMAT_VERSION + 1}, "plus récente"),
            (_document() | {"format_version": "1"}, "illisible"),
            (_document() | {"format_version": True}, "illisible"),
            (_document(fingerprint="autre"), "autre contenu"),
            (_document(size_bytes=11), "autre contenu"),
            ({"format": FORMAT, "format_version": 1}, "absente"),
        ],
    )
    def test_left_aside(self, document: object, reason: str) -> None:
        problem = import_problem(document, fingerprint="abc", size_bytes=10)
        assert problem is not None
        assert reason in problem

    def test_json_with_or_without_a_bom(self) -> None:
        assert parse(b'\xef\xbb\xbf{"format": "vfe-vision-analysis"}') == {"format": FORMAT}
        assert is_ours(parse(json.dumps({"format": FORMAT}).encode()))
        assert parse(b"Notes de tournage\n") is None
        assert parse(b"\xff\xfe\x00") is None
        assert not is_ours(None)


class TestValues:
    def test_dates_in_utc(self) -> None:
        paris = datetime(2025, 7, 14, 18, 30, tzinfo=timezone(timedelta(hours=2)))
        assert iso_utc(paris) == "2025-07-14T16:30:00Z"
        assert iso_utc(datetime(2025, 7, 14, 16, 30)) == "2025-07-14T16:30:00Z"  # naive: UTC
        assert parse_utc("2025-07-14T16:30:00Z") == datetime(2025, 7, 14, 16, 30, tzinfo=UTC)
        assert parse_utc("2025-07-14T16:30:00").tzinfo == UTC

    def test_no_absolute_path_is_kept(self) -> None:
        folder = r"C:\Users\me\Rushs été"
        value = {
            "format": {"filename": r"C:\Users\me\Rushs été\clip.mp4", "size": "1"},
            "exif": {
                "SourceFile": "C:/Users/me/Rushs été/clip.mp4",
                "System:Directory": "c:/users/me/rushs été",
                "Notes": ["un plan", r"C:\Users\me\Rushs été\sous\clip.srt"],
            },
            "other": r"C:\Users\me\Rushs été-2\clip.mp4",  # another folder: its own name
            "count": 3,
        }
        assert without_folder(value, folder) == {
            "format": {"filename": "clip.mp4", "size": "1"},
            "exif": {
                "SourceFile": "clip.mp4",
                "System:Directory": ".",
                "Notes": ["un plan", r"sous\clip.srt"],
            },
            "other": r"C:\Users\me\Rushs été-2\clip.mp4",
            "count": 3,
        }


class TestIds:
    def test_story_frames(self) -> None:
        mapping = {"k1": "n1", "k2": "n2"}
        assert story_keyframes(["k1", None, "k2", "gone", 3], mapping) == [
            "n1",
            None,
            "n2",
            None,  # a keyframe the file does not have: extracted again like an extra frame
            None,
        ]

    def test_synthesis_blocks(self) -> None:
        data = {
            "title": "Un titre",
            "blocks": [{"no": 1, "keyframe_ids": ["k1", "gone"]}, {"no": 2}, "?"],
        }
        assert synthesis_data(data, {"k1": "n1"}) == {
            "title": "Un titre",
            "blocks": [{"no": 1, "keyframe_ids": ["n1"]}, {"no": 2, "keyframe_ids": []}],
        }


def test_one_line_per_row_and_valid_json() -> None:
    document = {
        "format": FORMAT,
        "video": {"filename": "clip.mp4", "duration_s": 6.0},
        "user": {},
        "keyframes": [{"id": "k1", "t_s": 0.0}, {"id": "k2", "t_s": 2.0, "note": "élan"}],
        "shots": [],
        "transcript": None,
    }
    text = render(document)
    assert json.loads(text) == document
    lines = text.splitlines()
    assert '  {"id": "k2", "t_s": 2.0, "note": "élan"}' in lines  # not escaped, one row a line
    assert '  "filename": "clip.mp4",' in lines
    assert ' "shots": [],' in lines
    assert text.endswith("}\n")
    assert "\r" not in text


# ---------------------------------------------------------------- one file per language
DOCUMENT: dict[str, Any] = {
    "format": FORMAT,
    "exported_at": "2026-09-29T10:00:00Z",
    "frame_analyses": [
        {"language": "fr", "data": {"caption": "Un chat dort.", "visible_text": "STOP"}},
        {"language": "fr", "data": {"caption": "A cat wakes up."}},
    ],
    "shot_stories": [{"language": "fr", "story": {"summary": "Le chat."}, "answer": {}}],
    "detections": [
        {"source": "vlm", "label": "chat"},
        {"source": "detector", "label": "cat"},
    ],
    "context_place": {"label": "Bruxelles", "country": "Belgique", "data": {"road": "Rue Neuve"}},
    "video_synthesis": None,
}


class TestLanguages:
    def test_the_names_end_with_the_language(self) -> None:
        assert language_suffix("fr") == "_FR.txt"
        assert sidecar_name("clip.mp4", ["clip.mp4"], language_suffix("en")) == "clip_EN.txt"
        siblings = ["clip.mp4", "clip.mov"]
        assert sidecar_name("clip.mp4", siblings, language_suffix("en")) == "clip.mp4_EN.txt"
        assert other_name("clip.mp4", "clip_FR.txt", "_FR.txt") == "clip.mp4_FR.txt"

    def test_the_language_most_rows_say(self) -> None:
        assert written_language(DOCUMENT, "en") == "fr"
        assert written_language({"frame_analyses": []}, "en") == "en"

    def test_a_document_in_one_language(self) -> None:
        english = Dictionary(
            "en",
            {text_key("Un chat dort."): "A cat sleeps.", text_key("chat"): "cat",
             text_key("Belgique"): "Belgium"},
        )  # fmt: skip
        out = localized(DOCUMENT, english)
        assert out["language"] == "en"
        assert out["frame_analyses"][0] == {
            "language": "en", "data": {"caption": "A cat sleeps.", "visible_text": "STOP"},
        }  # fmt: skip
        assert out["frame_analyses"][1]["data"]["caption"] == "A cat wakes up."  # no entry
        assert [d["label"] for d in out["detections"]] == ["cat", "cat"]
        assert out["context_place"]["country"] == "Belgium"
        assert out["context_place"]["data"] == {"road": "Rue Neuve"}
        assert DOCUMENT["frame_analyses"][0]["data"]["caption"] == "Un chat dort."  # untouched

    def test_the_two_files_pair_text_by_text(self) -> None:
        english = localized(DOCUMENT, Dictionary("en", {text_key("Un chat dort."): "A cat."}))
        french = localized(DOCUMENT, Dictionary("fr", {}))
        pairs = paired(french, english)
        assert ("Un chat dort.", "A cat.") in pairs
        assert ("chat", "chat") in pairs  # the same words: left for the stage to ask
        assert "STOP" not in {a for a, _ in pairs}
        assert paired(french, {**english, "exported_at": "another"}) == []  # not written together
