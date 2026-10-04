"""Analysis requests: what a focus, a folder correction or a new file content redo."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from vfe_vision.core.config import Settings
from vfe_vision.core.errors import InvalidInputError
from vfe_vision.db.models import FrameAnalysis, Job, Keyframe, LibraryRoot, StageRun, Video
from vfe_vision.domain.enums import AnalysisMode, StageStatus, VideoStatus
from vfe_vision.jobs.scan import _update_existing
from vfe_vision.pipeline.stages import default_registry
from vfe_vision.services import analysis
from vfe_vision.services.container import AppContainer


@pytest.fixture
def container(settings: Settings, db: object, tmp_path: Path) -> AppContainer:
    return AppContainer.create(settings)


def _video(c: AppContainer, tmp_path: Path, *, status: VideoStatus = VideoStatus.READY) -> str:
    with c.db.write() as session:
        root = LibraryRoot(path=str(tmp_path), path_key=str(tmp_path).lower(), label="t")
        session.add(root)
        session.flush()
        video = Video(
            root_id=root.id, path=str(tmp_path / "a.mp4"), path_key=str(tmp_path / "a.mp4"),
            rel_path="a.mp4", filename="a.mp4", size_bytes=1, mtime=0.0, fingerprint="f",
            status=status,
        )  # fmt: skip
        session.add(video)
        session.flush()
        for stage in ("metadata", "weather", "keyframes", "vision_frames"):
            session.add(
                StageRun(
                    video_id=video.id, stage=stage, stage_version=1, cache_key="k",
                    input_key="i", status=StageStatus.SUCCEEDED, summary={},
                )
            )  # fmt: skip
        return video.id


def _describe(c: AppContainer, video_id: str, focus: str | None) -> None:
    with c.db.write() as session:
        frame = Keyframe(
            video_id=video_id, idx=0, t_s=0.0, image_path="x.jpg", thumb_path="t.jpg",
            width=64, height=36, selection_reason="first",
        )  # fmt: skip
        session.add(frame)
        session.flush()
        session.add(
            FrameAnalysis(
                keyframe_id=frame.id, model="qwen/qwen3-vl-8b", prompt_version="v2",
                schema_version=1, focus=focus, language="fr", data={},
            )
        )  # fmt: skip


def _payload(c: AppContainer, job_id: str) -> dict[str, object]:
    with c.db.read() as session:
        return dict(session.get_one(Job, job_id).payload)


def test_the_same_focus_does_not_redo_descriptions(container: AppContainer, tmp_path: Path) -> None:
    video_id = _video(container, tmp_path)
    _describe(container, video_id, "les oiseaux")
    same = analysis.request_analysis(container, video_id, focus="  les oiseaux ")
    assert _payload(container, same.id)["refresh"] is False
    other = analysis.request_analysis(container, video_id, focus="les chats")
    assert _payload(container, other.id)["refresh"] == ["vision_frames"]


def test_folder_corrections_redo_the_stages_reading_them(
    container: AppContainer, tmp_path: Path
) -> None:
    video_id = _video(container, tmp_path)
    with container.db.read() as session:
        root = session.execute(sa.select(LibraryRoot)).scalar_one()
    assert not analysis.root_inputs_differ(root, {"clock_offset_s": 0})  # none = 0 s
    assert not analysis.root_inputs_differ(root, {"label": "Vacances"})
    assert analysis.root_inputs_differ(root, {"clock_offset_s": 3600})

    assert analysis.root_inputs_changed(container, root.id) == 1
    with container.db.read() as session:
        keys = dict(
            session.execute(
                sa.select(StageRun.stage, StageRun.cache_key).where(StageRun.video_id == video_id)
            ).all()
        )
    assert keys == {"metadata": "", "weather": "", "keyframes": "k", "vision_frames": "k"}


def test_new_file_content_drops_every_earlier_result(
    container: AppContainer, tmp_path: Path
) -> None:
    video_id = _video(container, tmp_path)
    (tmp_path / "a.mp4").write_bytes(b"new content")
    stat = (tmp_path / "a.mp4").stat()
    _update_existing(container.db, video_id, tmp_path / "a.mp4", stat, "g", base=tmp_path)
    with container.db.read() as session:
        keys = set(
            session.execute(
                sa.select(StageRun.cache_key).where(StageRun.video_id == video_id)
            ).scalars()
        )
        assert session.get_one(Video, video_id).status == VideoStatus.NEW
    assert keys == {""}


def test_folder_complete_rechecks_skipped_stages(container: AppContainer, tmp_path: Path) -> None:
    video_id = _video(container, tmp_path)
    with container.db.write() as session:  # every other stage done too
        done = {"metadata", "weather", "keyframes", "vision_frames"}  # added by _video
        for stage in (name for name in default_registry().names if name not in done):
            session.add(
                StageRun(
                    video_id=video_id, stage=stage, stage_version=99, cache_key="k",
                    input_key="i", status=StageStatus.SUCCEEDED, summary={},
                )
            )  # fmt: skip
    root_id = _root_id(container)
    assert analysis.analyze_root(container, root_id) == 0  # finished: nothing to queue
    with container.db.write() as session:  # skipped because of the video itself: settled
        session.execute(
            sa.update(StageRun)
            .where(StageRun.stage == "proxy")
            .values(
                status=StageStatus.SKIPPED,
                skip_reason="Lisible directement par le navigateur",
                summary={"retryable": False, "permanent": True},
            )
        )
    assert analysis.analyze_root(container, root_id) == 0
    with container.db.write() as session:  # the weather was skipped while offline
        session.execute(
            sa.update(StageRun)
            .where(StageRun.stage == "weather")
            .values(status=StageStatus.SKIPPED, skip_reason="Services en ligne désactivés")
        )
    assert analysis.analyze_root(container, root_id) == 1


def _root_id(c: AppContainer) -> str:
    with c.db.read() as session:
        return session.execute(sa.select(LibraryRoot.id)).scalar_one()


def test_a_redone_stage_names_only_itself(container: AppContainer, tmp_path: Path) -> None:
    video_id = _video(container, tmp_path)
    job = analysis.request_analysis(
        container, video_id, stages=["keyframes"], mode=AnalysisMode.FULL
    )
    payload = _payload(container, job.id)
    # The stages reading its results are added when the job starts (PipelineRunner.run),
    # when the video has no other job running.
    assert payload["stages"] == ["keyframes"]
    assert payload["force"] == ["keyframes"]
    assert payload["refresh"] is False


def _add_video(
    c: AppContainer, name: str, status: VideoStatus, stages: dict[str, StageStatus]
) -> str:
    with c.db.write() as session:
        root_id = session.execute(sa.select(LibraryRoot.id)).scalar_one()
        video = Video(
            root_id=root_id, path=name, path_key=name, rel_path=name, filename=name,
            size_bytes=1, mtime=0.0, fingerprint=name, status=status,
        )  # fmt: skip
        session.add(video)
        session.flush()
        for stage, state in stages.items():
            session.add(
                StageRun(
                    video_id=video.id, stage=stage, stage_version=99, cache_key=f"k-{stage}",
                    input_key="i", status=state, summary={},
                )
            )  # fmt: skip
        return video.id


def test_a_batch_only_queues_videos_with_something_to_do(
    container: AppContainer, tmp_path: Path
) -> None:
    _video(container, tmp_path)  # creates the folder
    done = dict.fromkeys(default_registry().names, StageStatus.SUCCEEDED)
    finished = _add_video(container, "fini.mp4", VideoStatus.READY, done)
    fresh = _add_video(container, "neuve.mp4", VideoStatus.NEW, {})
    gone = _add_video(container, "partie.mp4", VideoStatus.OFFLINE, {})
    no_sound = _add_video(
        container, "muette.mp4", VideoStatus.PARTIAL, {**done, "audio_events": StageStatus.FAILED}
    )
    ids = [finished, fresh, gone, no_sound, fresh, "retiree"]  # a repeated id counts once

    batch = analysis.analyze_videos(container, ids)
    assert batch == analysis.BatchAnalysis(
        queued=2, up_to_date=1, offline=1, unknown=("retiree",)
    )  # a video removed since it was picked does not block the others

    # Only the chosen stages (and what they need) count: the sounds are not asked for here.
    with container.db.write() as session:
        session.execute(sa.delete(Job))
    batch = analysis.analyze_videos(container, [finished, no_sound], stages=["place"])
    assert batch == analysis.BatchAnalysis(queued=0, up_to_date=2, offline=0)
    batch = analysis.analyze_videos(container, [finished, no_sound], stages=["audio_events"])
    assert batch == analysis.BatchAnalysis(queued=1, up_to_date=1, offline=0)

    # Another mode is an explicit request: every reachable video is queued.
    with container.db.write() as session:
        session.execute(sa.delete(Job))
    batch = analysis.analyze_videos(
        container, [finished, gone], stages=["place"], mode=AnalysisMode.FULL
    )
    assert batch == analysis.BatchAnalysis(queued=1, up_to_date=0, offline=1)
    with container.db.read() as session:
        job = session.execute(sa.select(Job)).scalar_one()
    assert job.video_id == finished
    assert job.payload["force"] == ["place"]
    assert job.payload["stages"] == ["place"]


def test_a_batch_is_checked_before_anything_is_queued(
    container: AppContainer, tmp_path: Path
) -> None:
    video_id = _video(container, tmp_path)
    with pytest.raises(InvalidInputError):
        analysis.analyze_videos(container, [video_id], stages=["montage"])
    with container.db.read() as session:
        assert session.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one() == 0
