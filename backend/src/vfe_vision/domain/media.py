"""Technical description of a video file, derived from ffprobe output (pure functions)."""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

from pydantic import BaseModel, ConfigDict

from vfe_vision.domain.capture_time import parse_utc_offset
from vfe_vision.domain.enums import Orientation

# ffprobe `color_transfer` values that denote HDR signals.
HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})

# Encoders / software tags written by editing or transcoding tools: the container date is then
# the export date, not the capture date.
EDITING_SOFTWARE_MARKERS = (
    "davinci resolve", "blackmagic", "premiere", "final cut", "capcut", "imovie", "handbrake",
    "shotcut", "kdenlive", "vegas", "filmora", "clipchamp", "lumafusion", "vn video",
)  # fmt: skip

SQUARE_TOLERANCE = 0.03


class ProbeInfo(BaseModel):
    """Normalised facts extracted from ``ffprobe -show_format -show_streams``."""

    model_config = ConfigDict(frozen=True)

    duration_s: float | None
    width: int | None  # display width (rotation applied)
    height: int | None
    rotation: int
    flipped: bool = False  # the display matrix mirrors the picture
    fps: float | None
    video_codec: str | None
    pix_fmt: str | None
    bit_depth: int | None
    color_transfer: str | None
    color_primaries: str | None
    is_hdr: bool
    has_dolby_vision: bool
    hdr_format: str | None = None  # "HDR10", "HDR10+", "HLG", "Dolby Vision"
    hdr_peak_nits: float | None = None  # tone-mapping peak (MaxCLL, else mastering max)
    nominal_fps: float | None = None  # r_frame_rate (what the camera was set to)
    avg_fps: float | None = None  # frames / duration
    is_vfr: bool = False
    android_version: str | None = None  # com.android.version (AOSP MPEG4Writer files)
    capture_fps: float | None = None  # com.android.capture.fps (slow motion when > fps)
    utc_offset_min: int | None = None  # com.samsung.android.utc_offset
    location: str | None = None  # ISO 6709 "location" tag (Android, Apple, ffmpeg)
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None
    has_audio: bool
    subtitle_streams: int
    start_timecode: str | None
    encoder: str | None
    creation_time: str | None
    container: str | None
    bit_rate: int | None

    @property
    def orientation(self) -> Orientation | None:
        return orientation_of(self.width, self.height)

    @property
    def exported_by_editor(self) -> bool:
        return is_editing_software(self.encoder)


def orientation_of(width: int | None, height: int | None) -> Orientation | None:
    if not width or not height:
        return None
    ratio = width / height
    if abs(ratio - 1.0) <= SQUARE_TOLERANCE:
        return Orientation.SQUARE
    return Orientation.HORIZONTAL if ratio > 1 else Orientation.VERTICAL


def is_editing_software(tag: str | None) -> bool:
    if not tag:
        return False
    lowered = tag.lower()
    return any(marker in lowered for marker in EDITING_SOFTWARE_MARKERS)


def _rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "0"}:
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _is_vfr(nominal: float | None, average: float | None, *, android: bool) -> bool:
    """Variable frame rate from the container figures (no frame scan).

    A constant-rate file whose last frame is shorter drifts by a few hundredths of a percent, and
    interlaced files report the field rate (r = 2 × avg): neither is VFR. Android camera apps
    write VFR (the average then differs from the nominal rate by any amount).
    """
    if not nominal or not average:
        return False
    ratio = average / nominal
    if android:  # phones never report a field rate
        return abs(ratio - 1) > 1e-6
    if abs(ratio - 0.5) < 0.002:
        return False
    return abs(ratio - 1) > 0.002


def _fraction(value: Any) -> Fraction | None:
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def _str_tag(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) else result


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            return round(float(side["rotation"])) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    return int(tag) % 360 if tag and str(tag).lstrip("-").isdigit() else 0


def _flipped(stream: dict[str, Any]) -> bool:
    """Whether the display matrix mirrors the picture (negative determinant): ffprobe reports
    such a matrix as a mere rotation, 0° for a vertical flip."""
    for side in stream.get("side_data_list", []) or []:
        rows = [
            line.split(":", 1)[1].split()
            for line in str(side.get("displaymatrix") or "").splitlines()
            if ":" in line
        ]
        try:
            a, b = float(rows[0][0]), float(rows[0][1])
            c, d = float(rows[1][0]), float(rows[1][1])
        except (IndexError, ValueError):
            continue
        return a * d - b * c < 0
    return False


def _display_fps(nominal: float | None, average: float | None) -> float | None:
    """The nominal rate when the average only drifts (variable frame rate phones), else the
    average (the nominal rate of some containers is a timebase artefact)."""
    if nominal and average:
        return nominal if abs(average / nominal - 1) < 0.01 else average
    return average or nominal


DEFAULT_HDR_PEAK_NITS = 1000.0  # PQ grading default when the file states nothing usable


def _side_kind(entry: dict[str, Any]) -> str:
    return str(entry.get("side_data_type", ""))


def hdr_format_of(
    transfer: str | None, dolby_vision: bool, side_data: list[dict[str, Any]]
) -> str | None:
    """``side_data``: stream and first-frame side data (HDR10+ only shows in the frames)."""
    if dolby_vision:
        return "Dolby Vision"
    if transfer == "arib-std-b67":
        return "HLG"
    if transfer == "smpte2084":
        if any("2094-40" in _side_kind(s) or "hdr10+" in _side_kind(s).lower() for s in side_data):
            return "HDR10+"
        return "HDR10"
    return None


def hdr_peak_of(transfer: str | None, side_data: list[dict[str, Any]]) -> float | None:
    """Peak luminance (nits) for the tone mapper: MaxCLL, else the mastering maximum, else the
    PQ default. ffmpeg's first ``zscale`` drops this side data, so it must be passed explicitly.
    (Samsung writes a wrong *minimum* in its container mdcv; the maximum is sound.)"""
    if transfer not in HDR_TRANSFERS:
        return None
    if transfer == "arib-std-b67":
        return DEFAULT_HDR_PEAK_NITS
    for entry in side_data:
        if "content light" in _side_kind(entry).lower():
            content = _float(entry.get("max_content"))
            if content and content > 0:
                return min(10000.0, max(100.0, content))
    for entry in side_data:
        if "mastering display" in _side_kind(entry).lower():
            try:
                peak = float(Fraction(str(entry.get("max_luminance"))))
            except (ValueError, ZeroDivisionError):
                continue
            if peak > 0:
                return min(10000.0, max(100.0, peak))
    return DEFAULT_HDR_PEAK_NITS


def parse_probe(
    data: dict[str, Any], frame_side_data: list[dict[str, Any]] | None = None
) -> ProbeInfo:
    """Build a :class:`ProbeInfo` from ffprobe's JSON output."""
    streams: list[dict[str, Any]] = data.get("streams", []) or []
    fmt: dict[str, Any] = data.get("format", {}) or {}
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    format_tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}

    width = height = None
    rotation = 0
    flipped = False
    nominal = average = None
    vfr = False
    pix_fmt = transfer = primaries = codec = None
    bit_depth = None
    dolby_vision = False
    stream_tags: dict[str, Any] = {}
    if video is not None:
        rotation = _rotation(video)
        flipped = _flipped(video)
        raw_w, raw_h = _int(video.get("width")), _int(video.get("height"))
        width, height = (raw_h, raw_w) if rotation in {90, 270} else (raw_w, raw_h)
        nominal, average = _rate(video.get("r_frame_rate")), _rate(video.get("avg_frame_rate"))
        vfr = _is_vfr(nominal, average, android=bool(format_tags.get("com.android.version")))
        codec = video.get("codec_name")
        pix_fmt = video.get("pix_fmt")
        transfer = video.get("color_transfer")
        primaries = video.get("color_primaries")
        bit_depth = _int(video.get("bits_per_raw_sample"))
        if bit_depth is None and pix_fmt:
            bit_depth = 10 if "10" in pix_fmt else 12 if "12" in pix_fmt else 8
        dolby_vision = any(
            "dovi" in str(side.get("side_data_type", "")).lower()
            or "dolby vision" in str(side.get("side_data_type", "")).lower()
            for side in video.get("side_data_list", []) or []
        )
        stream_tags = {k.lower(): v for k, v in (video.get("tags") or {}).items()}

    timecode = stream_tags.get("timecode") or format_tags.get("timecode")
    if timecode is None:
        for stream in streams:
            tag = (stream.get("tags") or {}).get("timecode")
            if tag:
                timecode = tag
                break

    duration = _float(fmt.get("duration")) or (_float(video.get("duration")) if video else None)
    android = bool(format_tags.get("com.android.version"))
    # Android camera apps: the nominal rate is the setting; low-light frame drops only lower the
    # average (and must not look like slow motion against com.android.capture.fps).
    fps = nominal if android and nominal else _display_fps(nominal, average)
    stream_side = (video.get("side_data_list", []) or []) if video else []
    side_data = [*stream_side, *(frame_side_data or [])]
    return ProbeInfo(
        duration_s=duration,
        width=width,
        height=height,
        rotation=rotation,
        flipped=flipped,
        fps=round(fps, 3) if fps else None,
        nominal_fps=round(nominal, 3) if nominal else None,
        avg_fps=round(average, 3) if average else None,
        is_vfr=vfr,
        hdr_format=hdr_format_of(transfer, dolby_vision, side_data),
        hdr_peak_nits=hdr_peak_of(transfer, side_data),
        android_version=_str_tag(format_tags.get("com.android.version")),
        capture_fps=_float(format_tags.get("com.android.capture.fps")),
        utc_offset_min=parse_utc_offset(format_tags.get("com.samsung.android.utc_offset")),
        location=_str_tag(format_tags.get("location")),
        video_codec=codec,
        pix_fmt=pix_fmt,
        bit_depth=bit_depth,
        color_transfer=transfer,
        color_primaries=primaries,
        is_hdr=(transfer in HDR_TRANSFERS) or dolby_vision,
        has_dolby_vision=dolby_vision,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_channels=_int(audio.get("channels")) if audio else None,
        audio_sample_rate=_int(audio.get("sample_rate")) if audio else None,
        has_audio=audio is not None,
        subtitle_streams=sum(1 for s in streams if s.get("codec_type") == "subtitle"),
        start_timecode=timecode,
        encoder=format_tags.get("encoder") or stream_tags.get("encoder"),
        creation_time=format_tags.get("creation_time"),
        container=fmt.get("format_name"),
        bit_rate=_int(fmt.get("bit_rate")),
    )


# What the browsers play in <video>: codec -> pixel formats. HEVC: Chromium on Windows needs
# the GPU's decoder (every NVIDIA card since 2016); Safari and Chrome on a Mac decode it with the
# media engine of the chip.
WEB_VIDEO: dict[str, frozenset[str]] = {
    "h264": frozenset({"yuv420p", "yuvj420p"}),
    "hevc": frozenset({"yuv420p", "yuvj420p", "yuv420p10le"}),
    "av1": frozenset({"yuv420p", "yuv420p10le"}),
    "vp9": frozenset({"yuv420p", "yuv420p10le"}),
    "vp8": frozenset({"yuv420p"}),
}
WEB_AUDIO = frozenset({"aac", "mp3", "opus", "vorbis", "flac"})


def playback_issue(
    video_codec: str | None, pix_fmt: str | None, audio_codec: str | None
) -> str | None:
    """Why the browser cannot play the original file (None: it can)."""
    formats = WEB_VIDEO.get(video_codec or "")
    if formats is None:
        return f"Vidéo {(video_codec or 'inconnue').upper()} non lisible par le navigateur"
    if pix_fmt is not None and pix_fmt not in formats:
        return f"Vidéo {video_codec} {pix_fmt} non lisible par le navigateur"
    if audio_codec is not None and audio_codec not in WEB_AUDIO:
        return f"Son {audio_codec} non lisible par le navigateur"
    return None
