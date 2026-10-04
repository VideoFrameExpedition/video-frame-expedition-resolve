"""Calibration of the loaded vision model: status, doctor, API, CLI and worker."""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import partial
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import MODELS_PAYLOAD, FakeLmStudio
from tests.fakes.vision_models import GemmaLike, OtherModel, Reasoner
from vfe_vision import cli
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.adapters.lmstudio.catalog import pick_vision_instance
from vfe_vision.api.app import create_app
from vfe_vision.cli import _Calibration, _vision_lines
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import CancelledError, ConflictError, ServiceUnavailableError
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus
from vfe_vision.domain.vision_profile import BoxConvention, BoxField
from vfe_vision.jobs import queue
from vfe_vision.jobs.worker import LIGHT_KINDS, Worker
from vfe_vision.pipeline import vision_profile
from vfe_vision.pipeline.stage import Toolbox
from vfe_vision.services import system
from vfe_vision.services.container import AppContainer
from vfe_vision.storage.artifacts import ArtifactStore

pytestmark = pytest.mark.anyio


def _container(settings: Settings, db: Database, lm: FakeLmStudio) -> AppContainer:
    return AppContainer(
        settings=settings,
        db=db,
        artifacts=ArtifactStore(settings.artifacts_dir),
        ffmpeg=Ffmpeg(settings.ffmpeg_path, settings.ffprobe_path),
        lmstudio=lm.client(),
    )


async def test_qwen_is_known_without_a_probe(settings: Settings, db: Database) -> None:
    c = _container(settings, db, FakeLmStudio())
    status = await system.vision_status(c)
    assert (status.model, status.prior, status.profile) == ("qwen/qwen3-vl-8b", True, None)
    check = system._vision_profile_check(c, (await c.lmstudio.list_models())[0])
    assert "convention connue" in check.detail


async def test_a_measured_model_is_reported_and_used(settings: Settings, db: Database) -> None:
    lm = GemmaLike()
    c = _container(settings, db, lm)
    status = await system.vision_status(c)
    assert not status.prior
    assert status.profile is None
    [model] = [m for m in await c.lmstudio.list_models() if m.vision]
    assert system._vision_profile_check(c, model).status == system.CheckStatus.WARNING

    tools = cast(
        Toolbox,
        SimpleNamespace(db=db, lmstudio=c.lmstudio, lm_budget=TokenBudget(8192, 1)),
    )
    message = await Worker._probe_vision(tools, CancelToken())  # the « Recalibrate » job
    assert "positions vérifiées" in message
    status = await system.vision_status(c)
    assert status.profile is not None
    grounding = status.profile.grounding
    assert (grounding.convention, grounding.box_field) == (
        BoxConvention.YXYX_1000,
        BoxField.BOX_2D,
    )
    check = system._vision_profile_check(c, model)
    assert check.status == system.CheckStatus.OK
    assert "[y1, x1, y2, x2] sur 0-1000 (box_2d)" in check.detail
    setup = vision_profile.known_setup(db, model)
    assert setup is not None
    assert setup.source == "profile"

    lines = _vision_lines(status)
    assert lines[0].startswith("Modèle de vision : ")
    assert any(line.startswith("Positions : vérifiées") for line in lines)
    assert lines[-1].startswith("Conventions : yxyx_1000")

    job = await system.request_probe(c)
    assert job.kind == JobKind.PROBE_VISION
    claimed = queue.claim_next(db, 1)
    assert claimed is not None
    assert claimed.id == job.id
    again = await system.request_probe(c)  # a second click while it runs: the same job
    assert again.id == job.id
    assert again.status == JobStatus.RUNNING


async def test_nothing_to_calibrate_without_a_loaded_model(
    settings: Settings, db: Database
) -> None:
    lm = FakeLmStudio()
    lm.down = True
    c = _container(settings, db, lm)
    status = await system.vision_status(c)
    assert status.model is None
    assert status.lmstudio_error  # start LM Studio, rather than load a model
    with pytest.raises(ServiceUnavailableError):
        await system.request_probe(c)

    class NoVision(FakeLmStudio):
        def handler(self, request: Any) -> Any:
            if request.url.path == "/api/v1/models":
                payload = json.loads(json.dumps(MODELS_PAYLOAD))
                for model in payload["models"]:
                    model["loaded_instances"] = []
                return httpx.Response(200, json=payload)
            return super().handler(request)

    c = _container(settings, db, NoVision())
    status = await system.vision_status(c)
    assert (status.model, status.lmstudio_error) == (None, None)
    with pytest.raises(ConflictError):
        await system.request_probe(c)


async def _loaded(c: AppContainer) -> Any:
    picked = pick_vision_instance(await c.lmstudio.list_models(), None)
    assert picked is not None
    return picked


async def test_reasoning_that_eats_the_budget_is_reported(settings: Settings, db: Database) -> None:
    lm = Reasoner()
    c = _container(settings, db, lm)
    model, instance = await _loaded(c)
    profile = await vision_profile.probe(c.lmstudio, TokenBudget(8192, 1), model, instance)
    assert profile.truncated
    assert profile.reasoning_tokens_seen == 2 * (600 + 1500)  # cut answers count too
    assert "raisonne" in (profile.grounding.reason or "")
    # No box_2d round: another field name would not stop the reasoning.
    assert len(lm.chat_requests) == 4
    vision_profile.store_profile(db, profile)
    check = system._vision_profile_check(c, model)
    assert "raisonne" in check.detail
    assert "Coupez le raisonnement" in (check.hint or "")


async def test_cancelling_a_calibration_stops_it(settings: Settings, db: Database) -> None:
    token = CancelToken()

    class CancelOnFirst(GemmaLike):
        def handler(self, request: Any) -> Any:
            if request.url.path == "/v1/chat/completions":
                token.cancel()  # the user cancels while the probe is asked
            return super().handler(request)

    lm = CancelOnFirst()
    c = _container(settings, db, lm)
    model, instance = await _loaded(c)
    with pytest.raises(CancelledError):
        await vision_profile.measure(
            db, c.lmstudio, TokenBudget(8192, 1), model, instance, cancel=token
        )
    assert len(lm.chat_requests) <= 2  # never the box_2d round
    assert vision_profile.stored_profile(db, model) is None  # nothing half-measured is kept


def test_a_calibration_never_waits_for_a_video_slot(db: Database) -> None:
    job = queue.enqueue(db, JobKind.PROBE_VISION, priority=10)
    assert queue.claim_next(db, 1, kinds=set()) is None
    claimed = queue.claim_next(db, 1, kinds=LIGHT_KINDS)
    assert claimed is not None
    assert claimed.id == job.id
    # While it runs, a new request is queued once, then merged into that queued job.
    second = queue.enqueue(db, JobKind.PROBE_VISION, priority=10)
    third = queue.enqueue(db, JobKind.PROBE_VISION, priority=10)
    assert second.id != job.id
    assert third.id == second.id


def _app(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> list[str]:
    """The running app, as the CLI sees it through HTTP."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        seen.append(f"{request.method} {path}")
        answer = routes[path]
        return httpx.Response(200, json=answer() if callable(answer) else answer)

    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(handler))
    )
    return seen


NOT_CALIBRATED = {"model": "google/gemma-4-12b", "display_name": "Gemma 4", "profile": None}


def test_doctor_reports_a_stopped_worker(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _app(
        monkeypatch,
        {"/system/health": {"worker_pid": None}, "/system/vision-profile": NOT_CALIBRATED},
    )
    calibration = cli._vision_doctor(settings, force=False)
    assert "travailleur" in (calibration.error or "")
    assert not calibration.ok
    assert "POST /system/vision-profile/probe" not in seen
    assert capsys.readouterr().out == ""


def test_doctor_reports_a_calibration_still_waiting_or_failed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = {"status": "queued", "error": None}
    _app(
        monkeypatch,
        {
            "/system/health": {"worker_pid": 42},
            "/system/vision-profile": NOT_CALIBRATED,
            "/system/vision-profile/probe": {"id": "j1"},
            "/jobs/j1": lambda: state,
        },
    )
    monkeypatch.setattr(cli, "PROBE_WAIT_S", 0)
    waiting = cli._vision_doctor(settings, force=True)
    assert "toujours en attente" in (waiting.error or "")
    assert "j1" in (waiting.error or "")

    monkeypatch.setattr(cli, "PROBE_WAIT_S", 60)
    state.update(status="failed", error="LM Studio injoignable")
    failed = cli._vision_doctor(settings, force=True)
    assert failed.error == "Calibrage en échec : LM Studio injoignable"
    assert not failed.ok
    out = capsys.readouterr()
    assert out.out == ""  # stdout stays parsable with --json: progress goes to stderr
    assert "Calibrage de Gemma 4" in out.err


async def test_inline_doctor_reports_a_failed_probe(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Unreadable(OtherModel):
        def handler(self, request: Any) -> Any:
            if request.url.path == "/v1/chat/completions":
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "not json"},
                                       "finish_reason": "stop"}]},
                )  # fmt: skip
            return super().handler(request)

    monkeypatch.setattr(
        AppContainer, "create", staticmethod(lambda _s: _container(settings, db, Unreadable()))
    )
    calibration = await cli._vision_inline(settings, True)
    assert (calibration.error or "").startswith("Calibrage en échec : ")
    assert calibration.status.model == "google/gemma-4-12b"
    assert not calibration.ok


def test_one_exit_rule_for_text_and_json() -> None:
    status = system.VisionStatus(model="m")
    assert not _Calibration(status, None).ok  # nothing measured
    assert not _Calibration(status, "Calibrage en échec : x").ok


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


def test_api_refuses_a_probe_without_lm_studio(client: TestClient) -> None:
    status: dict[str, Any] = client.get("/api/v1/system/vision-profile").json()
    assert status["model"] is None  # LM Studio unreachable in tests
    assert status["lmstudio_error"]
    refused = client.post("/api/v1/system/vision-profile/probe", headers={"X-VFE-Client": "test"})
    assert refused.status_code == 503
