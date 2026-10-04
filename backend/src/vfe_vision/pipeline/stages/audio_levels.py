"""Stage ``audio_levels``: EBU R128 loudness, loudness curve and silences."""

from __future__ import annotations

import numpy as np

from vfe_vision.adapters.ffmpeg.tools import measure_audio
from vfe_vision.db.models import AudioStats, Video, VideoSignals
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage

CURVE_HZ = 2.0


class AudioLevelsStage(SyncStage):
    name = "audio_levels"
    version = 1
    family = StageFamily.SOUND
    requires = ("probe",)
    optional = True

    def run(self, ctx: StageContext) -> StageOutcome:
        with ctx.tools.db.read() as session:
            video = session.get_one(Video, ctx.video.id)
            has_audio, duration = bool(video.has_audio), video.duration_s or 0.0
        if not has_audio:
            return StageOutcome.skipped("Pas de piste audio", permanent=True)
        levels = measure_audio(
            ctx.tools.ffmpeg.ffmpeg_path, ctx.video.path, duration_s=duration, cancel=ctx.cancel
        )
        silent = sum(end - start for start, end in levels.silences)
        curve = _curve(levels.momentary)
        with ctx.tools.db.write() as session:
            stats = session.get(AudioStats, ctx.video.id) or AudioStats(video_id=ctx.video.id)
            stats.integrated_lufs = levels.integrated_lufs
            stats.loudness_range_lu = levels.loudness_range_lu
            stats.true_peak_dbfs = levels.true_peak_dbfs
            stats.silence_ratio = round(silent / duration, 4) if duration else None
            stats.silences = [[round(a, 2), round(b, 2)] for a, b in levels.silences]
            session.add(stats)
            signals = session.get(VideoSignals, ctx.video.id) or VideoSignals(video_id=ctx.video.id)
            signals.audio = {"schema_version": 1, "hz": CURVE_HZ, **curve}
            session.add(signals)
        return StageOutcome.ok(
            integrated_lufs=levels.integrated_lufs,
            true_peak_dbfs=levels.true_peak_dbfs,
            silences=len(levels.silences),
        )


def _curve(momentary: list[tuple[float, float]]) -> dict[str, list[float]]:
    """Momentary loudness (100 ms) averaged in energy into 0.5 s buckets."""
    buckets: dict[int, list[float]] = {}
    for t, lufs in momentary:
        buckets.setdefault(int(t * CURVE_HZ), []).append(lufs)
    t_out, lufs_out = [], []
    for key in sorted(buckets):
        energy = np.mean([10 ** (v / 10) for v in buckets[key]])
        t_out.append(round(key / CURVE_HZ, 2))
        lufs_out.append(round(float(10 * np.log10(max(energy, 1e-12))), 1))
    return {"t": t_out, "lufs": lufs_out}
