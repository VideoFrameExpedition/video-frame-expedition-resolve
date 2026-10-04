"""ffprobe and ffmpeg invocations."""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import structlog

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, ExternalToolError
from vfe_vision.core.procs import CREATE_NO_WINDOW, run_process
from vfe_vision.domain.media import DEFAULT_HDR_PEAK_NITS, ProbeInfo, parse_probe


def hdr_to_sdr(peak_nits: float | None = None) -> str:
    """Tone-mapping chain for PQ/HLG sources, so frames sent to the vision model look like the
    graded SDR image rather than a washed-out HDR signal. The first ``zscale`` strips the HDR side
    data, hence the explicit ``peak`` (in units of the 100-nit reference white)."""
    peak = (peak_nits or DEFAULT_HDR_PEAK_NITS) / 100
    return (
        "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
        f"tonemap=tonemap=hable:desat=0:peak={peak:g},zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    )


HDR_TO_SDR = hdr_to_sdr()

# Generic lift for flat/log footage (no per-camera LUT): enough for a vision model to judge
# colours and exposure; a user LUT per profile can replace it later.
LOG_TO_DISPLAY = "eq=contrast=1.35:brightness=-0.04:saturation=1.5:gamma=0.9"

log = structlog.get_logger(__name__)

_PTS_RE = re.compile(r"pts_time:\s*(-?[\d.]+)")
_SCENE_RE = re.compile(r"lavfi\.scene_score=([\d.]+)")


@dataclass(frozen=True, slots=True)
class CandidateFrame:
    path: Path
    t_s: float
    scene_score: float | None


class Ffmpeg:
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe") -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    # ------------------------------------------------------------------ probe
    def probe(
        self, video: Path, *, cancel: CancelToken | None = None
    ) -> tuple[dict[str, Any], ProbeInfo]:
        result = run_process(
            [
                self.ffprobe_path, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(video),
            ],
            timeout_s=120,
            cancel=cancel,
            tool_name="ffprobe",
        )  # fmt: skip
        try:
            raw: dict[str, Any] = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ExternalToolError(f"Sortie ffprobe illisible pour {video.name}") from exc
        first = parse_probe(raw)
        if first.color_transfer != "smpte2084":
            return raw, first
        # HDR10+ dynamic metadata only shows in the frames (and some phones write no container
        # side data at all): decode the first frame on the CPU to tell HDR10 from HDR10+.
        return raw, parse_probe(raw, self.first_frame_side_data(video, cancel=cancel))

    def first_frame_side_data(
        self, video: Path, *, cancel: CancelToken | None = None
    ) -> list[dict[str, Any]]:
        try:
            result = run_process(
                [
                    self.ffprobe_path, "-v", "error", "-select_streams", "v:0",
                    "-read_intervals", "%+#1", "-show_frames",
                    "-show_entries", "frame=side_data_list", "-print_format", "json", str(video),
                ],
                timeout_s=60,
                cancel=cancel,
                tool_name="ffprobe",
            )  # fmt: skip
            frames = json.loads(result.stdout.decode("utf-8", errors="replace")).get("frames", [])
        except (ExternalToolError, json.JSONDecodeError):
            return []
        return [
            side
            for frame in frames[:1]
            for side in frame.get("side_data_list", []) or []
            if isinstance(side, dict)
        ]

    def version(self) -> str | None:
        try:
            result = run_process([self.ffmpeg_path, "-version"], timeout_s=20, tool_name="ffmpeg")
        except ExternalToolError:
            return None
        first_line = result.stdout_text.splitlines()[0] if result.stdout else ""
        return first_line.removeprefix("ffmpeg version ").split(" ")[0] or None

    # ------------------------------------------------------------------ keyframe candidates
    def extract_candidates(
        self,
        video: Path,
        out_dir: Path,
        *,
        scene_threshold: float = 0.3,
        min_interval_s: float = 2.0,
        min_scene_gap_s: float = 0.5,
        long_side: int = 1280,
        hdr: bool = False,
        hdr_peak_nits: float | None = None,
        log_profile: str | None = None,
        hwaccel: str | None = None,
        report: dict[str, str] | None = None,
        cancel: CancelToken | None = None,
        timeout_s: float = 3600,
    ) -> list[CandidateFrame]:
        """One decode pass selecting a frame on a scene change or when ``min_interval_s`` elapsed.

        ffmpeg's ``select`` filter does it, on ``scene`` and ``prev_selected_t``: counted in
        seconds, not in frames, so that it stays correct on variable-frame-rate footage. With
        ``hwaccel`` the video is decoded by the GPU (decoded frames come back to the CPU, so the
        filters and the display rotation are unchanged); a GPU failure falls back to the CPU.
        ``report["decoder"]`` tells which one did the work.
        """
        select = (
            rf"isnan(prev_selected_t)"
            rf"+gte(t-prev_selected_t\,{min_interval_s})"
            rf"+gt(scene\,{scene_threshold})*gte(t-prev_selected_t\,{min_scene_gap_s})"
        )
        filters = [f"select='{select}'", "metadata=mode=print:key=lavfi.scene_score"]
        if hdr:
            filters.append(hdr_to_sdr(hdr_peak_nits))
        elif log_profile:
            filters.append(LOG_TO_DISPLAY)
        filters.append(
            f"scale=w={long_side}:h={long_side}:force_original_aspect_ratio=decrease"
            ":force_divisible_by=2"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        pattern = out_dir / "cand_%05d.jpg"
        for accel in dict.fromkeys((hwaccel, None)):  # the GPU first when asked, then the CPU
            try:
                result = run_process(
                    [
                        self.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "info",
                        *hwaccel_args(accel),
                        "-i", str(video), "-map", "0:v:0", "-vf", ",".join(filters),
                        "-fps_mode", "vfr", "-q:v", "2", "-y", str(pattern),
                    ],
                    timeout_s=timeout_s,
                    cancel=cancel,
                    tool_name="ffmpeg",
                )  # fmt: skip
            except ExternalToolError as exc:
                if accel is None:
                    raise
                log.warning("gpu decode failed, using the cpu", video=video.name, error=exc.detail)
                for partial in out_dir.glob("cand_*.jpg"):
                    partial.unlink()
                continue
            if report is not None:
                report["decoder"] = accel or "cpu"
            return _parse_candidates(result.stderr_text, out_dir)
        raise AssertionError("unreachable")  # pragma: no cover

    def extract_frame(
        self,
        video: Path,
        t_s: float,
        out: Path,
        *,
        long_side: int,
        hdr: bool = False,
        hdr_peak_nits: float | None = None,
        log_profile: str | None = None,
        cancel: CancelToken | None = None,
        timeout_s: float = 300,
    ) -> Path:
        """The frame shown at ``t_s`` (display rotation applied), normalised like the keyframes
        (HDR tone mapping, log lift). Decoded on the CPU: a handful of frames per video."""
        filters: list[str] = []
        if hdr:
            filters.append(hdr_to_sdr(hdr_peak_nits))
        elif log_profile:
            filters.append(LOG_TO_DISPLAY)
        filters.append(
            f"scale=w={long_side}:h={long_side}:force_original_aspect_ratio=decrease"
            ":force_divisible_by=2"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        run_process(
            [
                self.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
                "-ss", f"{t_s:.6f}", "-i", str(video), "-map", "0:v:0", "-frames:v", "1",
                "-vf", ",".join(filters), "-q:v", "2", "-y", str(out),
            ],
            timeout_s=timeout_s,
            cancel=cancel,
            tool_name="ffmpeg",
        )  # fmt: skip
        if not out.is_file():
            raise ExternalToolError(f"ffmpeg n'a produit aucune image à {t_s:.3f} s", tool="ffmpeg")
        return out

    # ------------------------------------------------------------------ viewing copy
    def make_proxy(
        self,
        video: Path,
        out: Path,
        *,
        long_side: int = 1920,
        crf: int = 23,
        hdr: bool = False,
        hdr_peak_nits: float | None = None,
        log_profile: str | None = None,
        duration_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> None:
        """H.264 + AAC copy for the browser, display rotation applied, colours of HDR and log
        footage brought to a normal display, timestamps kept (seeking matches the original)."""
        scale = (
            f"scale=w={long_side}:h={long_side}:force_original_aspect_ratio=decrease"
            ":force_divisible_by=2:flags=bicubic"
        )
        filters = [scale]
        if hdr:
            filters.append(hdr_to_sdr(hdr_peak_nits))
        elif log_profile:
            filters.append(LOG_TO_DISPLAY)
        filters.append("format=yuv420p")
        run_process(
            [
                self.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-i", str(video), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                "-vf", ",".join(filters), "-fps_mode", "passthrough",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-ac", "2",
                "-movflags", "+faststart", "-map_metadata", "-1", "-f", "mp4", str(out),
            ],
            timeout_s=600 + 10 * (duration_s or 600),
            cancel=cancel,
            tool_name="ffmpeg",
        )  # fmt: skip


def _parse_candidates(stderr: str, out_dir: Path) -> list[CandidateFrame]:
    """Pair ``metadata=print`` log records with the numbered output files, in order."""
    times: list[float] = []
    scores: list[float | None] = []
    for line in stderr.splitlines():
        if "Parsed_metadata" not in line:
            continue
        if (pts := _PTS_RE.search(line)) is not None:
            times.append(float(pts.group(1)))
            scores.append(None)
        elif (score := _SCENE_RE.search(line)) is not None and scores:
            scores[-1] = float(score.group(1))
    files = sorted(out_dir.glob("cand_*.jpg"))
    return [
        CandidateFrame(path=path, t_s=max(0.0, t), scene_score=s)
        for path, t, s in zip(files, times, scores, strict=False)
    ]


# ---------------------------------------------------------------------- streaming decode
@dataclass(frozen=True, slots=True)
class DecodedFrame:
    index: int
    t_s: float
    rgb: npt.NDArray[np.uint8]  # H×W×3, RGB order


def hwaccel_args(hwaccel: str | None, *, keep_on_gpu: bool = False) -> list[str]:
    """Hardware decoding. By default frames are copied back to system memory, so software
    filters (and the automatic display rotation) work unchanged; ``keep_on_gpu`` leaves them
    in video memory until an explicit ``hwdownload``."""
    if not hwaccel:
        return []
    args = ["-hwaccel", hwaccel, "-hwaccel_device", "0"]
    return [*args, "-hwaccel_output_format", hwaccel] if keep_on_gpu else args


# What NVDEC hands back for the 4:2:0 formats phones and most cameras record (same samples,
# semi-planar layout: the software conversions that follow give identical pixels).
GPU_FRAME_FORMATS = {"yuv420p": "nv12", "yuvj420p": "nv12", "yuv420p10le": "p010le"}
# What NVDEC decodes on this generation of GPU (RTX 30), in 4:2:0 only (4:2:2 needs RTX 50):
# H.264 in 8 bits only, HEVC/VP9 in 8 and 10 bits. Only codecs whose decoding is exact to the
# bit are listed (MPEG-2 and MPEG-4 Part 2 are not: their inverse DCT may differ from the CPU's).
# APV, ProRes, AV1 (libdav1d is picked before the hardware decoder) and the like stay on the CPU.
_8_BIT = frozenset({"yuv420p", "yuvj420p"})
NVDEC_FORMATS: dict[str, frozenset[str]] = {
    "h264": _8_BIT,
    "hevc": frozenset(GPU_FRAME_FORMATS),
    "vp9": frozenset(GPU_FRAME_FORMATS),
    "vp8": _8_BIT,
    "vc1": _8_BIT,
}
NVDEC_CODECS = frozenset(NVDEC_FORMATS)


def gpu_decodes(codec: str | None, pix_fmt: str | None) -> bool:
    """Whether the GPU's video decoder handles this stream (otherwise do not borrow the GPU)."""
    return (pix_fmt or "") in NVDEC_FORMATS.get(codec or "", frozenset())


def gpu_download_format(pix_fmt: str | None, rotation: int, *, flipped: bool = False) -> str | None:
    """Format to download GPU frames in when they can be thinned on the GPU first, else None.

    The automatic display rotation (and mirroring) only works on frames in system memory, so
    rotated or mirrored videos (portrait phone clips) are downloaded frame by frame as usual.
    """
    if rotation % 360 or flipped:
        return None
    return GPU_FRAME_FORMATS.get(pix_fmt or "")


def analysis_size(display_w: int, display_h: int, width: int = 256) -> tuple[int, int]:
    """Even-sized analysis resolution preserving the display aspect ratio."""
    height = max(2, round(width * display_h / display_w / 2) * 2)
    return width, height


def iter_frames(
    ffmpeg_path: str,
    video: Path,
    *,
    fps: float,
    size: tuple[int, int],
    hdr: bool = False,
    hdr_peak_nits: float | None = None,
    hwaccel: str | None = None,
    gpu_download: str | None = None,
    report: dict[str, str] | None = None,
    cancel: CancelToken | None = None,
) -> Iterator[DecodedFrame]:
    """Decode ``video`` at ``fps`` and ``size`` into RGB frames streamed through a pipe.

    ffmpeg applies the display rotation; timestamps are ``index / fps`` (fps filter output).
    With ``hwaccel`` the GPU decodes; with ``gpu_download`` too (see ``gpu_download_format``)
    frames are thinned to ``fps`` on the GPU and only those are copied back, in that format.
    The pixels are the same either way. If the GPU fails before the first frame, the CPU
    takes over (``report["decoder"]`` tells which one did the work).
    """
    width, height = size
    # Downscale first: tone mapping 4K float frames costs twice the CPU for the same 256 px image.
    tail = [f"scale={width}:{height}:flags=area"]
    if hdr:
        tail.append(hdr_to_sdr(hdr_peak_nits))

    def args(accel: str | None) -> list[str]:
        thin = accel is not None and gpu_download is not None
        filters = [f"fps={fps}", *([f"hwdownload,format={gpu_download}"] if thin else []), *tail]
        return [
            ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
            *hwaccel_args(accel, keep_on_gpu=thin),
            "-i", str(video), "-map", "0:v:0", "-vf", ",".join(filters),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ]  # fmt: skip

    produced = 0
    if hwaccel:
        if report is not None:
            report["decoder"] = hwaccel
        try:
            for frame in _stream_frames(args(hwaccel), size, fps, cancel):
                produced += 1
                yield frame
        except ExternalToolError as exc:
            log.warning(
                "gpu decode failed, using the cpu",
                video=video.name,
                error=exc.detail,
                frames_done=produced,
            )
        else:
            return
    if report is not None:
        report["decoder"] = f"{hwaccel}+cpu" if produced else "cpu"
    # Frames are numbered by the fps filter: the CPU resumes exactly after the GPU's last one.
    for frame in _stream_frames(args(None), size, fps, cancel):
        if frame.index >= produced:
            yield frame


def _stream_frames(
    args: list[str], size: tuple[int, int], fps: float, cancel: CancelToken | None
) -> Iterator[DecodedFrame]:
    ffmpeg_path = args[0]
    width, height = size
    frame_bytes = width * height * 3
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=errors,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise ExternalToolError(
                f"ffmpeg est introuvable : {ffmpeg_path}", tool="ffmpeg"
            ) from exc
        if proc.stdout is None:  # pragma: no cover - PIPE requested
            raise ExternalToolError("ffmpeg : sortie indisponible", tool="ffmpeg")
        index = 0
        finished = False
        try:
            while True:
                if cancel is not None and cancel.cancelled:
                    raise CancelledError(cancel.reason or "Annulé")
                chunk = proc.stdout.read(frame_bytes)
                if len(chunk) < frame_bytes:
                    finished = True
                    break
                rgb = np.frombuffer(chunk, dtype=np.uint8).reshape(height, width, 3)
                yield DecodedFrame(index, index / fps, rgb)
                index += 1
        finally:
            if proc.poll() is None:
                proc.kill()
            code = proc.wait()
        if finished and code != 0:
            errors.seek(0)
            tail = errors.read()[-2000:].decode("utf-8", errors="replace").strip()
            raise ExternalToolError(
                f"Décodage ffmpeg interrompu (code {code}) : {tail or 'sans message'}",
                tool="ffmpeg",
            )


# ---------------------------------------------------------------------- audio levels
# ebur128 prints "-inf" for a quiet window and "nan" for pure digital silence (ffmpeg 9).
_EBU_LEVEL = r"(-?(?:inf|nan|[\d.]+))"
_EBU_MOMENTARY = re.compile(rf"t:\s*([\d.]+)\s+TARGET:.*?M:\s*{_EBU_LEVEL}")
_EBU_INTEGRATED = re.compile(r"I:\s*(-?[\d.]+)\s*LUFS")
_EBU_LRA = re.compile(r"LRA:\s*(-?[\d.]+)\s*LU")
_EBU_PEAK = re.compile(rf"Peak:\s*{_EBU_LEVEL}\s*dBFS")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
SILENT_LUFS = -120.0


@dataclass(frozen=True, slots=True)
class AudioLevels:
    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbfs: float | None
    momentary: list[tuple[float, float]]  # (t, LUFS) every 100 ms
    silences: list[tuple[float, float]]


def measure_audio(
    ffmpeg_path: str,
    video: Path,
    *,
    duration_s: float,
    silence_db: float = -45.0,
    silence_min_s: float = 0.8,
    cancel: CancelToken | None = None,
) -> AudioLevels:
    """EBU R128 loudness (integrated, range, true peak, momentary curve) and silences."""
    audio_filter = (
        f"ebur128=peak=true:framelog=verbose,silencedetect=n={silence_db}dB:d={silence_min_s}"
    )
    result = run_process(
        [
            ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "verbose", "-i", str(video),
            "-map", "0:a:0", "-vn", "-af", audio_filter, "-f", "null", "-",
        ],
        timeout_s=max(600.0, duration_s * 2),
        cancel=cancel,
        tool_name="ffmpeg",
    )  # fmt: skip
    return parse_audio_levels(result.stderr_text, duration_s)


def _finite(text: str) -> float | None:
    value = float(text)
    return value if math.isfinite(value) else None


def parse_audio_levels(log: str, duration_s: float) -> AudioLevels:
    momentary: list[tuple[float, float]] = []
    for match in _EBU_MOMENTARY.finditer(log):
        value = _finite(match.group(2))
        momentary.append((float(match.group(1)), SILENT_LUFS if value is None else value))
    summary = log[log.rfind("Summary:") :] if "Summary:" in log else ""
    integrated = _EBU_INTEGRATED.search(summary)
    lra = _EBU_LRA.search(summary)
    peak = _EBU_PEAK.search(summary)
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in log.splitlines():
        if (m := _SILENCE_START.search(line)) is not None:
            start = max(0.0, float(m.group(1)))
        elif (m := _SILENCE_END.search(line)) is not None and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:
        silences.append((start, duration_s))
    return AudioLevels(
        integrated_lufs=float(integrated.group(1)) if integrated else None,
        loudness_range_lu=float(lra.group(1)) if lra else None,
        true_peak_dbfs=_finite(peak.group(1)) if peak else None,
        momentary=momentary,
        silences=silences,
    )
