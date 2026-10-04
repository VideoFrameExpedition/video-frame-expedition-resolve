"""Viewing copy for formats the browser cannot play (APV, ProRes, PCM sound…)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vfe_vision.adapters.ffmpeg.tools import LOG_TO_DISPLAY, Ffmpeg
from vfe_vision.domain.media import playback_issue


def test_what_the_browser_plays() -> None:
    assert playback_issue("h264", "yuv420p", "aac") is None
    assert playback_issue("hevc", "yuv420p10le", "aac") is None  # S26 Ultra HDR10+
    assert playback_issue("h264", "yuv420p", None) is None  # no sound
    assert "APV" in (playback_issue("apv", "yuv422p10le", "pcm_s16le") or "")  # S26 Ultra APV
    assert "yuv422p10le" in (playback_issue("h264", "yuv422p10le", "aac") or "")
    assert "pcm_s16le" in (playback_issue("h264", "yuv420p", "pcm_s16le") or "")


@pytest.fixture(scope="module", params=["apv", "prores"])
def pro_clip(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Like an S26 Ultra APV rush: 4:2:2 10-bit intra codec, PCM sound, filmed in portrait."""
    if request.param == "apv":
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
        ).stdout
        if "liboapv" not in encoders:  # FFmpeg reads APV on its own, but writes it with liboapv
            pytest.skip("this FFmpeg has no APV encoder (liboapv) to make the test clip")
    codec = (
        ["-c:v", "liboapv"] if request.param == "apv" else ["-c:v", "prores_ks", "-profile:v", "2"]
    )
    base = tmp_path_factory.mktemp(request.param)
    clip = base / ("raw.mp4" if request.param == "apv" else "raw.mov")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", *codec,
         "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le", "-shortest", str(base / "flat.mov")],
        check=True,
    )  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(base / "flat.mov"),
         "-c", "copy", str(clip)],
        check=True,
    )  # fmt: skip
    return clip


def _probe(path: Path) -> dict[str, object]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,pix_fmt,width,height:format=duration", "-of", "json",
         str(path)],
        check=True, capture_output=True,
    )  # fmt: skip
    data: dict[str, object] = json.loads(out.stdout)
    return data


def test_viewing_copy_plays_in_the_browser_with_the_same_timeline(
    pro_clip: Path, tmp_path: Path
) -> None:
    probe = _probe(pro_clip)
    streams = {s["codec_type"]: s for s in probe["streams"]}  # type: ignore[attr-defined]
    issue = playback_issue(
        streams["video"]["codec_name"], streams["video"]["pix_fmt"], streams["audio"]["codec_name"]
    )
    assert issue is not None

    out = tmp_path / "proxy.mp4"
    Ffmpeg().make_proxy(pro_clip, out, long_side=160, log_profile="samsung-log")
    copy = _probe(out)
    video, audio = (next(s for s in copy["streams"] if s["codec_type"] == kind)  # type: ignore[attr-defined]
                    for kind in ("video", "audio"))  # fmt: skip
    assert (video["codec_name"], video["pix_fmt"], audio["codec_name"]) == (
        "h264",
        "yuv420p",
        "aac",
    )
    assert playback_issue(video["codec_name"], video["pix_fmt"], audio["codec_name"]) is None
    assert (video["width"], video["height"]) == (90, 160)  # portrait: the rotation is applied
    assert float(copy["format"]["duration"]) == pytest.approx(2.0, abs=0.1)  # type: ignore[index]
    assert LOG_TO_DISPLAY  # log footage is lifted for display (same filter as the keyframes)
