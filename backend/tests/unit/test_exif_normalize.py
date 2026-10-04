"""ExifTool normalisation and sidecar parsers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from vfe_vision.adapters.exiftool.normalize import normalize, parse_exif_date
from vfe_vision.adapters.exiftool.sidecars import find_sidecars, parse_dji_srt, parse_gpx
from vfe_vision.domain.capture_time import Kind

IPHONE = {
    "Keys:Make": "Apple",
    "Keys:Model": "iPhone 15 Pro",
    "Keys:Software": "17.5",
    "Keys:CreationDate": "2025:07:14 18:32:10+02:00",
    "Keys:GPSCoordinates": "+48.8584+002.2945+035.000/",
    "QuickTime:CreateDate": "2025:07:14 16:32:10",
}

GOPRO = {
    "QuickTime:Make": "GoPro",
    "QuickTime:Model": "HERO12 Black",
    "QuickTime:CreateDate": "2025:08:02 10:15:00",
    "Doc1:SampleTime": 0.0,
    "Doc1:GPSLatitude": 45.7640,
    "Doc1:GPSLongitude": 4.8357,
    "Doc1:GPSAltitude": 12.0,
    "Doc1:GPSSpeed": 18.0,
    "Doc1:GPSDateTime": "2025:08:02 08:15:00.100Z",
    "Doc2:SampleTime": 1.0,
    "Doc2:GPSLatitude": 45.7641,
    "Doc2:GPSLongitude": 4.8359,
    "QuickTime:ColorMode": "GoPro Log",
}


def test_parse_exif_date() -> None:
    assert parse_exif_date("2025:07:14 18:32:10+02:00") == datetime(
        2025, 7, 14, 18, 32, 10, tzinfo=timezone(timedelta(hours=2))
    )
    assert parse_exif_date("2025:07:14 18:32:10Z") == datetime(2025, 7, 14, 18, 32, 10, tzinfo=UTC)
    naive = parse_exif_date("2025:07:14 18:32:10")
    assert naive is not None
    assert naive.tzinfo is None
    assert parse_exif_date("0000:00:00 00:00:00") is None
    assert parse_exif_date(42) is None


def test_iphone_metadata() -> None:
    meta = normalize(IPHONE)
    assert (meta.make, meta.model) == ("Apple", "iPhone 15 Pro")
    assert meta.point is not None
    assert (meta.point.latitude, meta.point.altitude_m) == (48.8584, 35.0)
    kinds = {c.source: c.kind for c in meta.dates}
    assert kinds["Keys:CreationDate"] == Kind.WITH_OFFSET
    assert kinds["QuickTime:CreateDate"] == Kind.CONTAINER_UTC


def test_windows_reencode_is_flagged() -> None:
    meta = normalize({"QuickTime:CreateDate": "2026:09:26 15:18:19",
                      "Microsoft:EncodingTime": "2024:09:22 16:22:58+02:00"})  # fmt: skip
    assert meta.reencoded_by == "Windows Media Foundation"
    assert all(c.source != "Microsoft:EncodingTime" for c in meta.dates)  # an encode date
    assert normalize(IPHONE).reencoded_by is None


def test_gopro_track_local_time_and_log_profile() -> None:
    meta = normalize(GOPRO)
    assert len(meta.track) == 2
    assert meta.track[0].speed_mps == pytest.approx(5.0)
    assert meta.track[0].utc is not None
    kinds = {c.source: c.kind for c in meta.dates}
    assert kinds["QuickTime:CreateDate"] == Kind.CAMERA_LOCAL  # GoPro writes local time
    assert kinds["track:GPSDateTime"] == Kind.GPS_UTC
    assert meta.color_profile == "protune-flat"
    assert meta.point is None  # the stage falls back to the first track sample


DJI_SRT = """1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2025-06-21 19:42:10.123
[iso: 100] [shutter: 1/500.0] [fnum: 2.8] [ev: 0] [ct: 5600] [color_md: default] [focal_len: 24.00] [latitude: 45.764042] [longitude: 4.835659] [rel_alt: 50.200 abs_alt: 230.100] </font>

2
00:00:01,000 --> 00:00:01,033
<font size="28">FrameCnt: 31, DiffTime: 33ms
2025-06-21 19:42:11.123
[iso: 100] [shutter: 1/500.0] [fnum: 2.8] [ev: 0] [ct: 5600] [latitude: 45.764100] [longitude: 4.835700] [rel_alt: 51.000 abs_alt: 231.000] </font>
"""

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="-33.8688" lon="151.2093"><ele>12.5</ele><time>2025-02-01T06:30:00Z</time></trkpt>
    <trkpt lat="-33.8690" lon="151.2095"><ele>13.0</ele><time>2025-02-01T06:30:05Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""


def test_dji_srt(tmp_path: Path) -> None:
    video = tmp_path / "DJI_0001.MP4"
    video.write_bytes(b"")
    srt = tmp_path / "DJI_0001.SRT"
    srt.write_text(DJI_SRT, encoding="utf-8")
    assert find_sidecars(video) == {".srt": srt}
    samples, settings = parse_dji_srt(srt)
    assert len(samples) == 2
    assert samples[0].point.latitude == pytest.approx(45.764042)
    assert samples[0].point.altitude_m == pytest.approx(230.1)
    assert samples[1].t_s == pytest.approx(1.0)
    assert samples[0].utc == datetime(2025, 6, 21, 19, 42, 10)  # naive local time
    assert settings["ct"] == 5600
    assert settings["shutter"] == pytest.approx(1 / 500)


def test_regular_subtitles_are_not_telemetry(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    srt = tmp_path / "clip.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour à tous\n", encoding="utf-8")
    assert parse_dji_srt(srt) == ([], {})
    # Nor a sidecar: the subtitles the application writes there leave the analysis alone.
    assert find_sidecars(video) == {}
    (tmp_path / "clip.gpx").write_text(GPX, encoding="utf-8")
    assert find_sidecars(video) == {".gpx": tmp_path / "clip.gpx"}


def test_gpx(tmp_path: Path) -> None:
    gpx = tmp_path / "track.gpx"
    gpx.write_text(GPX, encoding="utf-8")
    samples = parse_gpx(gpx)
    assert len(samples) == 2
    assert samples[0].point.latitude == pytest.approx(-33.8688)
    assert samples[1].utc == datetime(2025, 2, 1, 6, 30, 5, tzinfo=UTC)


def test_sidecar_lookup_treats_the_name_literally(tmp_path: Path) -> None:
    (tmp_path / "Vacances [2024].mp4").write_bytes(b"")
    own = tmp_path / "Vacances [2024].srt"
    own.write_text(DJI_SRT, encoding="utf-8")
    (tmp_path / "Vacances 2.mp4").write_bytes(b"")
    (tmp_path / "Vacances 2.srt").write_text(DJI_SRT, encoding="utf-8")
    assert find_sidecars(tmp_path / "Vacances [2024].mp4") == {".srt": own}


def test_corrupted_telemetry_date_is_ignored(tmp_path: Path) -> None:
    srt = tmp_path / "DJI_0002.SRT"
    srt.write_text(
        DJI_SRT.replace("2025-06-21 19:42:10.123", "2025-13-45 19:42:10.123"), encoding="utf-8"
    )
    samples, _ = parse_dji_srt(srt)
    assert samples[0].utc is None
    assert samples[1].utc is not None


def test_offsets_of_a_day_or_more_are_not_dates() -> None:
    assert parse_exif_date("2025:01:01 00:00:00+25:00") is None
    assert normalize({"Keys:Comment": "2025:01:01 00:00:00+25:00"}).dates == []


def test_samsung_log_apv_is_a_log_profile() -> None:
    """Galaxy S26 Ultra APV rush filmed in Samsung Log (the stream signals no log curve)."""
    meta = normalize(
        {
            "Samsung:SamsungModel": "SM-S948B",
            "Keys:AndroidVersion": "16",
            "Keys:SamsungAndroidLogvideo": "track_ID=1 , version=V1.0 , gamut=BT2020",
            "Keys:SamsungAndroidApv": "track_ID=1, profile=422-10, family=APV 422 LQ",
            "Track1:TransferCharacteristics": "Unspecified",
        }
    )
    assert meta.color_profile == "samsung-log"
