"""Command-line interface (``vfe``)."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from vfe_vision import __version__
from vfe_vision.core.language import terminal_language, tr

if TYPE_CHECKING:
    from vfe_vision.core.config import Settings
    from vfe_vision.services.system import VisionStatus

app = typer.Typer(
    name="vfe",
    help=tr(
        "Video Frame Expedition for DaVinci Resolve — analyse locale de vidéos.",
        "Video Frame Expedition for DaVinci Resolve — local video analysis.",
    ),
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def root() -> None:
    """Video Frame Expedition for DaVinci Resolve — analyse locale de vidéos."""


@app.command(help=tr("Affiche la version de l'application.", "Shows the application's version."))
def version() -> None:
    """Affiche la version de l'application."""
    typer.echo(__version__)


@app.command(
    help=tr(
        "Lance l'application : API, interface web, serveur MCP et worker d'analyse.\n\n"
        "Avec VFE_TAILSCALE=true (ou VFE_EXTRA_HOSTS), écoute aussi sur l'adresse Tailscale de "
        "cet ordinateur, jeton obligatoire depuis les autres appareils (voir vfe token).",
        "Starts the application: API, web interface, MCP server and analysis worker.\n\n"
        "With VFE_TAILSCALE=true (or VFE_EXTRA_HOSTS), also listens on this computer's "
        "Tailscale address, token required from the other devices (see vfe token).",
    )
)
def serve(
    host: Annotated[
        str | None,
        typer.Option(
            help=tr(
                "Adresse d'écoute (défaut : 127.0.0.1).",
                "Address to listen on (default: 127.0.0.1).",
            )
        ),
    ] = None,
    port: Annotated[
        int | None, typer.Option(help=tr("Port (défaut : 8765).", "Port (default: 8765)."))
    ] = None,
    reload: Annotated[
        bool,
        typer.Option(
            help=tr("Rechargement automatique (développement).", "Automatic reload (development).")
        ),
    ] = False,
    no_worker: Annotated[
        bool,
        typer.Option(
            "--no-worker", help=tr("Ne pas lancer le worker.", "Do not start the worker.")
        ),
    ] = False,
) -> None:
    """Lance l'application : API, interface web, serveur MCP et worker d'analyse.

    Avec VFE_TAILSCALE=true (ou VFE_EXTRA_HOSTS), écoute aussi sur l'adresse Tailscale de cet
    ordinateur, jeton obligatoire depuis les autres appareils (voir ``vfe token``)."""
    import os

    import uvicorn

    from vfe_vision.core.config import get_settings
    from vfe_vision.core.logging import configure_logging

    if host:
        os.environ["VFE_HOST"] = host
    if port:
        os.environ["VFE_PORT"] = str(port)
    if no_worker:
        os.environ["VFE_WORKER_ENABLED"] = "false"
    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        log_dir=settings.logs_dir,
        process_name="api",
    )
    if not reload:
        _serve_listening(settings)
        return
    if settings.tailscale or settings.extra_hosts:
        typer.secho(
            tr(
                "--reload : écoute locale seulement (Tailscale ignoré).",
                "--reload: local listening only (Tailscale ignored).",
            ),
            err=True,
            fg="yellow",
        )
    typer.echo(_started(settings.base_url))
    uvicorn.run(
        "vfe_vision.api.factory:app_from_env",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=True,
        reload_dirs=[str(Path(__file__).parent)],
        log_config=None,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=5,  # Ctrl+C: requests still open (folder dialog) are cut
    )


def _serve_listening(settings: Settings) -> None:
    """One socket per address: the loopback, and this machine's Tailscale IP when asked for.
    An extra address that cannot be listened on is skipped with a notice."""
    import uvicorn

    from vfe_vision.api.app import create_app
    from vfe_vision.api.listen import access_config, open_listeners, plan_listen

    plan = plan_listen(settings)
    try:
        listeners = open_listeners(plan, settings.port)
    except OSError as exc:
        typer.secho(
            tr(
                f"Écoute impossible sur {settings.host}:{settings.port} : {exc.strerror or exc}",
                f"Cannot listen on {settings.host}:{settings.port}: {exc.strerror or exc}",
            ),
            err=True,
            fg="red",
        )
        raise typer.Exit(1) from exc
    access = access_config(settings, plan, listeners)
    typer.echo(_started(settings.base_url))
    for url in access.remote_urls():
        typer.echo(
            tr(
                f"  depuis vos autres appareils → {url}  (MCP : {url}/mcp ; jeton : vfe token)",
                f"  from your other devices → {url}  (MCP: {url}/mcp; token: vfe token)",
            )
        )
    for notice in access.notices:
        typer.secho(f"  {notice}", err=True, fg="yellow")
    config = uvicorn.Config(
        create_app(settings, access=access),
        host=settings.host,
        port=settings.port,
        log_config=None,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=5,  # Ctrl+C: requests still open (folder dialog) are cut
    )
    server = uvicorn.Server(config)
    try:
        server.run(sockets=listeners.sockets)
    finally:
        listeners.close()
    if not server.started:
        raise typer.Exit(3)


def _started(url: str) -> str:
    return tr(
        f"Video Frame Expedition {__version__} → {url}  (MCP : {url}/mcp)",
        f"Video Frame Expedition {__version__} → {url}  (MCP: {url}/mcp)",
    )


@app.command(
    help=tr(
        "Affiche le jeton d'API demandé aux autres appareils (le crée s'il n'existe pas encore)."
        "\n\nLe jeton seul va sur la sortie standard ; les explications sur la sortie d'erreur.",
        "Shows the API token asked of the other devices (creates it when there is none yet)."
        "\n\nThe token alone goes to the standard output; the explanations to the error output.",
    )
)
def token(
    rotate: Annotated[
        bool,
        typer.Option(
            "--rotate",
            help=tr(
                "Remplace le jeton (les sessions ouvertes tombent).",
                "Replaces the token (open sessions are dropped).",
            ),
        ),
    ] = False,
) -> None:
    """Affiche le jeton d'API demandé aux autres appareils (le crée s'il n'existe pas encore).

    Le jeton seul va sur la sortie standard ; les explications sur la sortie d'erreur."""
    from vfe_vision.core.config import get_settings
    from vfe_vision.core.logging import configure_logging
    from vfe_vision.core.tokens import current_token, write_new_token

    configure_logging(level="WARNING")  # stderr: stdout carries the token alone
    settings = get_settings()
    if rotate:
        if settings.api_token is not None:
            typer.echo(
                tr(
                    "Le jeton vient de VFE_API_TOKEN : changez-le à cet endroit.",
                    "The token comes from VFE_API_TOKEN: change it there.",
                ),
                err=True,
            )
            raise typer.Exit(1)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fresh = write_new_token(settings.api_token_path)
        typer.echo(fresh)
        typer.echo(
            tr(
                f"Nouveau jeton enregistré dans {settings.api_token_path}. Redémarrez "
                "l'application pour l'appliquer ; les appareils connectés devront se reconnecter.",
                f"New token saved in {settings.api_token_path}. Restart the application to apply "
                "it; the connected devices will have to connect again.",
            ),
            err=True,
        )
        return
    value, source = current_token(settings, create=True)
    typer.echo(value)
    where = "VFE_API_TOKEN" if source == "env" else str(settings.api_token_path)
    typer.echo(
        tr(
            f"Jeton d'accès depuis les autres appareils ({where}).",
            f"Access token for the other devices ({where}).",
        ),
        err=True,
    )


@app.command(
    "mcp-stdio",
    help=tr(
        "Pont MCP stdio → HTTP pour les clients qui lancent une commande (Claude Desktop).\n\n"
        "Relaie les messages vers /mcp de l'application déjà lancée, sans jamais la démarrer ; "
        "la sortie standard ne porte que le protocole (journal sur la sortie d'erreur).",
        "MCP bridge from stdio to HTTP, for the clients that start a command (Claude Desktop)."
        "\n\nRelays the messages to /mcp of the application already running, without ever "
        "starting it; the standard output carries the protocol only (log on the error output).",
    ),
)
def mcp_stdio(
    url: Annotated[
        str | None,
        typer.Option(
            help=tr(
                "Adresse MCP de l'application (défaut : http://127.0.0.1:8765/mcp).",
                "The application's MCP address (default: http://127.0.0.1:8765/mcp).",
            )
        ),
    ] = None,
    token: Annotated[
        str | None,
        typer.Option(
            envvar="VFE_MCP_TOKEN",
            show_default=False,
            help=tr(
                "Jeton d'API, pour une application sur un autre appareil (ou VFE_MCP_TOKEN).",
                "API token, for an application on another device (or VFE_MCP_TOKEN).",
            ),
        ),
    ] = None,
) -> None:
    """Pont MCP stdio → HTTP pour les clients qui lancent une commande (Claude Desktop).

    Relaie les messages vers /mcp de l'application déjà lancée, sans jamais la démarrer ; la
    sortie standard ne porte que le protocole (journal sur la sortie d'erreur)."""
    import logging
    import os
    import sys

    import anyio

    from vfe_vision.mcp.stdio_bridge import serve_stdio

    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="vfe mcp-stdio: %(levelname)s %(message)s"
    )
    target = url or f"http://127.0.0.1:{os.environ.get('VFE_PORT', '8765')}/mcp"
    anyio.run(serve_stdio, target, token or None)


WORKER_END_GRACE_S = 15.0  # orphaned worker: time given to the graceful stop before the end


@app.command(hidden=True)
def worker() -> None:
    """Processus d'analyse (lancé et supervisé par ``vfe serve``)."""
    import asyncio
    import signal
    import sys
    import threading

    import anyio

    from vfe_vision.core import procs
    from vfe_vision.core.config import get_settings
    from vfe_vision.core.logging import configure_logging
    from vfe_vision.jobs.worker import Worker

    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        log_dir=settings.logs_dir,
        process_name="worker",
    )

    async def main() -> None:
        stop = anyio.Event()
        loop = asyncio.get_running_loop()

        def request_stop(*_: object) -> None:
            loop.call_soon_threadsafe(stop.set)

        signals = [signal.SIGINT, signal.SIGTERM]
        if sys.platform == "win32":
            signals.append(signal.SIGBREAK)
        for sig in signals:
            signal.signal(sig, request_stop)

        def parent_gone() -> None:
            """The API process died without stopping us (POSIX lifeline): finish as it would
            have asked, then make sure nothing of ours is left."""
            request_stop()
            threading.Timer(WORKER_END_GRACE_S, procs.end_with_group).start()

        procs.watch_lifeline(parent_gone)
        await Worker(settings).run(stop)

    anyio.run(main)


@app.command(
    help=tr(
        "Vérifie l'environnement : ffmpeg, ExifTool, LM Studio, GPU, modules natifs, stockage."
        "\n\nAvec --vision, mesure d'abord comment le modèle de vision chargé écrit ses "
        "positions : par l'application si elle tourne, sinon directement.",
        "Checks the environment: ffmpeg, ExifTool, LM Studio, GPU, native modules, storage."
        "\n\nWith --vision, first measures how the loaded vision model writes its positions: "
        "through the application when it runs, otherwise directly.",
    )
)
def doctor(
    as_json: Annotated[
        bool, typer.Option("--json", help=tr("Sortie JSON.", "JSON output."))
    ] = False,
    vision: Annotated[
        bool,
        typer.Option(
            "--vision",
            help=tr(
                "Calibre aussi le modèle de vision chargé (secondes).",
                "Also calibrates the loaded vision model (seconds).",
            ),
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=tr(
                "Avec --vision : recalibre même s'il l'est déjà.",
                "With --vision: calibrates again even when it is.",
            ),
        ),
    ] = False,
) -> None:
    """Vérifie l'environnement : ffmpeg, ExifTool, LM Studio, GPU, modules natifs, stockage.

    Avec --vision, mesure d'abord comment le modèle de vision chargé écrit ses positions :
    par l'application si elle tourne, sinon directement."""
    import anyio
    from rich.console import Console
    from rich.table import Table

    from vfe_vision.core.config import get_settings
    from vfe_vision.db.migrate import upgrade_database
    from vfe_vision.services.container import AppContainer
    from vfe_vision.services.system import CheckStatus, DoctorReport, doctor

    settings = get_settings()
    settings.ensure_dirs()
    upgrade_database(settings.db_path, settings.backups_dir)
    # Before the checks, so that the positions check shows what was just measured.
    calibration = _vision_doctor(settings, force=force) if vision else None
    container = AppContainer.create(settings)

    async def run() -> DoctorReport:
        try:
            return await doctor(container)
        finally:
            await container.aclose()

    report = anyio.run(run)
    if as_json:
        if calibration is None:
            typer.echo(report.model_dump_json(indent=2))
        else:
            payload = {
                "report": report.model_dump(mode="json"),
                "vision": calibration.status.model_dump(mode="json"),
                "vision_error": calibration.error,
            }
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        console = Console()
        table = Table(title=f"Video Frame Expedition {report.version} — Python {report.python}")
        table.add_column(tr("Vérification", "Check"))
        table.add_column(tr("État", "Status"))
        table.add_column(tr("Détail", "Detail"), overflow="fold")
        colors = {CheckStatus.OK: "green", CheckStatus.WARNING: "yellow", CheckStatus.ERROR: "red"}
        for check in report.checks:
            detail = check.detail + (f"\n[dim]{check.hint}[/dim]" if check.hint else "")
            table.add_row(check.label, f"[{colors[check.status]}]{check.status.value}[/]", detail)
        console.print(table)
        if calibration is not None:
            for line in _vision_lines(calibration.status):
                typer.echo(line)
    if calibration is not None and calibration.error:
        typer.echo(calibration.error, err=True)
    raise typer.Exit(0 if report.ok and (calibration is None or calibration.ok) else 1)


PROBE_WAIT_S = 600  # through the app: the job may wait for the worker


@dataclass(frozen=True, slots=True)
class _Calibration:
    status: VisionStatus
    error: str | None  # the calibration asked for failed, timed out or could not start

    @property
    def ok(self) -> bool:
        """Positions measured and usable (one rule for the text and JSON outputs)."""
        profile = self.status.profile
        return self.error is None and profile is not None and profile.grounding.enabled


def _no_model(status: VisionStatus) -> str | None:
    if status.model is not None:
        return None
    if status.lmstudio_error is not None:
        return tr(
            f"LM Studio injoignable : {status.lmstudio_error}",
            f"LM Studio cannot be reached: {status.lmstudio_error}",
        )
    return tr(
        "Aucun modèle de vision chargé dans LM Studio.", "No vision model loaded in LM Studio."
    )


def _vision_doctor(settings: Settings, *, force: bool) -> _Calibration:
    """Calibrate the loaded vision model (progress on stderr: stdout stays parsable)."""
    import anyio

    calibration = _vision_through_app(settings, force=force)
    if calibration is None:  # the app is not running: measure here
        calibration = anyio.run(_vision_inline, settings, force)
    return calibration


def _vision_through_app(settings: Settings, *, force: bool) -> _Calibration | None:
    """Ask the running app (its worker owns the LM Studio budget); None when it is not up."""
    import time

    import httpx

    from vfe_vision.services.system import VisionStatus

    host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host  # noqa: S104
    base = f"http://{host}:{settings.port}/api/v1"
    headers = {"X-VFE-Client": "cli"}
    if settings.api_token is not None:
        headers["Authorization"] = f"Bearer {settings.api_token.get_secret_value()}"
    try:
        with httpx.Client(base_url=base, headers=headers, timeout=10.0) as http:
            health = http.get("/system/health")
            if health.status_code != 200:
                return None
            status = VisionStatus.model_validate(http.get("/system/vision-profile").json())
            if status.model is None or (status.profile is not None and not force):
                return _Calibration(status, _no_model(status))
            if health.json().get("worker_pid") is None:
                return _Calibration(
                    status,
                    tr(
                        "Le travailleur de l'application est arrêté : calibrage impossible.",
                        "The application's worker is stopped: no calibration possible.",
                    ),
                )
            typer.echo(
                tr(
                    f"Calibrage de {status.display_name} par l'application…",
                    f"Calibrating {status.display_name} through the application…",
                ),
                err=True,
            )
            answer = http.post("/system/vision-profile/probe")
            if answer.status_code >= 400:
                detail = answer.json().get("detail")
                return _Calibration(
                    status, tr(f"Calibrage refusé : {detail}", f"Calibration refused: {detail}")
                )
            job = answer.json()
            error: str | None = tr(
                f"Calibrage toujours en attente après {PROBE_WAIT_S // 60} min (tâche "
                f"{job['id']}) : il se fera dès que l'application le pourra.",
                f"Calibration still waiting after {PROBE_WAIT_S // 60} min (job "
                f"{job['id']}): it will run as soon as the application can.",
            )
            deadline = time.monotonic() + PROBE_WAIT_S
            while time.monotonic() < deadline:
                state = http.get(f"/jobs/{job['id']}").json()
                if state["status"] not in {"queued", "running"}:
                    error = (
                        None
                        if state["status"] == "succeeded"
                        else tr(
                            f"Calibrage en échec : {state.get('error') or state['status']}",
                            f"Calibration failed: {state.get('error') or state['status']}",
                        )
                    )
                    break
                time.sleep(1.0)
            status = VisionStatus.model_validate(http.get("/system/vision-profile").json())
            return _Calibration(status, error)
    except httpx.HTTPError:
        return None


async def _vision_inline(settings: Settings, force: bool) -> _Calibration:
    from vfe_vision.adapters.lmstudio.budget import TokenBudget
    from vfe_vision.adapters.lmstudio.catalog import pick_vision_instance
    from vfe_vision.core.errors import VfeError
    from vfe_vision.db.preferences import load_preferences
    from vfe_vision.pipeline import vision_profile
    from vfe_vision.services.container import AppContainer
    from vfe_vision.services.system import vision_status

    container = AppContainer.create(settings)
    try:
        status = await vision_status(container)
        if status.model is None or (status.profile is not None and not force):
            return _Calibration(status, _no_model(status))
        try:
            prefs = load_preferences(container.db)
            models = await container.lmstudio.list_models()
            picked = pick_vision_instance(models, prefs.vision_model)
            if picked is None:
                return _Calibration(
                    status,
                    tr(
                        "Aucun modèle de vision chargé dans LM Studio.",
                        "No vision model loaded in LM Studio.",
                    ),
                )
            model, instance = picked
            typer.echo(
                tr(f"Calibrage de {model.display_name}…", f"Calibrating {model.display_name}…"),
                err=True,
            )
            budget = TokenBudget(capacity=8192, max_concurrency=1)  # resized to the instance
            await vision_profile.measure(container.db, container.lmstudio, budget, model, instance)
        except VfeError as exc:
            failed = tr(f"Calibrage en échec : {exc.detail}", f"Calibration failed: {exc.detail}")
            return _Calibration(await vision_status(container), failed)
        return _Calibration(await vision_status(container), None)
    finally:
        await container.aclose()


def _decimal(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if terminal_language() == "fr" else text


def _vision_lines(status: VisionStatus) -> list[str]:
    from vfe_vision.services.system import convention_text

    if status.model is None:
        return []  # said by the calibration's error
    lines = [
        tr(
            f"Modèle de vision : {status.display_name} ({status.model})",
            f"Vision model: {status.display_name} ({status.model})",
        )
    ]
    profile = status.profile
    if profile is None:
        if status.prior:
            lines.append(
                tr(
                    "Positions : convention connue (Qwen3-VL), non recalibrée",
                    "Positions: known convention (Qwen3-VL), not calibrated again",
                )
            )
        else:
            lines.append(tr("Positions : non calibrées", "Positions: not calibrated"))
        return lines
    g = profile.grounding
    seconds = _decimal(profile.wall_ms / 1000, 1)
    tokens = f"{profile.prompt_tokens} + {profile.completion_tokens}"
    reasoning = profile.reasoning_tokens_seen
    if reasoning:
        thinks = tr(
            f"actif ({reasoning} jetons) : coupez-le dans LM Studio",
            f"on ({reasoning} tokens): turn it off in LM Studio",
        )
    elif status.reasoning_capable:
        thinks = tr("coupé", "off")
    else:
        thinks = tr("aucun", "none")
    truncated = profile.truncated
    lines += [
        tr(
            f"Calibré le {profile.probed_at:%d/%m/%Y %H:%M} UTC en {seconds} s ({tokens} jetons, "
            f"empreinte {profile.fingerprint})",
            f"Calibrated on {profile.probed_at:%Y-%m-%d %H:%M} UTC in {seconds} s ({tokens} "
            f"tokens, fingerprint {profile.fingerprint})",
        ),
        tr(
            f"Réponses : {'tronquées' if truncated else 'complètes'}",
            f"Answers: {'truncated' if truncated else 'complete'}",
        ),
        tr(f"Raisonnement : {thinks}", f"Reasoning: {thinks}"),
    ]
    if g.enabled:
        scenes = ", ".join(
            tr(
                f"{'paysage' if k == 'landscape' else 'portrait'} {_decimal(v)}",
                f"{k} {_decimal(v)}",
            )
            for k, v in g.iou_by_scene.items()
        )
        found = min(g.matched_by_scene.values(), default=0)
        iou = _decimal(g.mean_iou)
        lines.append(
            tr(
                f"Positions : vérifiées, {convention_text(profile)}, IoU {iou} ({scenes}), "
                f"{found}/5 objets au moins par scène",
                f"Positions: checked, {convention_text(profile)}, IoU {iou} ({scenes}), "
                f"{found}/5 objects at least per scene",
            )
        )
    else:
        lines.append(tr(f"Positions : désactivées, {g.reason}", f"Positions: off, {g.reason}"))
    lines.append(
        tr("Conventions : ", "Conventions: ")
        + " · ".join(f"{r.convention.value} {_decimal(r.mean_iou)}" for r in g.ranking)
    )
    return lines


models_app = typer.Typer(
    help=tr(
        "Données et modèles téléchargés (action en ligne, explicite).",
        "Downloaded data and models (an explicit online action).",
    )
)
app.add_typer(models_app, name="models")


@models_app.command(
    "geonames",
    help=tr(
        "Télécharge GeoNames (CC BY 4.0) et construit le répertoire hors-ligne des lieux (~3 Mo).",
        "Downloads GeoNames (CC BY 4.0) and builds the offline directory of places (~3 MB).",
    ),
)
def models_geonames(
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help=tr(
                "Le télécharger à nouveau même s'il est déjà là (mise à jour des lieux).",
                "Download it again even when it is already there (an update of the places).",
            ),
        ),
    ] = False,
) -> None:
    """Télécharge GeoNames (CC BY 4.0) et construit le répertoire hors-ligne des lieux (~3 Mo).
    Déjà là, il est laissé tel quel, comme les modèles : une installation relancée ne le
    télécharge pas une deuxième fois."""
    from vfe_vision.adapters.geo.geonames_build import download_gazetteer
    from vfe_vision.adapters.geo.offline import GeoNamesGazetteer
    from vfe_vision.core.config import get_settings

    settings = get_settings()
    if not refresh and GeoNamesGazetteer(settings.geonames_dir).available:
        typer.echo(
            tr(
                f"GeoNames : déjà installé ({settings.geonames_dir})",
                f"GeoNames: already installed ({settings.geonames_dir})",
            )
        )
        return
    _online_or_exit()
    typer.echo(
        tr(
            "Téléchargement de GeoNames (cities1000, ~14 Mo)…",
            "Downloading GeoNames (cities1000, ~14 MB)…",
        )
    )
    rows = download_gazetteer(settings.geonames_dir)
    typer.echo(
        tr(
            f"{rows} lieux enregistrés dans {settings.geonames_dir}",
            f"{rows} places saved in {settings.geonames_dir}",
        )
    )


def _online_or_exit() -> None:
    from vfe_vision.core.config import get_settings
    from vfe_vision.db.migrate import upgrade_database
    from vfe_vision.db.preferences import load_preferences
    from vfe_vision.db.session import Database

    settings = get_settings()
    settings.ensure_dirs()
    upgrade_database(settings.db_path, settings.backups_dir)
    db = Database(settings.db_path)
    try:
        online = load_preferences(db).online_services
    finally:
        db.dispose()
    if not online:
        typer.echo(
            tr(
                "Services en ligne désactivés : téléchargement refusé (réglages).",
                "Online services turned off: download refused (settings).",
            ),
            err=True,
        )
        raise typer.Exit(1)


def _install(model_ids: list[str]) -> None:
    """Download and check models (sha256), printing progress; nothing is half-installed."""
    from vfe_vision.adapters.models.catalog import spec
    from vfe_vision.adapters.models.store import ModelStore
    from vfe_vision.core.config import get_settings
    from vfe_vision.core.errors import VfeError

    _online_or_exit()
    store = ModelStore(get_settings().models_dir)
    for model_id in model_ids:
        item = spec(model_id)
        label = tr(item.label, item.label_en or item.label)
        if store.installed(item) is not None:
            typer.echo(
                tr(
                    f"{label} : déjà installé ({store.path(item)})",
                    f"{label}: already installed ({store.path(item)})",
                )
            )
            continue
        size = item.download_size / 1_048_576
        licence_en = item.licence_en or item.licence
        typer.echo(
            tr(
                f"{label} — {size:.0f} Mo, licence {item.licence}",
                f"{label} — {size:.0f} MB, licence {licence_en}",
            )
        )
        last = -1

        def show(name: str, done: int, total: int) -> None:
            nonlocal last
            percent = int(100 * done / max(1, total))
            if percent != last:
                last = percent
                typer.echo(f"\r  {percent:3d} % {name:<28}", nl=False)

        try:
            folder = store.download(item, progress=show)
        except VfeError as exc:
            typer.echo(f"\n{exc.detail}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(tr(f"\n  installé dans {folder}", f"\n  installed in {folder}"))


@models_app.command(
    "whisper",
    help=tr(
        "Télécharge le modèle de transcription Whisper (turbo : 1,6 Go, MIT).",
        "Downloads the Whisper transcription model (turbo: 1.6 GB, MIT).",
    ),
)
def models_whisper(
    size: Annotated[
        str,
        typer.Option(
            help=tr(
                "turbo (recommandé), small (plus rapide) ou tiny (essais).",
                "turbo (recommended), small (faster) or tiny (tests).",
            )
        ),
    ] = "turbo",
) -> None:
    """Télécharge le modèle de transcription Whisper (turbo : 1,6 Go, MIT)."""
    names = {"turbo": "whisper/large-v3-turbo", "small": "whisper/small", "tiny": "whisper/tiny"}
    if size not in names:
        raise typer.BadParameter(tr("turbo, small ou tiny", "turbo, small or tiny"))
    _install([names[size]])


@models_app.command(
    "yamnet",
    help=tr(
        "Télécharge YAMNet (sons et instruments, 16 Mo, Apache-2.0) et l'ontologie AudioSet.",
        "Downloads YAMNet (sounds and instruments, 16 MB, Apache-2.0) and the AudioSet ontology.",
    ),
)
def models_yamnet() -> None:
    """Télécharge YAMNet (sons et instruments, 16 Mo, Apache-2.0) et l'ontologie AudioSet."""
    _install(["yamnet"])


@models_app.command(
    "sounds",
    help=tr(
        "Télécharge YAMNet et CED-small (sons entendus, 39 Mo, Apache-2.0).",
        "Downloads YAMNet and CED-small (sounds heard, 39 MB, Apache-2.0).",
    ),
)
def models_sounds() -> None:
    """Télécharge YAMNet et CED-small (sons entendus, 39 Mo, Apache-2.0)."""
    _install(["yamnet", "sounds/ced-small"])


@models_app.command(
    "cuda-runtime",
    help=tr(
        "Télécharge cuBLAS 12 (NVIDIA, 553 Mo) pour transcrire sur le GPU quand il est libre "
        "(Windows seulement : sur Mac, la transcription reste sur le processeur).",
        "Downloads cuBLAS 12 (NVIDIA, 553 MB) to transcribe on the GPU when it is free "
        "(Windows only: on a Mac, the transcription stays on the processor).",
    ),
)
def models_cuda_runtime() -> None:
    """Télécharge cuBLAS 12 (NVIDIA, 553 Mo) pour transcrire sur le GPU quand il est libre
    (Windows seulement : sur Mac, la transcription reste sur le processeur)."""
    if sys.platform == "win32":
        _install(["runtime/cublas-12.9"])
    else:
        typer.echo(
            tr(
                "La transcription sur le GPU (cuBLAS, cartes NVIDIA) n'existe que sous Windows : "
                "sur cet ordinateur, la transcription reste sur le processeur.",
                "Transcription on the GPU (cuBLAS, NVIDIA cards) exists on Windows only: on this "
                "computer, the transcription stays on the processor.",
            ),
            err=True,
        )
        raise typer.Exit(1)


@models_app.command(
    "ocr",
    help=tr(
        "Télécharge PP-OCRv6 small (texte à l'écran, 30 Mo, Apache-2.0).",
        "Downloads PP-OCRv6 small (on-screen text, 30 MB, Apache-2.0).",
    ),
)
def models_ocr() -> None:
    """Télécharge PP-OCRv6 small (texte à l'écran, 30 Mo, Apache-2.0)."""
    _install(["ocr/pp-ocrv6-small"])


@models_app.command(
    "subjects",
    help=tr(
        "Télécharge D-FINE S (personnes et animaux, 42 Mo, Apache-2.0) et YuNet (visages, MIT).",
        "Downloads D-FINE S (people and animals, 42 MB, Apache-2.0) and YuNet (faces, MIT).",
    ),
)
def models_subjects() -> None:
    """Télécharge D-FINE S (personnes et animaux, 42 Mo, Apache-2.0) et YuNet (visages, MIT)."""
    _install(["detector/d-fine-s-coco", "faces/yunet"])


@models_app.command(
    "search",
    help=tr(
        "Télécharge EmbeddingGemma-300m q4 (recherche par le sens, FR/EN, 208 Mo, licence Gemma).",
        "Downloads EmbeddingGemma-300m q4 (search by meaning, FR/EN, 208 MB, Gemma licence).",
    ),
)
def models_search() -> None:
    """Télécharge EmbeddingGemma-300m q4 (recherche par le sens, FR/EN, 208 Mo, licence Gemma)."""
    _install(["embeddings/embeddinggemma-300m-q4"])


@models_app.command(
    "audio-text",
    help=tr(
        "Télécharge d'un coup Whisper turbo, YAMNet, CED-small et PP-OCRv6 (~1,7 Go).",
        "Downloads Whisper turbo, YAMNet, CED-small and PP-OCRv6 at once (~1.7 GB).",
    ),
)
def models_audio_text() -> None:
    """Télécharge d'un coup Whisper turbo, YAMNet, CED-small et PP-OCRv6 (~1,7 Go)."""
    _install(["whisper/large-v3-turbo", "yamnet", "sounds/ced-small", "ocr/pp-ocrv6-small"])


@models_app.command(
    "list",
    help=tr(
        "Liste les modèles téléchargeables et ceux qui sont installés.",
        "Lists the downloadable models and the installed ones.",
    ),
)
def models_list(
    verify: Annotated[
        bool,
        typer.Option(help=tr("Recalcule les empreintes sha256.", "Computes the sha256 again.")),
    ] = False,
) -> None:
    """Liste les modèles téléchargeables et ceux qui sont installés."""
    from vfe_vision.adapters.models.store import ModelStore
    from vfe_vision.core.config import get_settings

    store = ModelStore(get_settings().models_dir)
    failed = False
    for status in store.statuses():
        if status.spec.kind == "runtime" and sys.platform != "win32":
            continue  # cuBLAS: Windows and NVIDIA only
        mark = tr("installé", "installed") if status.installed else tr("absent", "missing")
        size = status.spec.size / 1_048_576
        label = tr(status.spec.label, status.spec.label_en or status.spec.label)
        typer.echo(f"{status.spec.id:<24} {mark:<9} {size:7.0f} {tr('Mo', 'MB')}  {label}")
        if verify and status.installed:
            problems = store.verify(status.spec)
            failed |= bool(problems)
            for problem in problems:
                typer.echo(f"    ! {problem}")
    raise typer.Exit(1 if failed else 0)


@app.command(
    help=tr(
        "Exporte le schéma OpenAPI (source du client TypeScript).",
        "Exports the OpenAPI schema (source of the TypeScript client).",
    ),
)
def openapi(
    output: Annotated[Path, typer.Option(help=tr("Fichier de sortie.", "Output file."))] = Path(
        "openapi.json"
    ),
) -> None:
    """Exporte le schéma OpenAPI (source du client TypeScript)."""
    from vfe_vision.api.app import create_app
    from vfe_vision.core.config import Settings

    schema = create_app(Settings(), start_worker=False).openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    output.write_bytes(text.encode("utf-8"))  # LF on every platform (stable diffs)
    typer.echo(tr(f"Schéma OpenAPI écrit dans {output}", f"OpenAPI schema written to {output}"))


def main() -> None:
    """Console-script entry point."""
    _never_fail_on_a_character()
    try:
        app()
    except ImportError as error:
        raise SystemExit(_start_again_without(error)) from None


def _never_fail_on_a_character() -> None:
    """Redirected to a file or a pipe on Windows, the output takes the system's code page, which
    has no « → » nor « ✓ »: such a character is written « ? » rather than ending the command
    (``vfe doctor > diagnostic.txt``)."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _start_again_without(error: ImportError) -> int:
    """Windows' Smart App Control refused a compiled extension that the package also has in
    plain Python: set the extension aside and run the same command again. Any other
    import error stands."""
    import subprocess
    import sys

    from vfe_vision.core import native_modules

    aside = native_modules.set_aside(error)
    if aside is None:
        raise error
    typer.echo(
        tr(
            f"Windows (Smart App Control) refuse l'extension compilée « {error.name} » : "
            f"l'application emploie désormais sa version en Python, plus lente et équivalente. "
            f"Fichier mis de côté : {aside}",
            f"Windows (Smart App Control) refuses the compiled extension “{error.name}”: the "
            f"application now uses its Python version, slower and equivalent. File set aside: "
            f"{aside}",
        ),
        err=True,
    )
    with subprocess.Popen([sys.executable, "-m", "vfe_vision", *sys.argv[1:]]) as again:
        while True:
            try:
                return again.wait()
            except KeyboardInterrupt:  # Ctrl+C reached the new process too: wait for its end
                continue
