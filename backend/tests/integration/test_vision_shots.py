"""The stage ``vision_shots``: what happens in each shot, told from its frames."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import sqlalchemy as sa
import structlog

from tests.conftest import SHOT_ANSWER, FakeLmStudio
from vfe_vision.adapters.ffmpeg.tools import CandidateFrame, Ffmpeg
from vfe_vision.adapters.imaging import write_jpeg
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.api.schemas import ShotOut
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import CancelledError, VfeError
from vfe_vision.db.models import (
    ContextPlace,
    FrameAnalysis,
    Keyframe,
    LibraryRoot,
    Shot,
    ShotStory,
    Video,
)
from vfe_vision.db.session import Database
from vfe_vision.db.translations import TranslationCache
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.domain.shots import SOFT_MIN_SCORE
from vfe_vision.mcp.formatting import _shot_lines
from vfe_vision.mcp.server import _shots
from vfe_vision.pipeline.stage import StageContext, StageOutcome, Toolbox, VideoRef
from vfe_vision.pipeline.stages.keyframes import KeyframesStage, _Kept
from vfe_vision.pipeline.stages.vision_shots import VisionShotsStage, _plan
from vfe_vision.services import videos
from vfe_vision.storage.artifacts import ArtifactStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def video(db: Database, sample_video: Path) -> VideoRef:
    with db.write() as session:
        root = LibraryRoot(
            path=str(sample_video.parent), path_key=str(sample_video.parent).lower(), label="t"
        )
        session.add(root)
        session.flush()
        row = Video(
            root_id=root.id, path=str(sample_video), path_key=str(sample_video).lower(),
            rel_path=sample_video.name, filename=sample_video.name, size_bytes=1, mtime=0.0,
            fingerprint="f" * 16, duration_s=6.0, has_audio=True, width=320, height=240,
        )  # fmt: skip
        session.add(row)
        session.flush()
        return VideoRef(id=row.id, path=sample_video, filename=row.filename, fingerprint="f" * 16)


def _shot(db: Database, video: VideoRef, idx: int, start: float, end: float, **kw: Any) -> str:
    with db.write() as session:
        shot = Shot(
            video_id=video.id, idx=idx, start_s=start, end_s=end, boundary="cut",
            motion=kw.get("motion", "pan_left"), motion_score=kw.get("score", 1.5), stability=0.9,
            metrics=kw.get("metrics", {}),
        )  # fmt: skip
        session.add(shot)
        session.flush()
        return shot.id


def _keyframe(
    settings: Settings,
    db: Database,
    video: VideoRef,
    shot_id: str,
    *,
    idx: int,
    t: float,
    caption: str | None = None,
) -> str:
    store = ArtifactStore(settings.artifacts_dir)
    path = store.subdir(video.id, "keyframes") / f"kf{idx}.jpg"
    write_jpeg(path, np.full((240, 320, 3), 40 * idx % 255, np.uint8))
    with db.write() as session:
        row = Keyframe(
            video_id=video.id, idx=idx, t_s=t, image_path=store.rel(path),
            thumb_path=store.rel(path), width=320, height=240, selection_reason="scene",
            shot_id=shot_id,
        )  # fmt: skip
        session.add(row)
        session.flush()
        if caption:
            session.add(
                FrameAnalysis(
                    keyframe_id=row.id, model="m", prompt_version="frame_analysis.v2",
                    schema_version=1, language="fr",
                    data={"caption": caption, "actions": ["court"]},
                )
            )  # fmt: skip
        return row.id


def _ctx(
    settings: Settings, db: Database, video: VideoRef, lm: FakeLmStudio | None = None
) -> StageContext:
    tools = cast(
        Toolbox,
        type(
            "T",
            (),
            {
                "db": db,
                "artifacts": ArtifactStore(settings.artifacts_dir),
                "ffmpeg": Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
                "lmstudio": (lm or FakeLmStudio()).client(),
                "lm_budget": TokenBudget(capacity=8192, max_concurrency=1),
            },
        )(),
    )
    return StageContext(
        video=video,
        prefs=AnalysisPreferences(),
        tools=tools,
        cancel=CancelToken(),
        progress=lambda _f, _m: None,
        log=structlog.get_logger("test"),
    )


def _stories(db: Database) -> list[ShotStory]:
    with db.read() as session:
        return list(session.execute(sa.select(ShotStory).order_by(ShotStory.start_s)).scalars())


async def test_a_shot_is_told_from_its_own_frames_in_order(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    first = _shot(db, video, 0, 0.0, 5.0)
    second = _shot(db, video, 1, 5.0, 6.0)  # under 4 s: not told
    ids = [
        _keyframe(settings, db, video, first, idx=0, t=0.0, caption="Un chien court"),
        _keyframe(settings, db, video, first, idx=1, t=2.0),
        # A scene keyframe sits one frame before its cut, inside the previous shot's time
        # range: it belongs to the next shot and is never sent with this one.
        _keyframe(settings, db, video, second, idx=2, t=4.96, caption="Un chat"),
    ]
    lm = FakeLmStudio()
    lm.answers = [
        json.dumps(
            SHOT_ANSWER
            | {
                "main_action": "zoom avant sur le chien",  # camera only: dropped
                "beats": [{"image": 2, "what": "il saute"}, {"image": 2, "what": "encore"}],
                "continuity": "subject_changes",
            }
        )
    ]
    ctx = _ctx(settings, db, video, lm)
    outcome = await VisionShotsStage().execute(ctx)
    assert outcome.status == StageStatus.SUCCEEDED
    assert outcome.summary == {"model": "qwen/qwen3-vl-8b", "shots": 1, "parts": 1, "told": 1,
                               "cached": 0, "failed": 0}  # fmt: skip

    [request] = lm.chat_requests
    assert request["model"] == "qwen/qwen3-vl-8b"  # the loaded instance, never another
    content = request["messages"][1]["content"]
    user = content[0]["text"]
    assert user.startswith(f'Shot 1 of the video "{video.filename}": from 0.0 s to 5.0 s')
    assert "- Image 1: Un chien court (actions: court)" in user
    assert "Un chat" not in user
    assert "The 4 images follow in chronological order." in user
    labels = [part["text"] for part in content[1:] if part["type"] == "text"]
    assert labels == [
        "Image 1 - 0.0 s into this part:",
        "Image 2 - 2.0 s into this part:",
        "Image 3 - 3.2 s into this part:",  # extracted: no keyframe there
        "Image 4 - 4.7 s into this part:",
    ]
    assert sum(part["type"] == "image_url" for part in content) == 4
    schema = request["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["best_image"]["maximum"] == 4
    assert "French" in request["messages"][0]["content"]

    [story] = _stories(db)
    assert (story.part, story.parts, story.start_s, story.end_s) == (1, 1, 0.0, 5.0)
    assert story.frame_times == [0.0, 2.0, 3.233, 4.7]
    assert story.keyframe_ids == [ids[0], ids[1], None, None]
    store = ArtifactStore(settings.artifacts_dir)
    assert all(store.resolve(p).is_file() for p in story.frame_paths)
    assert "shot_frames" in story.frame_paths[2]
    assert story.story["main_action"] == ""
    assert story.story["notes"] == [{"t_s": 2.0, "what": "il saute"}]
    assert story.story["possible_cut"] is True

    # Nothing changed: the answer comes from the cache, the extracted frames are redone.
    again = await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    assert (again.summary["told"], again.summary["cached"]) == (0, 1)
    assert len(lm.chat_requests) == 1
    [story] = _stories(db)
    assert all(store.resolve(p).is_file() for p in story.frame_paths)
    folders = list((store.video_dir(video.id) / "shot_frames").iterdir())
    assert len(folders) == 1  # the older extracted frames are gone


async def test_a_long_shot_is_told_in_parts(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    shot = _shot(db, video, 0, 0.0, 45.0)
    for idx, t in enumerate([1, 4, 7, 10, 13, 16, 19, 24, 27, 30, 33, 36, 39, 42]):
        _keyframe(settings, db, video, shot, idx=idx, t=float(t))
    lm = FakeLmStudio()
    outcome = await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    assert outcome.summary["parts"] == 2
    users = sorted(r["messages"][1]["content"][0]["text"] for r in lm.chat_requests)
    assert users[0].startswith(f'Shot 1 of the video "{video.filename}", part 1 of 2: from 0.0')
    assert users[1].startswith(f'Shot 1 of the video "{video.filename}", part 2 of 2: from 24.0')
    stories = _stories(db)
    assert [(s.part, s.start_s, s.end_s) for s in stories] == [(1, 0.0, 24.0), (2, 24.0, 45.0)]
    assert all(len(s.frame_times) == 4 for s in stories)  # four keyframes each, no extraction
    assert all(s.keyframe_ids[0] is not None for s in stories)


async def test_lm_studio_down_keeps_the_previous_stories(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    shot = _shot(db, video, 0, 0.0, 5.0)
    for idx, t in enumerate([0.5, 1.5, 2.5, 3.5]):
        _keyframe(settings, db, video, shot, idx=idx, t=t)
    lm = FakeLmStudio()
    await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    lm.down = True
    outcome = await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SKIPPED
    assert outcome.retryable
    assert len(_stories(db)) == 1


async def test_shots_with_nothing_to_tell(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    still = _shot(db, video, 0, 0.0, 5.0, motion="static")  # one distinct keyframe
    _keyframe(settings, db, video, still, idx=0, t=1.0)
    _shot(db, video, 1, 5.0, 12.0, metrics={"frozen_ratio": 0.95})  # a frozen screen
    lm = FakeLmStudio()
    outcome = await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SUCCEEDED
    assert outcome.summary["parts"] == 0
    assert lm.chat_requests == []


async def test_nothing_to_tell_is_settled_without_lm_studio(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    shot = _shot(db, video, 0, 0.0, 5.0, motion="handheld", score=0.8)
    _keyframe(settings, db, video, shot, idx=0, t=1.0)
    lm = FakeLmStudio()
    await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    assert len(_stories(db)) == 1
    with db.write() as session:  # a weak shake on one keyframe: shown « static », nothing to tell
        session.execute(sa.update(Shot).where(Shot.id == shot).values(motion_score=0.3))
    lm.down = True
    ctx = _ctx(settings, db, video, lm)
    # Decided before waiting for LM Studio: never a retryable « LM Studio injoignable ».
    assert VisionShotsStage().precheck(ctx) == StageOutcome.ok(shots=0, parts=0)
    assert _stories(db) == []  # the stale story goes, with its extracted frames
    store = ArtifactStore(settings.artifacts_dir)
    assert list((store.video_dir(video.id) / "shot_frames").iterdir()) == []
    outcome = await VisionShotsStage().execute(ctx)
    assert (outcome.status, outcome.retryable) == (StageStatus.SUCCEEDED, False)
    assert len(lm.chat_requests) == 1  # the first story only


def test_eligibility_reads_the_camera_label_as_shown(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    weak = _shot(db, video, 0, 0.0, 5.0, motion="handheld", score=0.49)  # shown « static »
    _keyframe(settings, db, video, weak, idx=0, t=1.0)
    moving = _shot(db, video, 1, 5.0, 10.0, motion="moving", score=0.6)
    _keyframe(settings, db, video, moving, idx=1, t=6.0)
    plan = _plan(_ctx(settings, db, video))
    assert (plan.shots, [p.shot_id for p in plan.parts]) == (1, [moving])
    config = VisionShotsStage().cache_config(AnalysisPreferences(), _ctx(settings, db, video))
    assert config["soft_motion"] == SOFT_MIN_SCORE


def test_the_place_is_a_setting_not_a_fact(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    shot = _shot(db, video, 0, 0.0, 5.0)
    _keyframe(settings, db, video, shot, idx=0, t=0.5)
    _keyframe(settings, db, video, shot, idx=1, t=2.5)
    ctx, stage = _ctx(settings, db, video), VisionShotsStage()
    facts = stage.input_facts(ctx)
    assert stage.cache_config(ctx.prefs, ctx)["place"] is None
    with db.write() as session:
        session.add(
            ContextPlace(
                video_id=video.id, source="nominatim", locality="Katmandou", region="Bagmati",
                country="Népal", data={},
            )
        )  # fmt: skip
    # A place found or made more precise re-tells nothing, unless an update is asked.
    assert stage.input_facts(ctx) == facts
    assert stage.cache_config(ctx.prefs, ctx)["place"] == "Katmandou, Bagmati, Népal"


async def test_new_keyframes_drop_the_stories_and_their_frames(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    shot = _shot(db, video, 0, 0.0, 5.0)
    _keyframe(settings, db, video, shot, idx=0, t=0.0)
    _keyframe(settings, db, video, shot, idx=1, t=2.0)  # and two extracted frames
    lm = FakeLmStudio()
    await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    store = ArtifactStore(settings.artifacts_dir)
    frames_dir = store.video_dir(video.id) / "shot_frames"
    assert len(_stories(db)) == 1
    assert len(list(frames_dir.iterdir())) == 1

    candidate = store.subdir(video.id, "candidates") / "c.jpg"
    write_jpeg(candidate, np.full((240, 320, 3), 90, np.uint8))
    ctx = _ctx(settings, db, video, lm)
    KeyframesStage()._persist(ctx, [_Kept(CandidateFrame(candidate, 1.0, None), 0, "first")])
    # The stories named the deleted keyframes and showed their pruned thumbnails.
    assert _stories(db) == []
    assert list(frames_dir.iterdir()) == []
    lm.down = True  # the stage is put off: nothing stale is served meanwhile
    assert (await VisionShotsStage().execute(ctx)).status == StageStatus.SKIPPED
    assert _stories(db) == []

    lm.down = False  # told again from the new keyframe, once technical links it to its shot
    with db.write() as session:
        session.execute(sa.update(Keyframe).values(shot_id=shot))
    outcome = await VisionShotsStage().execute(_ctx(settings, db, video, lm))
    assert outcome.status == StageStatus.SUCCEEDED
    [story] = _stories(db)
    assert story.frame_times == [1.0, 3.233, 4.7]
    assert all(store.resolve(p).is_file() for p in story.frame_paths)
    assert len(list(frames_dir.iterdir())) == 1  # a new generation of extracted frames


async def test_a_cancel_while_frames_are_extracted_leaves_no_new_folder(
    settings: Settings, db: Database, video: VideoRef, monkeypatch: pytest.MonkeyPatch
) -> None:
    shot = _shot(db, video, 0, 0.0, 5.0)
    _keyframe(settings, db, video, shot, idx=0, t=0.0)
    _keyframe(settings, db, video, shot, idx=1, t=2.0)  # two frames to extract
    await VisionShotsStage().execute(_ctx(settings, db, video))
    store = ArtifactStore(settings.artifacts_dir)
    [stored] = list((store.video_dir(video.id) / "shot_frames").iterdir())

    ctx = _ctx(settings, db, video)
    extract = ctx.tools.ffmpeg.extract_frame

    def extract_then_cancel(*args: Any, **kwargs: Any) -> Path:
        path = extract(*args, **kwargs)
        ctx.cancel.cancel()  # between the first and the second frame
        return path

    monkeypatch.setattr(ctx.tools.ffmpeg, "extract_frame", extract_then_cancel)
    with pytest.raises(CancelledError):
        await VisionShotsStage().execute(ctx)
    assert list((store.video_dir(video.id) / "shot_frames").iterdir()) == [stored]
    [story] = _stories(db)
    assert all(store.resolve(p).is_file() for p in story.frame_paths)


async def test_a_part_whose_frames_all_failed_is_never_sent(
    settings: Settings, db: Database, video: VideoRef, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shot(db, video, 0, 0.0, 5.0)  # no keyframe of its own: its four frames are extracted
    lm = FakeLmStudio()
    ctx = _ctx(settings, db, video, lm)

    def unreadable(*args: Any, **kwargs: Any) -> Path:
        raise VfeError("ffmpeg : passage illisible")

    monkeypatch.setattr(ctx.tools.ffmpeg, "extract_frame", unreadable)
    with pytest.raises(VfeError, match="Aucun plan"):  # counted as failed, the only part
        await VisionShotsStage().execute(ctx)
    assert lm.chat_requests == []  # no request with 0 images and image numbers from 1 to 0


def test_new_descriptions_are_new_facts(settings: Settings, db: Database, video: VideoRef) -> None:
    shot = _shot(db, video, 0, 0.0, 5.0)
    first = _keyframe(settings, db, video, shot, idx=0, t=0.5, caption="Un chien")
    _keyframe(settings, db, video, shot, idx=1, t=2.5)
    ctx = _ctx(settings, db, video)
    before = VisionShotsStage().input_facts(ctx)
    assert VisionShotsStage().input_facts(ctx) == before
    with db.write() as session:
        analysis = session.execute(
            sa.select(FrameAnalysis).where(FrameAnalysis.keyframe_id == first)
        ).scalar_one()
        analysis.data = {"caption": "A dog"}  # described again in English
    assert VisionShotsStage().input_facts(ctx) != before


async def test_api_and_mcp_serve_the_stories(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    shot_id = _shot(db, video, 0, 0.0, 5.0)
    # Keyframes 5 to 8 of the video (the first four belong to earlier shots, elsewhere).
    for idx, t in enumerate([0.5, 1.5, 2.5, 3.5], start=4):
        _keyframe(settings, db, video, shot_id, idx=idx, t=t)
    await VisionShotsStage().execute(_ctx(settings, db, video))

    container = cast(Any, type("C", (), {"db": db, "translations": TranslationCache()})())
    view = videos.get_shots_view(container, video.id)
    [shot] = view.shots
    out = ShotOut.of(shot, view.stories[shot.id])
    [story] = out.stories
    assert story.summary == SHOT_ANSWER["summary"]
    assert [f.note for f in story.frames] == [None, "les barres apparaissent", None, None]
    assert story.frames[0].thumb_url.startswith("/api/v1/media/")

    [line] = _shot_lines(view.shots, view.stories)
    assert line.endswith(f"panoramique gauche, stabilité 90% · {SHOT_ANSWER['summary']}")
    with db.read() as session:
        row = session.get_one(Video, video.id)
    keyframes = videos.get_keyframes(container, video.id)
    shots = _shots(row, view, keyframes=keyframes, start_s=None, end_s=None, max_shots=10)
    assert shots.status == "ready"
    [info] = shots.shots
    assert (info.shot, info.camera, len(info.parts)) == (1, "pan_left", 1)
    # The note is on the part's image 2: keyframe 6 of the video, as get_frames numbers it.
    [note] = info.parts[0].frames
    assert (note.seen_at_s, note.keyframe) == (1.5, 6)
    unlisted = _shots(row, view, keyframes=[], start_s=None, end_s=None, max_shots=10)
    assert unlisted.shots[0].parts[0].frames[0].keyframe is None  # an unknown id: no number
    assert _shots(row, view, keyframes=keyframes, start_s=6.0, end_s=None, max_shots=10).shots == []
