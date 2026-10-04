"""Geo parsing, capture-time resolution, colour metrics and shot segmentation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import cv2
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from vfe_vision.domain.capture_time import (
    NAME_DATE_ONLY,
    NAME_DATETIME,
    Candidate,
    Kind,
    parse_filename_date,
    resolve_capture_time,
)
from vfe_vision.domain.color import cct_mccamy, estimate_cct, exposure_metrics, kelvin_label
from vfe_vision.domain.enums import Confidence
from vfe_vision.domain.geo import GeoPoint, haversine_m, parse_iso6709, track_stats
from vfe_vision.domain.shots import (
    Motion,
    build_shots,
    classify_motion,
    detect_cuts,
    shown_motion,
)

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


class TestIso6709:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("+45.7640+004.8357/", (45.7640, 4.8357, None)),
            ("+48.8584+002.2945+035.000/", (48.8584, 2.2945, 35.0)),
            ("-33.8688+151.2093/", (-33.8688, 151.2093, None)),  # southern hemisphere (legacy bug)
            ("+40.7127837-074.0059413+010.5/", (40.7127837, -74.0059413, 10.5)),
            ("+4851.50+00217.67/", (48 + 51.5 / 60, 2 + 17.67 / 60, None)),  # DDMM.mm form
            ("+48.8584+2.2945/", None),  # longitude needs 3 degree digits
            ("+00.0000+000.0000/", None),  # null island
            ("", None),
            ("garbage", None),
        ],
    )
    def test_parse(self, text: str, expected: tuple[float, float, float | None] | None) -> None:
        point = parse_iso6709(text)
        if expected is None:
            assert point is None
        else:
            assert point is not None
            assert point.latitude == pytest.approx(expected[0], abs=1e-6)
            assert point.longitude == pytest.approx(expected[1], abs=1e-6)
            assert point.altitude_m == expected[2]

    @given(st.floats(min_value=-89.9, max_value=89.9), st.floats(min_value=-179.9, max_value=179.9))
    def test_roundtrip(self, lat: float, lon: float) -> None:
        text = f"{lat:+08.4f}{lon:+09.4f}/"
        point = parse_iso6709(text)
        if round(lat, 4) == 0 and round(lon, 4) == 0:
            assert point is None
        else:
            assert point is not None
            assert point.latitude == pytest.approx(lat, abs=1e-4)
            assert point.longitude == pytest.approx(lon, abs=1e-4)

    def test_distance_and_stats(self) -> None:
        paris, lyon = GeoPoint(48.8566, 2.3522), GeoPoint(45.7640, 4.8357)
        assert haversine_m(paris, lyon) == pytest.approx(391_500, rel=0.01)
        stats = track_stats([paris, lyon])
        assert stats is not None
        assert stats.points == 2
        assert stats.centre.latitude == pytest.approx((48.8566 + 45.7640) / 2)


class TestCaptureTime:
    def test_gps_wins(self) -> None:
        gps = datetime(2025, 7, 14, 16, 30, tzinfo=UTC)
        result = resolve_capture_time(
            [Candidate(datetime(2025, 7, 14, 18, 0), Kind.CAMERA_LOCAL, "QuickTime:CreateDate"),
             Candidate(gps, Kind.GPS_UTC, "GPSDateTime")],
            timezone="Europe/Paris", exported_by_editor=True, now=NOW,
        )  # fmt: skip
        assert (result.utc, result.confidence) == (gps, Confidence.HIGH)

    def test_offset_tag_is_trusted_even_after_export(self) -> None:
        tagged = datetime(2025, 7, 14, 18, 32, 10, tzinfo=timezone(timedelta(hours=2)))
        result = resolve_capture_time(
            [Candidate(tagged, Kind.WITH_OFFSET, "Keys:CreationDate")],
            timezone="Europe/Paris", exported_by_editor=True, now=NOW,
        )  # fmt: skip
        assert result.utc == datetime(2025, 7, 14, 16, 32, 10, tzinfo=UTC)
        assert result.confidence == Confidence.HIGH
        local = result.local
        assert local is not None
        assert local.hour == 18

    def test_camera_local_time_uses_the_timezone(self) -> None:
        result = resolve_capture_time(
            [Candidate(datetime(2025, 1, 10, 9, 0), Kind.CAMERA_LOCAL, "QuickTime:CreateDate")],
            timezone="Europe/Paris", exported_by_editor=False, now=NOW,
        )  # fmt: skip
        assert result.utc == datetime(2025, 1, 10, 8, 0, tzinfo=UTC)  # CET = UTC+1 in winter
        assert result.confidence == Confidence.MEDIUM

    def test_export_date_is_low_confidence(self) -> None:
        result = resolve_capture_time(
            [Candidate(datetime(2025, 7, 1, 23, 27, tzinfo=UTC), Kind.CONTAINER_UTC, "QuickTime:CreateDate")],
            timezone=None, exported_by_editor=True, now=NOW,
        )  # fmt: skip
        assert (result.source, result.confidence) == ("export_date", Confidence.LOW)

    @pytest.mark.parametrize(
        "bad",
        [
            datetime(1904, 1, 1, tzinfo=UTC),
            datetime(2000, 1, 1, tzinfo=UTC),
            NOW + timedelta(days=30),
        ],
    )
    def test_implausible_dates_are_rejected(self, bad: datetime) -> None:
        result = resolve_capture_time(
            [Candidate(bad, Kind.CONTAINER_UTC, "QuickTime:CreateDate")],
            timezone=None, exported_by_editor=False, now=NOW,
        )  # fmt: skip
        assert result.utc is None

    def test_clock_offset_is_applied(self) -> None:
        result = resolve_capture_time(
            [Candidate(datetime(2025, 7, 14, 16, 0, tzinfo=UTC), Kind.CONTAINER_UTC, "CreateDate")],
            timezone=None, exported_by_editor=False, clock_offset_s=-3600, now=NOW,
        )  # fmt: skip
        assert result.utc == datetime(2025, 7, 14, 15, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("name", "expected", "source"),
        [
            ("VID_20240509_143012.mp4", datetime(2024, 5, 9, 14, 30, 12), NAME_DATETIME),
            # Milliseconds glued to the clock (Pixel, in UTC): only the day is trusted.
            ("PXL_20240509_143012345.mp4", datetime(2024, 5, 9, 12), NAME_DATE_ONLY),
            ("2024-05-09 14.30.12.mov", datetime(2024, 5, 9, 14, 30, 12), NAME_DATETIME),
            ("Extra Life 20240509 MAG4 BE - Trim.mp4", datetime(2024, 5, 9, 12), NAME_DATE_ONLY),
            ("clip_2024_05_09.mp4", datetime(2024, 5, 9, 12), NAME_DATE_ONLY),
            ("2024-0509.mp4", None, None),  # mixed separators
            ("20240231.mp4", None, None),  # 31 February
            ("IMG_1234.MOV", None, None),
            ("take 120240509.mp4", None, None),  # longer digit run
        ],
    )
    def test_filename_dates(self, name: str, expected: datetime | None, source: str | None) -> None:
        candidate = parse_filename_date(name)
        if expected is None:
            assert candidate is None
        else:
            assert candidate is not None
            assert (candidate.value, candidate.source) == (expected, source)

    def test_rewritten_container_defers_to_the_file_name(self) -> None:
        name = parse_filename_date("Extra Life 20240509 - Trim.mp4")
        assert name is not None
        trimmed = Candidate(
            datetime(2026, 9, 26, 15, 18, tzinfo=UTC), Kind.CONTAINER_UTC, "CreateDate"
        )
        result = resolve_capture_time(
            [trimmed, name], timezone="Europe/Paris", exported_by_editor=False, now=NOW
        )
        assert result.source == NAME_DATE_ONLY
        assert result.confidence == Confidence.LOW
        assert result.utc == datetime(2024, 5, 9, 10, tzinfo=UTC)  # noon in Paris (CEST)

    def test_file_name_agreeing_with_the_container_keeps_the_container(self) -> None:
        name = parse_filename_date("VID_20240509_143012.mp4")
        assert name is not None
        container = Candidate(
            datetime(2024, 5, 9, 12, 30, 12, tzinfo=UTC), Kind.CONTAINER_UTC, "CreateDate"
        )
        result = resolve_capture_time(
            [name, container], timezone="Europe/Paris", exported_by_editor=False, now=NOW
        )
        assert (result.source, result.confidence) == ("CreateDate", Confidence.MEDIUM)

    def test_export_with_a_timestamped_name_uses_the_name(self) -> None:
        name = parse_filename_date("VID_20240509_143012 - export.mp4")
        assert name is not None
        export = Candidate(
            datetime(2025, 7, 1, 23, 27, tzinfo=UTC), Kind.CONTAINER_UTC, "CreateDate"
        )
        result = resolve_capture_time(
            [export, name], timezone="Europe/Paris", exported_by_editor=True, now=NOW
        )
        assert (result.source, result.confidence) == (NAME_DATETIME, Confidence.MEDIUM)
        assert result.utc == datetime(2024, 5, 9, 12, 30, 12, tzinfo=UTC)

    def test_dst_gap_does_not_crash(self) -> None:
        # 2025-03-30 02:30 does not exist in Europe/Paris.
        result = resolve_capture_time(
            [Candidate(datetime(2025, 3, 30, 2, 30), Kind.CAMERA_LOCAL, "CreateDate")],
            timezone="Europe/Paris", exported_by_editor=False, now=NOW,
        )  # fmt: skip
        assert result.utc is not None


class TestColor:
    def test_mccamy_reference_points(self) -> None:
        assert cct_mccamy(0.3127, 0.3290) == pytest.approx(6504, abs=30)  # D65
        assert cct_mccamy(0.4476, 0.4074) == pytest.approx(2856, abs=30)  # illuminant A

    def test_estimate_cct_warm_vs_cool(self) -> None:
        warm = np.full((120, 160, 3), (90, 160, 230), np.uint8)  # BGR orange-ish
        cool = np.full((120, 160, 3), (230, 190, 160), np.uint8)
        warm_k, cool_k = estimate_cct(warm), estimate_cct(cool)
        assert warm_k is not None
        assert cool_k is not None
        assert warm_k < 4000 < cool_k

    def test_kelvin_labels_cover_every_range(self) -> None:
        for kelvin in range(1500, 15001, 250):
            assert kelvin_label(kelvin)

    def test_exposure_metrics(self) -> None:
        image = np.zeros((100, 100, 3), np.uint8)
        image[:, 50:] = 255
        metrics = exposure_metrics(image)
        assert metrics.clipped_shadows == pytest.approx(0.5)
        assert metrics.clipped_highlights == pytest.approx(0.5)
        blurred = exposure_metrics(np.asarray(cv2.GaussianBlur(image, (21, 21), 0), dtype=np.uint8))
        assert blurred.sharpness < metrics.sharpness


class TestShots:
    def test_cuts_are_found_and_spaced(self) -> None:
        times = [i / 8 for i in range(48)]
        deltas = [2.0] * 48
        deltas[16] = 60.0  # hard cut at 2 s
        deltas[17] = 50.0  # too close to the previous cut
        deltas[32] = 12.0  # small change → no cut
        deltas[40] = 25.0  # 12× the baseline → adaptive cut at 5 s
        assert detect_cuts(times, deltas) == [2.0, 5.0]
        assert build_shots([2.0, 5.0], 6.0) == [(0.0, 2.0), (2.0, 5.0), (5.0, 6.0)]
        # A closing fade shorter than a shot joins the previous shot.
        assert build_shots([2.0, 5.9], 6.0) == [(0.0, 2.0), (2.0, 6.0)]

    def test_motion_classes(self) -> None:
        still = classify_motion([0.0] * 10, [0.0] * 10, [0.05] * 10, [0.0] * 10)
        assert still.motion == Motion.STATIC
        assert still.stability == pytest.approx(1.0)
        pan = classify_motion([-2.0] * 10, [0.1] * 10, [2.0] * 10, [0.0] * 10)
        assert pan.motion == Motion.PAN_RIGHT  # content moves left when the camera pans right
        zoom = classify_motion([0.0] * 10, [0.0] * 10, [1.0] * 10, [0.8] * 10)
        assert zoom.motion == Motion.ZOOM_IN
        shaky = classify_motion([1.5, -1.5] * 5, [-1.2, 1.2] * 5, [1.8] * 10, [0.0] * 10)
        assert shaky.motion == Motion.HANDHELD
        assert shaky.stability < 0.3

    def test_a_weak_soft_label_reads_as_a_still_camera(self) -> None:
        assert shown_motion("zoom_in", 0.4) == "static"  # a subject moving, not the camera
        assert shown_motion("handheld", 0.49) == "static"
        assert shown_motion("moving", 0.6) == "moving"
        assert shown_motion("pan_left", 0.3) == "pan_left"  # a direction is kept
