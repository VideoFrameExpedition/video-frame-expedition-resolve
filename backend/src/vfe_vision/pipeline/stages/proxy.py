"""Stage ``proxy``: a viewing copy for videos the browser cannot play (APV, ProRes, PCM sound…).

H.264 1080p with AAC sound, colours of log and HDR footage brought to a normal display, same
timeline as the original (seeking and keyframe times match). The original is never touched:
the copy lives with the other artefacts.
"""

from __future__ import annotations

import shutil
from typing import Any

from vfe_vision.db.models import Video
from vfe_vision.domain.media import playback_issue
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.gpu import video_stream_facts
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage
from vfe_vision.pipeline.stages.keyframes import LOG_PROFILES

PROXY_DIR = "proxy"
PROXY_FILE = "proxy.mp4"
PROXY_LONG_SIDE = 1920
PROXY_CRF = 23


class ProxyStage(SyncStage):
    name = "proxy"
    version = 1
    family = StageFamily.FILE
    requires = ("probe",)
    after = ("metadata",)  # the colour profile (log footage) changes the copy's colours
    optional = True
    rebuilt_after_import = True  # the copy is made again after an analysis file is imported

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {"long_side": PROXY_LONG_SIDE, "crf": PROXY_CRF, "video": "h264", "audio": "aac"}

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        stream = video_stream_facts(ctx)
        with ctx.tools.db.read() as session:
            video = session.get_one(Video, ctx.video.id)
            audio, hdr, peak = video.audio_codec, bool(video.is_hdr), video.hdr_peak_nits
            profile = video.color_profile
        return {
            "codec": stream.get("video_codec"),
            "pix_fmt": stream.get("pix_fmt"),
            "audio": audio,
            "hdr": hdr,
            "hdr_peak_nits": peak if hdr else None,
            "log_profile": profile if profile in LOG_PROFILES else None,
        }

    def run(self, ctx: StageContext) -> StageOutcome:
        facts = self.input_facts(ctx)
        directory = ctx.tools.artifacts.video_dir(ctx.video.id) / PROXY_DIR
        issue = playback_issue(facts["codec"], facts["pix_fmt"], facts["audio"])
        if issue is None:
            shutil.rmtree(directory, ignore_errors=True)
            return StageOutcome.skipped("Lisible directement par le navigateur", permanent=True)
        ctx.progress(0.05, "Copie de visionnage (H.264)")
        directory = ctx.tools.artifacts.subdir(ctx.video.id, PROXY_DIR)
        part = directory / "proxy.part.mp4"
        ctx.tools.ffmpeg.make_proxy(
            ctx.video.path,
            part,
            long_side=PROXY_LONG_SIDE,
            crf=PROXY_CRF,
            hdr=facts["hdr"],
            hdr_peak_nits=facts["hdr_peak_nits"],
            log_profile=facts["log_profile"],
            duration_s=ctx.video.duration_s,
            cancel=ctx.cancel,
        )
        target = directory / PROXY_FILE
        part.replace(target)
        return StageOutcome.ok(reason=issue, size_mb=round(target.stat().st_size / 1_048_576, 1))
