"""Stage ``analysis_pass``: one low-resolution decode → shots, camera motion, technical signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import sqlalchemy as sa

from vfe_vision.adapters.ffmpeg.tools import (
    analysis_size,
    gpu_decodes,
    gpu_download_format,
    iter_frames,
)
from vfe_vision.db.models import Shot, Video, VideoSignals
from vfe_vision.domain.color import estimate_cct
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.shots import CutParams, build_shots, classify_motion, detect_cuts
from vfe_vision.pipeline.gpu import decode_need_mib, video_stream_facts
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage

ANALYSIS_FPS = 8.0
ANALYSIS_WIDTH = 256
SIGNAL_HZ = 2.0  # time-series resolution stored for charts
BLACK_LUMA = 0.06
FROZEN_DELTA = 0.6


@dataclass(slots=True)
class _Series:
    t: list[float] = field(default_factory=list)
    delta: list[float] = field(default_factory=list)
    luma: list[float] = field(default_factory=list)
    contrast: list[float] = field(default_factory=list)
    saturation: list[float] = field(default_factory=list)
    sharpness: list[float] = field(default_factory=list)
    dx: list[float] = field(default_factory=list)
    dy: list[float] = field(default_factory=list)
    mag: list[float] = field(default_factory=list)
    div: list[float] = field(default_factory=list)
    cct: dict[float, float] = field(default_factory=dict)


def _radial_field(
    height: int, width: int
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    rx, ry = xs - width / 2, ys - height / 2
    norm = np.hypot(rx, ry) + 1e-6
    return rx / norm, ry / norm


class AnalysisPassStage(SyncStage):
    name = "analysis_pass"
    version = 2
    family = StageFamily.IMAGE
    requires = ("probe",)

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {"fps": ANALYSIS_FPS, "width": ANALYSIS_WIDTH, "cuts": CutParams().__repr__()}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        with ctx.tools.db.read() as session:
            hdr_peak = session.get_one(Video, ctx.video.id).hdr_peak_nits
        # Only HDR sources depend on the peak: SDR cache keys stay unchanged.
        return {"hdr_peak_nits": hdr_peak} if hdr_peak else {}

    def run(self, ctx: StageContext) -> StageOutcome:
        with ctx.tools.db.read() as session:
            video = session.get_one(Video, ctx.video.id)
            width, height, duration, hdr = (
                video.width,
                video.height,
                video.duration_s,
                bool(video.is_hdr),
            )
            hdr_peak = video.hdr_peak_nits
        if not width or not height or not duration:
            return StageOutcome.skipped("Pas de piste vidéo exploitable", permanent=True)
        size = analysis_size(width, height, ANALYSIS_WIDTH)
        decode: dict[str, str] = {}
        need = decode_need_mib(width, height)
        stream = video_stream_facts(ctx)
        enabled = ctx.prefs.gpu_decode and gpu_decodes(
            stream.get("video_codec"), stream.get("pix_fmt")
        )
        with ctx.tools.gpu.borrow(need, enabled=enabled) as gpu:
            series = self._measure(
                ctx,
                size,
                duration,
                hdr,
                hdr_peak,
                hwaccel="cuda" if gpu else None,
                gpu_download=gpu_download_format(
                    stream.get("pix_fmt"),
                    int(stream.get("rotation") or 0),
                    flipped=bool(stream.get("flipped")),
                )
                if gpu
                else None,
                report=decode,
            )
        if len(series.t) < 2:
            return StageOutcome.skipped("Trop peu d'images décodées", permanent=True)

        cuts = detect_cuts(series.t, series.delta)
        spans = build_shots(cuts, duration)
        luma = np.array(series.luma)
        times = np.array(series.t)
        shots: list[Shot] = []
        for idx, (start, end) in enumerate(spans):
            inside = np.where((times >= start) & (times < end))[0]
            moving = inside[1:] if len(inside) > 1 else inside  # flow at the cut itself is noise
            summary = classify_motion(
                [series.dx[i] for i in moving],
                [series.dy[i] for i in moving],
                [series.mag[i] for i in moving],
                [series.div[i] for i in moving],
            )
            before = np.where((times < start) & (times >= start - 0.5))[0]
            fade = idx > 0 and len(before) > 0 and float(luma[before].min()) < BLACK_LUMA
            cct_values = [v for t, v in series.cct.items() if start <= t < end]
            shots.append(
                Shot(
                    video_id=ctx.video.id,
                    idx=idx,
                    start_s=round(start, 3),
                    end_s=round(end, 3),
                    boundary="start" if idx == 0 else "fade" if fade else "cut",
                    motion=summary.motion.value,
                    motion_score=round(summary.score, 3),
                    stability=round(summary.stability, 3),
                    metrics=_aggregate(series, inside, cct_values),
                )
            )
        visual = _downsample(series)
        with ctx.tools.db.write() as session:
            session.execute(sa.delete(Shot).where(Shot.video_id == ctx.video.id))
            session.add_all(shots)
            signals = session.get(VideoSignals, ctx.video.id) or VideoSignals(video_id=ctx.video.id)
            signals.visual = visual
            session.add(signals)
        motions = sorted({s.motion for s in shots})
        return StageOutcome.ok(
            frames=len(series.t),
            shots=len(shots),
            motions=motions,
            decoder=decode.get("decoder", "cpu"),
        )

    def _measure(
        self,
        ctx: StageContext,
        size: tuple[int, int],
        duration: float,
        hdr: bool,
        hdr_peak: float | None,
        *,
        hwaccel: str | None,
        gpu_download: str | None,
        report: dict[str, str],
    ) -> _Series:
        width, height = size
        rx, ry = _radial_field(height, width)
        series = _Series()
        prev_hsv: npt.NDArray[np.uint8] | None = None
        prev_gray: npt.NDArray[np.uint8] | None = None
        expected = max(1, int(duration * ANALYSIS_FPS))
        cv2.setNumThreads(2)
        for frame in iter_frames(
            ctx.tools.ffmpeg.ffmpeg_path, ctx.video.path, fps=ANALYSIS_FPS, size=size, hdr=hdr,
            hdr_peak_nits=hdr_peak,
            hwaccel=hwaccel,
            gpu_download=gpu_download,
            report=report,
            cancel=ctx.cancel,
        ):  # fmt: skip
            rgb = frame.rgb
            hsv = np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV), dtype=np.uint8)
            gray = np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
            luma = gray.astype(np.float64) / 255.0
            series.t.append(frame.t_s)
            series.luma.append(float(luma.mean()))
            series.contrast.append(float(luma.std()))
            series.saturation.append(float(hsv[..., 1].mean() / 255.0))
            series.sharpness.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            if prev_hsv is None or prev_gray is None:
                series.delta.append(0.0)
                series.dx.append(0.0)
                series.dy.append(0.0)
                series.mag.append(0.0)
                series.div.append(0.0)
            else:
                diff = np.abs(hsv.astype(np.int16) - prev_hsv.astype(np.int16))
                series.delta.append(float(diff.mean()))
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray,
                    gray,
                    None,
                    0.5,
                    3,
                    15,
                    3,
                    5,
                    1.2,
                    0,  # type: ignore[call-overload]
                )
                fx, fy = flow[..., 0], flow[..., 1]
                series.dx.append(float(np.median(fx)))
                series.dy.append(float(np.median(fy)))
                series.mag.append(float(np.median(np.hypot(fx, fy))))
                series.div.append(float(np.mean(fx * rx + fy * ry)))
            if frame.index % int(ANALYSIS_FPS) == 0:  # colour temperature once per second
                cct = estimate_cct(np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), dtype=np.uint8))
                if cct is not None:
                    series.cct[frame.t_s] = round(cct)
            prev_hsv, prev_gray = hsv, gray
            if frame.index % 16 == 0:
                ctx.progress(min(0.99, frame.index / expected), None)
        return series


def _aggregate(series: _Series, inside: npt.NDArray[np.intp], cct: list[float]) -> dict[str, Any]:
    def mean(values: list[float]) -> float | None:
        picked = [values[i] for i in inside]
        return round(float(np.mean(picked)), 4) if picked else None

    luma = [series.luma[i] for i in inside]
    return {
        "luma": mean(series.luma),
        "contrast": mean(series.contrast),
        "saturation": mean(series.saturation),
        "sharpness": mean(series.sharpness),
        "black_ratio": round(float(np.mean([v < BLACK_LUMA for v in luma])), 3) if luma else 0.0,
        "frozen_ratio": round(
            float(np.mean([series.delta[i] < FROZEN_DELTA for i in inside[1:]])), 3
        ) if len(inside) > 1 else 0.0,
        "cct_k": round(float(np.median(cct))) if cct else None,
    }  # fmt: skip


def _downsample(series: _Series) -> dict[str, Any]:
    """Average the per-frame series into SIGNAL_HZ buckets for the timeline and charts."""
    step = int(ANALYSIS_FPS / SIGNAL_HZ)
    out: dict[str, list[float]] = {"t": [], "luma": [], "contrast": [], "saturation": [],
                                   "sharpness": [], "motion": []}  # fmt: skip
    for start in range(0, len(series.t), step):
        block = slice(start, start + step)
        out["t"].append(round(series.t[start], 2))
        for key, values in (("luma", series.luma), ("contrast", series.contrast),
                            ("saturation", series.saturation), ("sharpness", series.sharpness),
                            ("motion", series.mag)):  # fmt: skip
            out[key].append(round(float(np.mean(values[block])), 4))
    cct_t = sorted(series.cct)
    return {
        "schema_version": 1,
        "hz": SIGNAL_HZ,
        **out,
        "cct": {"t": [round(t, 2) for t in cct_t], "k": [series.cct[t] for t in cct_t]},
    }
