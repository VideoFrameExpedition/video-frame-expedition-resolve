"""FastAPI application factory."""

from __future__ import annotations

import contextlib
import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vfe_vision import __version__
from vfe_vision.api.access import AccessConfig, AccessGuard
from vfe_vision.api.errors import install_error_handlers
from vfe_vision.api.routers import access as access_routes
from vfe_vision.api.routers import (
    ask,
    bench,
    events,
    jobs,
    library,
    media,
    resolve,
    search,
    system,
    videos,
)
from vfe_vision.api.routers import settings as settings_routes
from vfe_vision.api.schemas import API_PREFIX
from vfe_vision.api.security import FrameGuardMiddleware, LocalSecurityMiddleware, allowed_origins
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import NotFoundError
from vfe_vision.core.logging import get_logger
from vfe_vision.core.paths import joined_inside
from vfe_vision.db.migrate import upgrade_database
from vfe_vision.jobs.supervisor import WorkerSupervisor
from vfe_vision.mcp.server import build_mcp_server
from vfe_vision.services.container import AppContainer

log = get_logger(__name__)
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

# Python reads MIME types from the Windows registry, which often lacks these two: declaring
# them here avoids serving the help page's screenshots as « application/octet-stream ».
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")


def create_app(
    settings: Settings, *, start_worker: bool | None = None, access: AccessConfig | None = None
) -> FastAPI:
    """The whole application; ``access`` says where it listens beyond the loopback and which
    token other devices need (``vfe serve`` builds it, loopback only by default)."""
    run_worker = settings.worker_enabled if start_worker is None else start_worker
    access = access or AccessConfig.local(settings)
    guard = AccessGuard(access)
    holder: dict[str, AppContainer] = {}
    mcp = build_mcp_server(lambda: holder["container"])
    hosts = sorted({*access.trusted_hosts(), settings.host})
    origins = allowed_origins(settings.port, access.remote_origins())
    mcp_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{h}:{settings.port}" for h in hosts] + hosts,
        allowed_origins=sorted(origins),
    )
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=mcp_security)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        await anyio.to_thread.run_sync(upgrade_database, settings.db_path, settings.backups_dir)
        container = AppContainer.create(settings)
        holder["container"] = container
        app.state.container = container
        supervisor = WorkerSupervisor(settings) if run_worker else None
        if supervisor is not None:
            await anyio.to_thread.run_sync(supervisor.start)
            container.worker_pid = supervisor.pid
        log.info(
            "vfe-vision ready", url=settings.base_url, remote=access.remote_urls(),
            worker=run_worker, version=__version__,
        )  # fmt: skip
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if supervisor is not None:
                await anyio.to_thread.run_sync(supervisor.stop)
            await container.aclose()

    app = FastAPI(
        title="Video Frame Expedition for DaVinci Resolve",
        version=__version__,
        description="Local video analysis API (LM Studio vision model).",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    install_error_handlers(app)
    app.state.access_guard = guard
    app.add_middleware(LocalSecurityMiddleware, origins=origins, guard=guard)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(FrameGuardMiddleware)

    api = APIRouter(prefix=API_PREFIX)
    modules = (
        system, library, videos, search, ask, jobs, bench, media, events, settings_routes, resolve
    )  # fmt: skip
    for module in modules:
        api.include_router(module.router)
    api.include_router(access_routes.router)
    app.include_router(api)
    for route in mcp_app.routes:
        app.router.routes.append(route)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built single-page application (``just build``) with client-side routing."""
    index = WEB_DIST / "index.html"
    if not index.is_file():

        @app.get("/", include_in_schema=False)
        def no_frontend() -> JSONResponse:
            message = "Interface non construite : lancez `just build`, ou `just dev-frontend`."
            return JSONResponse({"message": message, "api": f"{API_PREFIX}/docs"})

        return
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "mcp", ".well-known/")):  # never the page for those
            raise NotFoundError("Ressource introuvable")
        joined = joined_inside(WEB_DIST, full_path) if full_path else None
        if joined is not None:  # decided on the text first: a network path is never looked at
            candidate = joined.resolve()
            if candidate.is_relative_to(WEB_DIST) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
