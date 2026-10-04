"""Stage ``keyframes``: scene-change + time-floor selection, de-duplication, thumbnails."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from vfe_vision.adapters.ffmpeg.tools import CandidateFrame, Ffmpeg, gpu_decodes
from vfe_vision.adapters.imaging import (
    Image,
    frame_dhash,
    read_image,
    resize_long_side,
    sharpness,
    write_jpeg,
)
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.ids import new_id
from vfe_vision.db.models import Keyframe, ShotStory, Video
from vfe_vision.domain.dedup import DuplicateFilter
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.pipeline.gpu import decode_need_mib, video_stream_facts
from vfe_vision.pipeline.stage import StageContext, StageFamily, StageOutcome, SyncStage
from vfe_vision.storage.artifacts import ArtifactStore

KEYFRAME_LONG_SIDE = 1280
THUMB_LONG_SIDE = 320
POSTER_POSITION = 0.10  # poster frame ~10 % in (frame 0 is often black or a fade-in)
# Flat/log profiles: frames are normalised before description so colours are judged sensibly.
LOG_PROFILES = frozenset({"d-log", "d-log-m", "s-log2", "s-log3", "v-log", "c-log", "f-log",
                          "n-log", "apple-log", "samsung-log", "protune-flat", "flat"})  # fmt: skip
SHOT_FRAMES_DIR = "shot_frames"  # frames extracted for the shot stories (vision_shots)
# A stored keyframe time is rounded to the millisecond: extracting it again seeks this much
# earlier, so the first frame at or after the seek is the very frame that was kept.
SEEK_SLACK_S = 0.0005


@dataclass(frozen=True, slots=True)
class FrameLook:
    """How the frames of a video are brought to a normal display: HDR tone mapping (to the
    source's peak) or the lift of flat/log profiles. The same for every extracted image."""

    hdr: bool = False
    hdr_peak_nits: float | None = None
    log_profile: str | None = None

    @classmethod
    def of(
        cls, is_hdr: bool | None, hdr_peak_nits: float | None, color_profile: str | None
    ) -> FrameLook:
        profile = color_profile if color_profile in LOG_PROFILES else None
        return cls(bool(is_hdr), hdr_peak_nits, profile)

    @classmethod
    def of_video(cls, video: Video) -> FrameLook:
        return cls.of(video.is_hdr, video.hdr_peak_nits, video.color_profile)


class KeyframeFiles:
    """Keyframe images and thumbnails of one extraction, written to fresh generation folders:
    the previous ones are removed only once the database points at the new files, so a cancel
    or a crash never mixes old rows and new images."""

    def __init__(self, store: ArtifactStore, video_id: str) -> None:
        self.store = store
        self.video_id = video_id
        self.generation = new_id()
        self.frames_dir = store.subdir(video_id, f"keyframes/{self.generation}", reset=True)
        self.thumbs_dir = store.subdir(video_id, f"thumbs/{self.generation}", reset=True)

    def write(self, idx: int, image: Image) -> tuple[str, str]:
        """Store the keyframe ``idx`` and its thumbnail; their artifact paths."""
        image_path = self.frames_dir / f"kf_{idx:04d}.jpg"
        thumb_path = self.thumbs_dir / f"kf_{idx:04d}.jpg"
        write_jpeg(image_path, image, quality=88)
        write_jpeg(thumb_path, resize_long_side(image, THUMB_LONG_SIDE), quality=80)
        return self.store.rel(image_path), self.store.rel(thumb_path)

    def keep(self) -> None:
        """The database points at these files: earlier generations go."""
        self.store.prune(self.video_id, "keyframes", keep=self.generation)
        self.store.prune(self.video_id, "thumbs", keep=self.generation)

    def discard(self) -> None:
        """Abandoned (cancelled, failed): these files go, the earlier ones stay."""
        shutil.rmtree(self.frames_dir, ignore_errors=True)
        shutil.rmtree(self.thumbs_dir, ignore_errors=True)


def pick_poster(rows: Sequence[Keyframe], duration_s: float | None) -> Keyframe | None:
    """The keyframe shown on the library card: the nearest to 10 % of the video."""
    target = (duration_s or 0.0) * POSTER_POSITION
    return min(rows, key=lambda r: abs(r.t_s - target), default=None)


def keyframe_at(
    ffmpeg: Ffmpeg,
    video: Path,
    t_s: float,
    look: FrameLook,
    scratch: Path,
    *,
    cancel: CancelToken | None = None,
) -> Image:
    """The keyframe shown at ``t_s`` as this stage extracts it (display orientation, the same
    normalisation, size and encoding), e.g. to rebuild the images of an imported analysis."""
    source = scratch / f"{t_s:010.3f}.src.jpg"
    try:
        ffmpeg.extract_frame(
            video, max(0.0, t_s - SEEK_SLACK_S), source, long_side=KEYFRAME_LONG_SIDE,
            hdr=look.hdr, hdr_peak_nits=look.hdr_peak_nits, log_profile=look.log_profile,
            cancel=cancel,
        )  # fmt: skip
        return read_image(source)
    finally:
        source.unlink(missing_ok=True)


@dataclass(slots=True)
class _Kept:
    candidate: CandidateFrame  # the image is re-read when persisted: memory stays bounded
    hash: int
    reason: str


class KeyframesStage(SyncStage):
    name = "keyframes"
    version = 1
    family = StageFamily.IMAGE
    requires = ("probe",)
    after = ("metadata",)  # the colour profile (log footage) changes the frame normalisation

    def cache_config(self, prefs: AnalysisPreferences, ctx: StageContext) -> dict[str, Any]:
        return {
            "interval": prefs.keyframe_interval_s,
            "scene": prefs.scene_threshold,
            "max": prefs.max_keyframes,
            "dedup": prefs.dedup_max_distance,
            "long_side": KEYFRAME_LONG_SIDE,
        }

    def input_facts(self, ctx: StageContext) -> dict[str, Any]:
        with ctx.tools.db.read() as session:
            video = session.get_one(Video, ctx.video.id)
            profile, hdr_peak = video.color_profile, video.hdr_peak_nits
        return {
            "log_profile": profile if profile in LOG_PROFILES else None,
            # Only HDR sources depend on the peak: SDR cache keys stay unchanged.
            **({"hdr_peak_nits": hdr_peak} if hdr_peak else {}),
        }

    def run(self, ctx: StageContext) -> StageOutcome:
        store = ctx.tools.artifacts
        with ctx.tools.db.read() as session:
            video = session.get_one(Video, ctx.video.id)
            look = FrameLook.of_video(video)
            need = decode_need_mib(video.width, video.height)
        stream = video_stream_facts(ctx)
        enabled = ctx.prefs.gpu_decode and gpu_decodes(
            stream.get("video_codec"), stream.get("pix_fmt")
        )

        ctx.progress(0.05, "Extraction des images candidates")
        candidates_dir = store.subdir(ctx.video.id, "candidates", reset=True)
        decode: dict[str, str] = {}
        try:
            with ctx.tools.gpu.borrow(need, enabled=enabled) as gpu:
                candidates = ctx.tools.ffmpeg.extract_candidates(
                    ctx.video.path,
                    candidates_dir,
                    scene_threshold=ctx.prefs.scene_threshold,
                    min_interval_s=ctx.prefs.keyframe_interval_s,
                    long_side=KEYFRAME_LONG_SIDE,
                    hdr=look.hdr,
                    hdr_peak_nits=look.hdr_peak_nits,
                    log_profile=look.log_profile,
                    hwaccel="cuda" if gpu else None,
                    report=decode,
                    cancel=ctx.cancel,
                )
            ctx.progress(0.6, f"{len(candidates)} images candidates")
            kept, duplicates = self._deduplicate(ctx, candidates)
            kept = _thin(kept, ctx.prefs.max_keyframes)
            self._persist(ctx, kept)
        finally:
            shutil.rmtree(candidates_dir, ignore_errors=True)
        return StageOutcome.ok(
            candidates=len(candidates),
            kept=len(kept),
            duplicates=duplicates,
            decoder=decode.get("decoder", "cpu"),
        )

    def _deduplicate(
        self, ctx: StageContext, candidates: list[CandidateFrame]
    ) -> tuple[list[_Kept], int]:
        dedup = DuplicateFilter(max_distance=ctx.prefs.dedup_max_distance)
        kept: list[_Kept] = []
        duplicates = 0
        for index, candidate in enumerate(candidates):
            ctx.cancel.raise_if_cancelled()
            image = read_image(candidate.path)
            frame_hash = frame_dhash(image)
            scene_cut = (candidate.scene_score or 0.0) > ctx.prefs.scene_threshold
            # A real cut is always kept; interval frames only when they differ from recent ones.
            if not scene_cut and index > 0 and dedup.is_duplicate(frame_hash):
                duplicates += 1
                continue
            dedup.keep(frame_hash)
            reason = "first" if index == 0 else "scene" if scene_cut else "interval"
            kept.append(_Kept(candidate, frame_hash, reason))
        return kept, duplicates

    def _persist(self, ctx: StageContext, kept: list[_Kept]) -> None:
        files = KeyframeFiles(ctx.tools.artifacts, ctx.video.id)
        rows: list[Keyframe] = []
        for idx, item in enumerate(kept):
            ctx.cancel.raise_if_cancelled()
            image = read_image(item.candidate.path)
            image_path, thumb_path = files.write(idx, image)
            height, width = image.shape[:2]
            rows.append(
                Keyframe(
                    video_id=ctx.video.id,
                    idx=idx,
                    t_s=round(item.candidate.t_s, 3),
                    image_path=image_path,
                    thumb_path=thumb_path,
                    width=width,
                    height=height,
                    selection_reason=item.reason,
                    phash=f"{item.hash:016x}",
                    sharpness=round(sharpness(image), 2),
                )
            )
            ctx.progress(0.6 + 0.35 * (idx + 1) / max(1, len(kept)), None)

        poster = pick_poster(rows, ctx.video.duration_s)
        with ctx.tools.db.write() as session:
            # Analyses of the previous keyframes are removed by ON DELETE CASCADE. The shot
            # stories name them and show their thumbnails without a foreign key: they go too,
            # until vision_shots tells the shots again from the new keyframes.
            session.execute(sa.delete(Keyframe).where(Keyframe.video_id == ctx.video.id))
            session.execute(sa.delete(ShotStory).where(ShotStory.video_id == ctx.video.id))
            session.add_all(rows)
            video = session.get_one(Video, ctx.video.id)
            video.poster_path = poster.thumb_path if poster else None
        files.keep()
        ctx.tools.artifacts.prune(ctx.video.id, SHOT_FRAMES_DIR, keep="")  # those stories' frames


def _thin(kept: list[_Kept], limit: int) -> list[_Kept]:
    """Keep at most ``limit`` frames, evenly spread in time (always keeping the first one)."""
    if len(kept) <= limit:
        return kept
    if limit <= 1:
        return kept[:1]
    step = (len(kept) - 1) / (limit - 1)
    indices = sorted({round(i * step) for i in range(limit)})
    return [kept[i] for i in indices]
