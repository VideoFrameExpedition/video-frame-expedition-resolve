"""Stage ``probe``: container and stream facts from ffprobe."""

from __future__ import annotations

from vfe_vision.db.models import Video, VideoMetadata
from vfe_vision.domain.enums import StageStatus
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage


class ProbeStage(SyncStage):
    name = "probe"
    version = 2
    family = StageFamily.FILE

    def run(self, ctx: StageContext) -> StageOutcome:
        raw, info = ctx.tools.ffmpeg.probe(ctx.video.path, cancel=ctx.cancel)
        with ctx.tools.db.write() as session:
            video = session.get_one(Video, ctx.video.id)
            video.duration_s = info.duration_s
            video.width = info.width
            video.height = info.height
            video.fps = info.fps
            video.video_codec = info.video_codec
            video.audio_codec = info.audio_codec
            video.has_audio = info.has_audio
            video.orientation = info.orientation
            video.is_hdr = info.is_hdr
            video.hdr_format = info.hdr_format
            video.hdr_peak_nits = info.hdr_peak_nits
            video.is_vfr = info.is_vfr
            video.capture_fps = info.capture_fps
            video.color_transfer = info.color_transfer
            video.start_timecode = info.start_timecode
            video.encoder = info.encoder
            meta = session.get(VideoMetadata, ctx.video.id) or VideoMetadata(video_id=ctx.video.id)
            meta.probe = raw
            meta.normalized = {**(meta.normalized or {}), "probe": info.model_dump(mode="json")}
            session.add(meta)
        ctx.video.duration_s = info.duration_s
        if info.width is None:
            return StageOutcome(
                StageStatus.SUCCEEDED,
                {"warning": "aucune piste vidéo"},
            )
        return StageOutcome.ok(
            duration_s=info.duration_s,
            resolution=f"{info.width}x{info.height}",
            codec=info.video_codec,
            hdr=info.hdr_format or info.is_hdr,
            vfr=info.is_vfr,
            exported_by_editor=info.exported_by_editor,
        )
