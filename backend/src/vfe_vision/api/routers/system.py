"""System endpoints: health, diagnostics, LM Studio catalogue, the database as a whole
(export, import, reset) and the restart of the application."""

from __future__ import annotations

import threading
from functools import partial
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, BackgroundTasks, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from vfe_vision import __version__
from vfe_vision.adapters.folder_picker import pick_folder
from vfe_vision.adapters.lmstudio.catalog import ModelInfo
from vfe_vision.api.deps import Container
from vfe_vision.api.schemas import HealthOut, JobOut, PickedFolderOut, StageOut
from vfe_vision.api.server import Server
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import is_loopback, platform_name
from vfe_vision.core.errors import CancelledError, ConflictError, PathNotAllowedError, VfeError
from vfe_vision.services import analysis, database, system
from vfe_vision.services import settings as settings_service

router = APIRouter(prefix="/system", tags=["system"])
_picker = threading.Lock()  # one folder dialog at a time
DISCONNECT_POLL_S = 0.5


class RestartOut(BaseModel):
    restarting: bool


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


# ---------------------------------------------------------------- the database as a whole


@router.get("/data")
def data(request: Request, c: Container) -> database.DataOut:
    """The size of the database and of the frames, and the import or the reset waiting for the
    next start (``POST /system/restart``)."""
    return database.status(c, can_restart=_server(request) is not None)


@router.post("/data/export")
async def export_data(c: Container, choice: database.ExportChoice) -> database.ExportOut:
    """Prepare an archive of the database (and, at will, of the frames); its link downloads
    it once."""
    return await anyio.to_thread.run_sync(database.export, c, choice)


@router.get("/data/export/{token}", response_class=FileResponse)
def download_export(c: Container, token: str) -> FileResponse:
    path, name = database.export_file(c, token)
    return FileResponse(
        path, media_type="application/zip", filename=name,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )  # fmt: skip


BINARY = {"schema": {"type": "string", "format": "binary"}}


@router.post(
    "/data/import",
    openapi_extra={
        "requestBody": {"required": True, "content": {"application/octet-stream": BINARY}}
    },
)
async def import_data(
    request: Request,
    c: Container,
    name: Annotated[str | None, Query(max_length=255)] = None,
    keep_settings: bool = True,
) -> database.PendingOut:
    """An export (or a database of the backups folder) as the body of the request: checked,
    it replaces the library at the next start; ``keep_settings`` keeps this computer's."""
    return await database.import_file(c, request.stream(), name=name, keep_settings=keep_settings)


@router.post("/data/reset")
def reset_data(c: Container, choice: database.ResetChoice) -> database.PendingOut:
    """Erase the library, the settings, or both, at the next start."""
    return database.reset(c, choice)


@router.delete("/data/pending", status_code=status.HTTP_204_NO_CONTENT)
def cancel_data(c: Container) -> None:
    """Drop the import or the reset waiting for the next start."""
    database.cancel(c)


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
def restart(request: Request, background: BackgroundTasks) -> RestartOut:
    """Stop the application as Ctrl+C does (analyses back in the queue), then start it again
    in its window: its launcher (run.bat, run.command) does."""
    server = _server(request)
    if server is None:
        raise ConflictError(
            "L'application ne peut pas redémarrer d'elle-même ici : arrêtez-la, puis relancez-la."
        )
    background.add_task(server.restart)  # once the answer is sent
    return RestartOut(restarting=True)


def _server(request: Request) -> Server | None:
    server: Server | None = getattr(request.app.state, "server", None)
    return server
