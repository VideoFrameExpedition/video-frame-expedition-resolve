"""Subject positions: the two stages wired to the database, the API and the MCP text.

The CPU detectors are replaced by fakes and LM Studio by the fake server, so the default run
needs no model; ``-m models`` checks the real D-FINE and YuNet files.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pytest
import sqlalchemy as sa
import structlog
from fastapi.testclient import TestClient

from tests.conftest import FakeLmStudio
from tests.fakes.vision_models import GemmaLike, OtherModel, XFirstOther, is_probe
from tests.integration.test_audio_text_stages import FakeStore, _container, _keyframes, _record
from vfe_vision.adapters.detection import yunet
from vfe_vision.adapters.detection.dfine import RawBox
from vfe_vision.adapters.detection.yunet import Face
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.imaging import write_jpeg
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.api.app import create_app
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import CancelledError
from vfe_vision.db.models import (
    Detection,
    Keyframe,
    LibraryRoot,
    LlmCall,
    SubjectScan,
    Video,
)
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import StageStatus
from vfe_vision.domain.preferences import AnalysisPreferences
from vfe_vision.mcp.formatting import subjects_summary
from vfe_vision.mcp.server import _locations
from vfe_vision.pipeline.stage import StageContext, Toolbox, VideoRef
from vfe_vision.pipeline.stages import detections
from vfe_vision.pipeline.stages.detections import DetectionsStage
from vfe_vision.pipeline.stages.grounding import GroundingStage
from vfe_vision.services import subjects
from vfe_vision.storage.artifacts import ArtifactStore

MODELS = ("detector/d-fine-s-coco", "faces/yunet")


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
            fingerprint="f" * 16, duration_s=6.0, has_audio=True, width=1920, height=1080,
        )  # fmt: skip
        session.add(row)
        session.flush()
        return VideoRef(id=row.id, path=sample_video, filename=row.filename, fingerprint="f" * 16)


def _ctx(
    settings: Settings,
    db: Database,
    video: VideoRef,
    *,
    models: FakeStore | None = None,
    lmstudio: FakeLmStudio | None = None,
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
                "models": models,
                "lmstudio": (lmstudio or FakeLmStudio()).client(),
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


class FakeDFine:
    def detect(self, image: Any, *, keep: Any, min_score: float) -> list[RawBox]:
        assert "person" in keep
        assert "car" not in keep
        return [
            RawBox("person", 0.92, (0.1, 0.1, 0.4, 0.9)),
            RawBox("person", 0.71, (0.11, 0.1, 0.4, 0.9)),  # the same person twice: merged
            RawBox("cat", 0.8, (0.6, 0.6, 0.8, 0.8)),
        ]


class FakeFaces:
    def detect(self, image: Any) -> list[Face]:
        eyes = ((0.23, 0.16), (0.27, 0.16), (0.25, 0.19), (0.235, 0.22), (0.265, 0.22))
        return [Face((0.2, 0.12, 0.3, 0.25), 0.9, eyes), Face((0.9, 0.1, 0.95, 0.2), 0.8)]


@pytest.fixture
def fake_detectors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detections, "_detector", lambda _dir: FakeDFine())
    monkeypatch.setattr(yunet.FaceDetector, "from_file", lambda _path: FakeFaces())


def _rows(db: Database, source: str | None = None) -> list[Detection]:
    with db.read() as session:
        query = sa.select(Detection).order_by(Detection.t_s, Detection.idx)
        if source:
            query = query.where(Detection.source == source)
        return list(session.execute(query).scalars())


GROUNDED = {
    "beings": [
        {"label": "chat", "category": "mammal", "main": True, "bbox_2d": [600, 600, 800, 800]},
        {"label": "femme", "category": "person", "main": False, "bbox_2d": [105, 100, 400, 900]},
        {"label": "abeille", "category": "insect", "main": False, "bbox_2d": [450, 100, 480, 140]},
    ]
}


# ------------------------------------------------------------------ CPU detectors
@pytest.mark.usefixtures("fake_detectors")
class TestDetections:
    def test_people_animals_and_faces_are_stored(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        ids = _keyframes(settings, db, video, 2)
        ctx = _ctx(settings, db, video, models=FakeStore(settings.models_dir, *MODELS))
        outcome = DetectionsStage().run(ctx)
        assert outcome.status == StageStatus.SUCCEEDED
        assert outcome.summary == {
            "keyframes": 2,
            "with_subjects": 2,
            "boxes": 4,
            "faces": 4,
            "unreadable": 0,
        }
        rows = [r for r in _rows(db) if r.keyframe_id == ids[0]]
        assert [(r.source, r.label, r.category) for r in rows] == [
            ("detector", "person", "person"),
            ("detector", "cat", "mammal"),
            ("faces", "face", "face"),
            ("faces", "face", "face"),
        ]
        assert rows[0].box == [0.1, 0.1, 0.4, 0.9]
        assert rows[0].model == "detector/d-fine-s-coco@test + faces/yunet@test"
        assert rows[2].points is not None
        assert rows[2].points[0] == [0.23, 0.16]  # right eye, for the eye line
        with db.read() as session:
            scans = session.execute(sa.select(SubjectScan)).scalars().all()
        assert {(s.source, s.found) for s in scans} == {("detector", 2), ("faces", 2)}

    def test_a_new_run_replaces_its_own_boxes_only(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        ids = _keyframes(settings, db, video, 1)
        with db.write() as session:
            session.add(
                Detection(
                    keyframe_id=ids[0], video_id=video.id, t_s=0.0, source="vlm", idx=0,
                    label="abeille", category="insect", box=[0.4, 0.1, 0.5, 0.2], score=None,
                    main=True, model="qwen/qwen3-vl-8b",
                )
            )  # fmt: skip
        ctx = _ctx(settings, db, video, models=FakeStore(settings.models_dir, *MODELS))
        DetectionsStage().run(ctx)
        DetectionsStage().run(ctx)
        assert len(_rows(db, "detector")) == 2
        assert [r.label for r in _rows(db, "vlm")] == ["abeille"]

    def test_duplicate_keyframes_are_not_analysed(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        ids = _keyframes(settings, db, video, 2)
        with db.write() as session:
            session.get_one(Keyframe, ids[1]).duplicate_of = ids[0]
        ctx = _ctx(settings, db, video, models=FakeStore(settings.models_dir, *MODELS))
        assert DetectionsStage().run(ctx).summary["keyframes"] == 1
        view = subjects.get_subjects(_container(settings, db), video.id)
        assert [f.duplicate_of for f in view.frames] == [None, ids[0]]  # boxes repeated

    def test_an_unreadable_keyframe_is_skipped(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        ids = _keyframes(settings, db, video, 3)
        store = ArtifactStore(settings.artifacts_dir)
        with db.read() as session:
            store.resolve(session.get_one(Keyframe, ids[1]).image_path).unlink()
        ctx = _ctx(settings, db, video, models=FakeStore(settings.models_dir, *MODELS))
        outcome = DetectionsStage().run(ctx)
        assert outcome.status == StageStatus.SUCCEEDED
        assert outcome.summary["unreadable"] == 1
        assert {r.keyframe_id for r in _rows(db)} == {ids[0], ids[2]}

    def test_missing_models(self, settings: Settings, db: Database, video: VideoRef) -> None:
        _keyframes(settings, db, video, 1)
        only_detector = FakeStore(settings.models_dir, "detector/d-fine-s-coco")
        outcome = DetectionsStage().run(_ctx(settings, db, video, models=only_detector))
        assert outcome.status == StageStatus.SKIPPED
        assert "vfe models subjects" in (outcome.skip_reason or "")

    def test_new_keyframes_are_new_facts(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        ids = _keyframes(settings, db, video, 2)
        ctx = _ctx(settings, db, video, models=FakeStore(settings.models_dir, *MODELS))
        DetectionsStage().run(ctx)
        before = DetectionsStage().input_facts(ctx)
        with db.write() as session:
            session.execute(sa.delete(Keyframe).where(Keyframe.id == ids[1]))
        assert DetectionsStage().input_facts(ctx) != before
        assert {r.keyframe_id for r in _rows(db)} == {ids[0]}  # the boxes went with it


# ------------------------------------------------------------------ vision model
@pytest.mark.anyio
class TestGrounding:
    async def test_boxes_come_from_the_loaded_model_and_are_cached(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        ids = _keyframes(settings, db, video, 2)
        lm = FakeLmStudio()
        lm.answers = [json.dumps(GROUNDED)] * 2
        ctx = _ctx(settings, db, video, lmstudio=lm)
        outcome = await GroundingStage().execute(ctx)
        assert outcome.status == StageStatus.SUCCEEDED
        assert outcome.summary["located"] == 2
        assert outcome.summary["boxes"] == 6
        request = lm.chat_requests[0]
        assert request["model"] == "qwen/qwen3-vl-8b"  # the loaded instance, never another
        assert request["response_format"]["json_schema"]["schema"]["required"] == ["beings"]
        assert "French" in request["messages"][0]["content"]
        rows = [r for r in _rows(db, "vlm") if r.keyframe_id == ids[0]]
        assert [(r.label, r.category, r.main) for r in rows] == [
            ("chat", "mammal", True),
            ("femme", "person", False),
            ("abeille", "insect", False),
        ]
        assert rows[2].box == [0.45, 0.1, 0.48, 0.14]
        with db.read() as session:
            scans = session.execute(sa.select(SubjectScan)).scalars().all()
        assert [(s.source, s.found) for s in scans] == [("vlm", 3), ("vlm", 3)]
        with db.read() as session:
            purposes = session.execute(sa.select(LlmCall.purpose)).scalars().all()
        assert set(purposes) == {"subjects"}

        again = await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        assert again.summary["cached"] == 2
        assert len(lm.chat_requests) == 2  # no new request: same images, same prompt

    async def test_lm_studio_down_keeps_the_previous_boxes(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        _keyframes(settings, db, video, 1)
        lm = FakeLmStudio()
        lm.answers = [json.dumps(GROUNDED)]
        await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        lm.down = True
        outcome = await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        assert outcome.status == StageStatus.SKIPPED
        assert outcome.retryable
        assert len(_rows(db, "vlm")) == 3

    async def test_cancelling_stops_the_requests_waiting_for_their_turn(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        _keyframes(settings, db, video, 12)

        class CancelOnFirst(FakeLmStudio):
            token: CancelToken | None = None

            def handler(self, request: Any) -> Any:
                if request.url.path == "/v1/chat/completions" and self.token is not None:
                    self.token.cancel()  # the user cancels while the first frames are asked
                return super().handler(request)

        lm = CancelOnFirst()
        ctx = _ctx(settings, db, video, lmstudio=lm)
        lm.token = ctx.cancel
        with pytest.raises(CancelledError):
            await GroundingStage().execute(ctx)
        assert 1 <= len(lm.chat_requests) <= 4  # the loaded slots at most, not the 12 frames
        assert _rows(db, "vlm") == []  # nothing half-written

    async def test_a_missing_keyframe_fails_alone(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        ids = _keyframes(settings, db, video, 3)
        store = ArtifactStore(settings.artifacts_dir)
        with db.read() as session:
            store.resolve(session.get_one(Keyframe, ids[1]).image_path).unlink()
        lm = FakeLmStudio()
        lm.answers = [json.dumps(GROUNDED)] * 2
        outcome = await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        assert outcome.status == StageStatus.SUCCEEDED
        # The two readable frames are the same image: the second may come from the cache.
        assert outcome.summary["located"] + outcome.summary["cached"] == 2
        assert outcome.summary["failed"] == 1
        assert 1 <= len(lm.chat_requests) <= 2

    async def test_a_model_that_cannot_be_measured_is_not_asked(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        _keyframes(settings, db, video, 1)
        lm = OtherModel()  # answers the probe with something else: no usable measure
        outcome = await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        assert outcome.status == StageStatus.SKIPPED
        assert outcome.retryable  # measured again next time
        assert "Calibrage" in (outcome.skip_reason or "")
        assert all(is_probe(body) for body in lm.chat_requests)  # never asked for beings

    async def test_a_y_first_model_is_measured_once_then_read_correctly(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        _keyframes(settings, db, video, 1)
        lm = GemmaLike()
        outcome = await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        assert outcome.status == StageStatus.SUCCEEDED
        probes = [body for body in lm.chat_requests if is_probe(body)]
        assert len(probes) == 4  # bbox_2d round, then the box_2d round that matches its order
        [row] = _rows(db, "vlm")
        assert row.box == [0.2, 0.1, 0.6, 0.5]  # its [y1, x1, y2, x2] read as such
        grounding = lm.chat_requests[-1]
        assert "box_2d: [y1, x1, y2, x2]" in grounding["messages"][1]["content"][0]["text"]

        lm.chat_requests.clear()
        again = await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        assert again.status == StageStatus.SUCCEEDED
        assert not any(is_probe(body) for body in lm.chat_requests)  # the profile is kept

    async def test_a_measured_convention_is_always_in_the_key(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        # Qwen3-VL (a prior): the key is the one it had before profiles, to the character.
        qwen = await GroundingStage().resolve_config(_ctx(settings, db, video))
        assert qwen == {"model": "qwen/qwen3-vl-8b"}
        # Another model measured with the same convention: never the key of its old
        # « convention inconnue » skip, so that skip is not served from the cache.
        other = await GroundingStage().resolve_config(
            _ctx(settings, db, video, lmstudio=XFirstOther())
        )
        assert other == {
            "model": "google/gemma-4-12b",
            "convention": "xyxy_1000",
            "box_field": "bbox_2d",
        }

    async def test_cancelling_during_the_calibration(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        _keyframes(settings, db, video, 1)

        class CancelOnProbe(GemmaLike):
            token: CancelToken | None = None

            def handler(self, request: Any) -> Any:
                if request.url.path == "/v1/chat/completions" and self.token is not None:
                    self.token.cancel()
                return super().handler(request)

        lm = CancelOnProbe()
        ctx = _ctx(settings, db, video, lmstudio=lm)
        lm.token = ctx.cancel
        with pytest.raises(CancelledError):  # the job is cancelled, not a retryable skip
            await GroundingStage().execute(ctx)
        assert len(lm.chat_requests) <= 2
        assert all(is_probe(body) for body in lm.chat_requests)

    async def test_reasoning_tokens_are_reported(
        self, settings: Settings, db: Database, video: VideoRef
    ) -> None:
        _keyframes(settings, db, video, 1)
        lm = FakeLmStudio()
        lm.answers = [json.dumps(GROUNDED)]
        lm.reasoning_tokens = 120  # the model reasoned despite reasoning_off
        outcome = await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm))
        assert outcome.summary["reasoning_tokens"] == 120


# ------------------------------------------------------------------ API and MCP
@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


async def _both(settings: Settings, db: Database, video: VideoRef) -> list[str]:
    ids = _keyframes(settings, db, video, 2)
    ctx = _ctx(settings, db, video, models=FakeStore(settings.models_dir, *MODELS))
    _record(db, video.id, "detections", DetectionsStage().run(ctx))
    lm = FakeLmStudio()
    lm.answers = [json.dumps(GROUNDED)] * 2
    _record(
        db,
        video.id,
        "grounding",
        await GroundingStage().execute(_ctx(settings, db, video, lmstudio=lm)),
    )
    return ids


@pytest.mark.anyio
@pytest.mark.usefixtures("fake_detectors")
async def test_api_returns_fused_subjects(
    settings: Settings, db: Database, video: VideoRef, client: TestClient
) -> None:
    ids = await _both(settings, db, video)
    body = client.get(f"/api/v1/videos/{video.id}/subjects").json()
    assert (body["status"], body["run_status"]) == ("ready", "ready")
    assert (body["width"], body["height"]) == (1920, 1080)
    first = body["frames"][0]
    assert first["keyframe_id"] == ids[0]
    labels = [(s["label"], s["main"], s["sources"]) for s in first["subjects"]]
    assert labels == [
        ("chat", True, ["vlm", "detector"]),  # the VLM's name and main flag, the detector box
        ("femme", False, ["vlm", "detector", "faces"]),
        ("abeille", False, ["vlm"]),  # only the vision model sees insects
    ]  # the face alone at the right edge is dropped: the vision model saw nobody there
    person = first["subjects"][1]
    assert person["box"] == [0.1, 0.1, 0.4, 0.9]
    assert person["face_box"] == [0.2, 0.12, 0.3, 0.25]
    assert person["face_points"][0] == [0.23, 0.16]
    assert person["score"] == 0.92


@pytest.mark.anyio
@pytest.mark.usefixtures("fake_detectors")
async def test_mcp_locations_and_manifest_line(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    await _both(settings, db, video)
    c = _container(settings, db)
    view = subjects.get_subjects(c, video.id)
    with db.read() as session:
        row = session.get_one(Video, video.id)
        session.expunge(row)
    located = _locations(row, view, categories=None, main_only=False, max_frames=10)
    assert located.status == "ready"
    assert len(located.frames) == 2
    cat = located.frames[0].subjects[0]
    assert cat.box_px == [1152, 648, 1536, 864]  # 1920×1080
    only_main = _locations(row, view, categories=None, main_only=True, max_frames=10)
    assert [len(f.subjects) for f in only_main.frames] == [1, 1]
    insects = _locations(row, view, categories=["insect"], main_only=False, max_frames=1)
    assert [s.label for f in insects.frames for s in f.subjects] == ["abeille"]
    assert len(insects.frames) == 1  # max_frames
    line = subjects_summary(view)
    assert line is not None
    assert line.startswith("abeille, chat, femme — sur 2 images distinctes")


@pytest.mark.models
def test_real_detectors_run_on_the_cpu(settings: Settings) -> None:
    """The installed D-FINE and YuNet load on the CPU and answer on a plain image."""
    from vfe_vision.adapters.detection.dfine import DFine
    from vfe_vision.adapters.detection.yunet import FaceDetector
    from vfe_vision.adapters.models.catalog import spec
    from vfe_vision.adapters.models.store import ModelStore
    from vfe_vision.core.config import default_data_dir
    from vfe_vision.domain.subjects import COCO_LIVING

    store = ModelStore(default_data_dir() / "models")
    detector_dir = store.installed(spec("detector/d-fine-s-coco"))
    faces_dir = store.installed(spec("faces/yunet"))
    if detector_dir is None or faces_dir is None:
        pytest.skip("vfe models subjects")
    image = np.full((720, 1280, 3), 128, np.uint8)
    detector = DFine.from_dir(detector_dir, threads=2)
    assert detector.providers == ("CPUExecutionProvider",)
    assert len(detector.labels) == 80
    assert detector.detect(image, keep=COCO_LIVING, min_score=0.5) == []
    assert FaceDetector.from_file(faces_dir / "yunet.onnx").detect(image) == []


@pytest.mark.anyio
@pytest.mark.usefixtures("fake_detectors")
async def test_spans_empty_frames_and_duplicates(
    settings: Settings, db: Database, video: VideoRef
) -> None:
    """A keyframe stands for [t_s, next keyframe): an interval between two keyframes gets the
    earlier one; a frame looked at with nobody there is listed empty; a repeated image only
    extends the span of its original."""
    ids = _keyframes(settings, db, video, 4)  # at 0, 2, 4 and 6 s
    store = ArtifactStore(settings.artifacts_dir)
    with db.write() as session:
        session.get_one(Keyframe, ids[1]).duplicate_of = ids[0]
        empty = store.resolve(session.get_one(Keyframe, ids[2]).image_path)
    write_jpeg(empty, np.zeros((240, 320, 3), np.uint8))  # the vision model sees nobody here
    ctx = _ctx(settings, db, video, models=FakeStore(settings.models_dir, *MODELS))
    DetectionsStage().run(ctx)

    class ByImage(FakeLmStudio):
        """Answers by the image received, whatever the order of the requests."""

        def handler(self, request: Any) -> Any:
            if request.url.path == "/v1/chat/completions":
                body = json.loads(request.content)
                url = body["messages"][1]["content"][1]["image_url"]["url"]
                data = np.frombuffer(base64.b64decode(url.split(",", 1)[1]), np.uint8)
                gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
                dark = gray is not None and float(gray.mean()) < 128
                self.answers = [json.dumps({"beings": []} if dark else GROUNDED)]
            return super().handler(request)

    await GroundingStage().execute(_ctx(settings, db, video, lmstudio=ByImage()))
    c = _container(settings, db)

    inside = subjects.get_subjects(c, video.id, start_s=4.5, end_s=5.5)
    assert [(f.t_s, f.until_s) for f in inside.frames] == [(4.0, 6.0)]
    assert inside.frames[0].subjects == []  # looked at, nobody there (the detector's boxes
    # were not confirmed by the vision model)

    view = subjects.get_subjects(c, video.id)
    with db.read() as session:
        row = session.get_one(Video, video.id)
        session.expunge(row)
    located = _locations(row, view, categories=None, main_only=False, max_frames=10)
    assert [(f.keyframe, f.t_s, f.until_s) for f in located.frames] == [
        (1, 0.0, 4.0), (3, 4.0, 6.0), (4, 6.0, 6.0)
    ]  # fmt: skip
    assert (located.total_frames, located.sampled) == (3, False)
    faces = _locations(row, view, categories=["face"], main_only=False, max_frames=10)
    assert {s.category for f in faces.frames for s in f.subjects} == {"person"}
    assert all(s.face_box for f in faces.frames for s in f.subjects)
    sampled = _locations(row, view, categories=None, main_only=False, max_frames=2)
    assert (sampled.total_frames, sampled.sampled, len(sampled.frames)) == (3, True, 2)
