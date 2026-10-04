"""Pure domain logic: timecodes, probe parsing, orientation, de-duplication, identifiers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.core.ids import new_id
from vfe_vision.domain.dedup import DuplicateFilter, dhash, hamming
from vfe_vision.domain.enums import Orientation
from vfe_vision.domain.media import is_editing_software, orientation_of, parse_probe
from vfe_vision.domain.timecode import format_clock, parse_timecode, seconds_to_frame


class TestTimecode:
    @pytest.mark.parametrize(
        ("seconds", "millis", "expected"),
        [
            (0, False, "00:00"),
            (83.5, False, "01:23"),
            (83.5, True, "01:23.500"),
            (3725.0, False, "1:02:05"),
            (59.9996, True, "01:00.000"),  # rounding carries into the seconds
        ],
    )
    def test_format_clock(self, seconds: float, millis: bool, expected: str) -> None:
        assert format_clock(seconds, millis=millis) == expected

    def test_format_clock_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="invalide"):
            format_clock(-1)

    def test_parse_timecode_uses_nominal_rate(self) -> None:
        assert parse_timecode("01:01:33:08", 29.97) == pytest.approx(3693 + 8 / 30)

    def test_parse_timecode_rejects_frame_overflow(self) -> None:
        with pytest.raises(ValueError, match="hors limite"):
            parse_timecode("00:00:01:30", 29.97)

    @given(st.floats(min_value=0, max_value=36_000), st.sampled_from([23.976, 25, 29.97, 60]))
    def test_seconds_to_frame_is_monotonic(self, seconds: float, fps: float) -> None:
        assert seconds_to_frame(seconds, fps) <= seconds_to_frame(seconds + 1 / fps, fps)


class TestProbe:
    def test_resolve_export_is_detected(
        self, ffprobe_json: Callable[[str], dict[str, Any]]
    ) -> None:
        info = parse_probe(ffprobe_json("ffprobe_resolve_export.json"))
        assert (info.width, info.height) == (3840, 2160)
        assert info.fps == pytest.approx(29.97, abs=0.01)
        assert info.video_codec == "hevc"
        assert info.start_timecode == "01:01:33:08"
        assert info.is_hdr is False
        assert info.exported_by_editor is True
        assert info.orientation is Orientation.HORIZONTAL

    def test_rotation_swaps_display_dimensions(self) -> None:
        data = {
            "format": {"duration": "10.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "avg_frame_rate": "30000/1001",
                    "color_transfer": "arib-std-b67",
                    "side_data_list": [{"rotation": -90}],
                }
            ],
        }
        info = parse_probe(data)
        assert (info.width, info.height, info.rotation) == (2160, 3840, 270)
        assert info.orientation is Orientation.VERTICAL
        assert info.is_hdr is True  # HLG
        assert info.has_audio is False

    def test_ten_bit_is_not_hdr(self) -> None:
        data = {
            "format": {},
            "streams": [
                {"codec_type": "video", "pix_fmt": "yuv420p10le", "color_transfer": "bt709"}
            ],
        }
        info = parse_probe(data)
        assert info.bit_depth == 10
        assert info.is_hdr is False

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [(1920, 1080, Orientation.HORIZONTAL), (1080, 1920, Orientation.VERTICAL),
         (1080, 1080, Orientation.SQUARE), (1000, 1020, Orientation.SQUARE), (None, 1080, None)],
    )  # fmt: skip
    def test_orientation(
        self, width: int | None, height: int | None, expected: Orientation
    ) -> None:
        assert orientation_of(width, height) is expected

    @pytest.mark.parametrize(
        ("tag", "expected"),
        [("Blackmagic Design DaVinci Resolve Studio", True), ("Lavf61.1.100", False),
         ("Apple iPhone 15 Pro", False), (None, False)],
    )  # fmt: skip
    def test_editing_software(self, tag: str | None, expected: bool) -> None:
        assert is_editing_software(tag) is expected


class TestDedup:
    def test_identical_images_have_zero_distance(self) -> None:
        image = np.random.default_rng(1).integers(0, 255, (8, 9), dtype=np.uint8)
        assert hamming(dhash(image), dhash(image.copy())) == 0

    def test_filter_uses_a_sliding_window(self) -> None:
        dedup = DuplicateFilter(max_distance=2, window=2)
        dedup.keep(0b0000)
        assert dedup.is_duplicate(0b0001)
        dedup.keep(0xFF00)
        dedup.keep(0x00FF)  # 0b0000 leaves the window
        assert not dedup.is_duplicate(0b0001)

    def test_dhash_rejects_wrong_size(self) -> None:
        with pytest.raises(ValueError, match="9×8"):
            dhash(np.zeros((8, 8), dtype=np.uint8))


def test_ids_are_unique_sortable_hex() -> None:
    ids = [new_id() for _ in range(2000)]
    assert len(set(ids)) == len(ids)
    assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)
    stamps = [i[:12] for i in ids]  # 48-bit millisecond timestamp prefix
    assert stamps == sorted(stamps)
    assert ids[0][12] == "7"  # UUID version nibble


def test_a_mirrored_display_matrix_is_flagged() -> None:
    from vfe_vision.domain.media import parse_probe

    def probe(matrix: str, rotation: int) -> dict[str, object]:
        side = {"side_data_type": "Display Matrix", "displaymatrix": matrix, "rotation": rotation}
        stream = {
            "codec_type": "video", "codec_name": "h264", "width": 320, "height": 240,
            "pix_fmt": "yuv420p", "avg_frame_rate": "25/1", "r_frame_rate": "25/1",
            "side_data_list": [side],
        }  # fmt: skip
        return {"streams": [stream], "format": {"duration": "1.0"}}

    rows = "\n00000000: {} {} 0\n00000001: {} {} 0\n00000002: 0 0 1073741824\n"
    portrait = parse_probe(probe(rows.format(0, 65536, -65536, 0), -90))
    assert (portrait.rotation, portrait.flipped) == (270, False)
    # ffprobe reports a vertical mirror as a 0° rotation: only the matrix tells.
    mirrored = parse_probe(probe(rows.format(65536, 0, 0, -65536), 0))
    assert (mirrored.rotation, mirrored.flipped) == (0, True)
