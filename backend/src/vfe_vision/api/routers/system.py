"""System endpoints: health, diagnostics, LM Studio catalogue."""

from __future__ import annotations

import threading
from functools import partial
from pathlib import Path

import anyio
from fastapi import APIRouter, Request, status

from vfe_vision import __version__
from vfe_vision.adapters.folder_picker import pick_folder
from vfe_vision.adapters.lmstudio.catalog import ModelInfo
from vfe_vision.api.deps import Container
from vfe_vision.api.schemas import HealthOut, JobOut, PickedFolderOut, StageOut
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import is_loopback, platform_name
from vfe_vision.core.errors import CancelledError, ConflictError, PathNotAllowedError, VfeError
from vfe_vision.services import analysis, system
from vfe_vision.services import settings as settings_service

router = APIRouter(prefix="/system", tags=["system"])
_picker = threading.Lock()  # one folder dialog at a time
DISCONNECT_POLL_S = 0.5


@router.get("/health")
def health(c: Container) -> HealthOut:
    return HealthOut(
        status="ok", version=__version__, worker_pid=c.worker_pid, platform=platform_name()
    )


@router.get("/doctor")
async def doctor(c: Container) -> system.DoctorReport:
    return await system.doctor(c)


@router.get("/stages")
def stages() -> list[StageOut]:
    """The analysis stages, in their order of execution, with their dependencies."""
    return [StageOut.of(stage) for stage in analysis.list_stages()]


@router.get("/vision-profile")
async def vision_profile(c: Container) -> system.VisionStatus:
    """The loaded vision model and how it writes its positions (measured or known profile)."""
    return await system.vision_status(c)


@router.post("/vision-profile/probe", status_code=status.HTTP_202_ACCEPTED)
async def probe_vision(c: Container) -> JobOut:
    """Recalibrate the loaded vision model (a few seconds; 409 without a loaded model)."""
    return JobOut.of(await system.request_probe(c))


@router.post("/pick-folder")
async def choose_folder(request: Request) -> PickedFolderOut:
    """Open the desktop's folder picker on the application's computer (for « Add a folder »);
    reserved for a browser open on this computer."""
    if request.client is None or not is_loopback(request.client.host):
        raise PathNotAllowedError("Le choix de dossier ne s'ouvre que sur l'ordinateur local.")
    if not _picker.acquire(blocking=False):
        raise ConflictError("Une fenêtre de choix de dossier est déjà ouverte.")
    token = CancelToken()
    picked: Path | None = None
    failure: VfeError | None = None
    try:
        async with anyio.create_task_group() as group:
            group.start_soon(_close_when_gone, request, token)
            try:
                picked = await anyio.to_thread.run_sync(
                    partial(pick_folder, "Choisir un dossier de vidéos", cancel=token),
                    abandon_on_cancel=True,  # the app stopping does not wait for the dialog
                )
            except CancelledError:  # the page that asked is gone
                picked = None
            except VfeError as exc:  # raised after the group, not as an ExceptionGroup
                failure = exc
            group.cancel_scope.cancel()
    finally:
        token.cancel("Requête terminée")  # closes a dialog left open by an interrupted request
        _picker.release()
    if failure is not None:
        raise failure
    return PickedFolderOut(path=str(picked) if picked else None)


async def _close_when_gone(request: Request, token: CancelToken) -> None:
    """Closing or reloading the tab closes the dialog, and frees the way for the next one."""
    while not await request.is_disconnected():  # noqa: ASYNC110 - no event to wait on
        await anyio.sleep(DISCONNECT_POLL_S)
    token.cancel("Page fermée")


@router.get("/lmstudio/models")
async def lmstudio_models(c: Container) -> list[ModelInfo]:
    return await settings_service.list_models(c)
