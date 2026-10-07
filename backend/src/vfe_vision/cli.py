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

if TYPE_CHECKING:
    from vfe_vision.core.config import Settings
    from vfe_vision.services.system import VisionStatus

app = typer.Typer(
    name="vfe",
    help="Video Frame Expedition for DaVinci Resolve — analyse locale de vidéos.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def root() -> None:
    """Video Frame Expedition for DaVinci Resolve — analyse locale de vidéos."""


@app.command()
def version() -> None:
    """Affiche la version de l'application."""
    typer.echo(__version__)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Adresse d'écoute (défaut : 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (défaut : 8765).")] = None,
    reload: Annotated[bool, typer.Option(help="Rechargement automatique (développement).")] = False,
    no_worker: Annotated[
        bool, typer.Option("--no-worker", help="Ne pas lancer le worker.")
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
        typer.secho("--reload : écoute locale seulement (Tailscale ignoré).", err=True, fg="yellow")
    typer.echo(
        f"Video Frame Expedition {__version__} → {settings.base_url}"
        f"  (MCP : {settings.base_url}/mcp)"
    )
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
            f"Écoute impossible sur {settings.host}:{settings.port} : {exc.strerror or exc}",
            err=True, fg="red",
        )  # fmt: skip
        raise typer.Exit(1) from exc
    access = access_config(settings, plan, listeners)
    typer.echo(
        f"Video Frame Expedition {__version__} → {settings.base_url}"
        f"  (MCP : {settings.base_url}/mcp)"
    )
    for url in access.remote_urls():
        typer.echo(f"  depuis vos autres appareils → {url}  (MCP : {url}/mcp ; jeton : vfe token)")
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


@app.command()
def token(
    rotate: Annotated[
        bool, typer.Option("--rotate", help="Remplace le jeton (les sessions ouvertes tombent).")
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
            typer.echo("Le jeton vient de VFE_API_TOKEN : changez-le à cet endroit.", err=True)
            raise typer.Exit(1)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fresh = write_new_token(settings.api_token_path)
        typer.echo(fresh)
        typer.echo(
            f"Nouveau jeton enregistré dans {settings.api_token_path}. Redémarrez l'application "
            "pour l'appliquer ; les appareils connectés devront se reconnecter.",
            err=True,
        )
        return
    value, source = current_token(settings, create=True)
    typer.echo(value)
    where = "VFE_API_TOKEN" if source == "env" else str(settings.api_token_path)
    typer.echo(f"Jeton d'accès depuis les autres appareils ({where}).", err=True)


@app.command("mcp-stdio")
def mcp_stdio(
    url: Annotated[
        str | None,
        typer.Option(help="Adresse MCP de l'application (défaut : http://127.0.0.1:8765/mcp)."),
    ] = None,
    token: Annotated[
        str | None,
        typer.Option(
            envvar="VFE_MCP_TOKEN",
            show_default=False,
            help="Jeton d'API, pour une application sur un autre appareil (ou VFE_MCP_TOKEN).",
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


@app.command()
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Sortie JSON.")] = False,
    vision: Annotated[
        bool,
        typer.Option("--vision", help="Calibre aussi le modèle de vision chargé (secondes)."),
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Avec --vision : recalibre même s'il l'est déjà.")
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
        table.add_column("Vérification")
        table.add_column("État")
        table.add_column("Détail", overflow="fold")
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
        return f"LM Studio injoignable : {status.lmstudio_error}"
    return "Aucun modèle de vision chargé dans LM Studio."


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
                    status, "Le travailleur de l'application est arrêté : calibrage impossible."
                )
            typer.echo(f"Calibrage de {status.display_name} par l'application…", err=True)
            answer = http.post("/system/vision-profile/probe")
            if answer.status_code >= 400:
                return _Calibration(status, f"Calibrage refusé : {answer.json().get('detail')}")
            job = answer.json()
            error: str | None = (
                f"Calibrage toujours en attente après {PROBE_WAIT_S // 60} min (tâche "
                f"{job['id']}) : il se fera dès que l'application le pourra."
            )
            deadline = time.monotonic() + PROBE_WAIT_S
            while time.monotonic() < deadline:
                state = http.get(f"/jobs/{job['id']}").json()
                if state["status"] not in {"queued", "running"}:
                    error = (
                        None
                        if state["status"] == "succeeded"
                        else f"Calibrage en échec : {state.get('error') or state['status']}"
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
                return _Calibration(status, "Aucun modèle de vision chargé dans LM Studio.")
            model, instance = picked
            typer.echo(f"Calibrage de {model.display_name}…", err=True)
            budget = TokenBudget(capacity=8192, max_concurrency=1)  # resized to the instance
            await vision_profile.measure(container.db, container.lmstudio, budget, model, instance)
        except VfeError as exc:
            failed = f"Calibrage en échec : {exc.detail}"
            return _Calibration(await vision_status(container), failed)
        return _Calibration(await vision_status(container), None)
    finally:
        await container.aclose()


def _decimal(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def _vision_lines(status: VisionStatus) -> list[str]:
    from vfe_vision.services.system import convention_text

    if status.model is None:
        return []  # said by the calibration's error
    lines = [f"Modèle de vision : {status.display_name} ({status.model})"]
    profile = status.profile
    if profile is None:
        lines.append(
            "Positions : convention connue (Qwen3-VL), non recalibrée"
            if status.prior
            else "Positions : non calibrées"
        )
        return lines
    g = profile.grounding
    lines += [
        (
            f"Calibré le {profile.probed_at:%d/%m/%Y %H:%M} UTC en "
            f"{_decimal(profile.wall_ms / 1000, 1)} s "
            f"({profile.prompt_tokens} + {profile.completion_tokens} jetons, empreinte "
            f"{profile.fingerprint})"
        ),
        f"Réponses : {'tronquées' if profile.truncated else 'complètes'}",
        "Raisonnement : "
        + (
            f"actif ({profile.reasoning_tokens_seen} jetons) : coupez-le dans LM Studio"
            if profile.reasoning_tokens_seen
            else ("coupé" if status.reasoning_capable else "aucun")
        ),
    ]
    if g.enabled:
        scenes = ", ".join(
            f"{'paysage' if k == 'landscape' else 'portrait'} {_decimal(v)}"
            for k, v in g.iou_by_scene.items()
        )
        found = min(g.matched_by_scene.values(), default=0)
        lines.append(
            f"Positions : vérifiées, {convention_text(profile)}, IoU {_decimal(g.mean_iou)} "
            f"({scenes}), {found}/5 objets au moins par scène"
        )
    else:
        lines.append(f"Positions : désactivées, {g.reason}")
    lines.append(
        "Conventions : "
        + " · ".join(f"{r.convention.value} {_decimal(r.mean_iou)}" for r in g.ranking)
    )
    return lines


models_app = typer.Typer(help="Données et modèles téléchargés (action en ligne, explicite).")
app.add_typer(models_app, name="models")


@models_app.command("geonames")
def models_geonames() -> None:
    """Télécharge GeoNames (CC BY 4.0) et construit le répertoire hors-ligne des lieux (~3 Mo)."""
    from vfe_vision.adapters.geo.geonames_build import download_gazetteer
    from vfe_vision.core.config import get_settings

    _online_or_exit()
    settings = get_settings()
    typer.echo("Téléchargement de GeoNames (cities1000, ~14 Mo)…")
    rows = download_gazetteer(settings.geonames_dir)
    typer.echo(f"{rows} lieux enregistrés dans {settings.geonames_dir}")


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
        typer.echo("Services en ligne désactivés : téléchargement refusé (réglages).", err=True)
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
        if store.installed(item) is not None:
            typer.echo(f"{item.label} : déjà installé ({store.path(item)})")
            continue
        typer.echo(
            f"{item.label} — {item.download_size / 1_048_576:.0f} Mo, licence {item.licence}"
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
        typer.echo(f"\n  installé dans {folder}")


@models_app.command("whisper")
def models_whisper(
    size: Annotated[
        str, typer.Option(help="turbo (recommandé), small (plus rapide) ou tiny (essais).")
    ] = "turbo",
) -> None:
    """Télécharge le modèle de transcription Whisper (turbo : 1,6 Go, MIT)."""
    names = {"turbo": "whisper/large-v3-turbo", "small": "whisper/small", "tiny": "whisper/tiny"}
    if size not in names:
        raise typer.BadParameter("turbo, small ou tiny")
    _install([names[size]])


@models_app.command("yamnet")
def models_yamnet() -> None:
    """Télécharge YAMNet (sons et instruments, 16 Mo, Apache-2.0) et l'ontologie AudioSet."""
    _install(["yamnet"])


@models_app.command("sounds")
def models_sounds() -> None:
    """Télécharge YAMNet et CED-small (sons entendus, 39 Mo, Apache-2.0)."""
    _install(["yamnet", "sounds/ced-small"])


@models_app.command("cuda-runtime")
def models_cuda_runtime() -> None:
    """Télécharge cuBLAS 12 (NVIDIA, 553 Mo) pour transcrire sur le GPU quand il est libre
    (Windows seulement : sur Mac, la transcription reste sur le processeur)."""
    if sys.platform == "win32":
        _install(["runtime/cublas-12.9"])
    else:
        typer.echo(
            "La transcription sur le GPU (cuBLAS, cartes NVIDIA) n'existe que sous Windows : sur "
            "cet ordinateur, la transcription reste sur le processeur.",
            err=True,
        )
        raise typer.Exit(1)


@models_app.command("ocr")
def models_ocr() -> None:
    """Télécharge PP-OCRv6 small (texte à l'écran, 30 Mo, Apache-2.0)."""
    _install(["ocr/pp-ocrv6-small"])


@models_app.command("subjects")
def models_subjects() -> None:
    """Télécharge D-FINE S (personnes et animaux, 42 Mo, Apache-2.0) et YuNet (visages, MIT)."""
    _install(["detector/d-fine-s-coco", "faces/yunet"])


@models_app.command("search")
def models_search() -> None:
    """Télécharge EmbeddingGemma-300m q4 (recherche par le sens, FR/EN, 208 Mo, licence Gemma)."""
    _install(["embeddings/embeddinggemma-300m-q4"])


@models_app.command("audio-text")
def models_audio_text() -> None:
    """Télécharge d'un coup Whisper turbo, YAMNet, CED-small et PP-OCRv6 (~1,7 Go)."""
    _install(["whisper/large-v3-turbo", "yamnet", "sounds/ced-small", "ocr/pp-ocrv6-small"])


@models_app.command("list")
def models_list(
    verify: Annotated[bool, typer.Option(help="Recalcule les empreintes sha256.")] = False,
) -> None:
    """Liste les modèles téléchargeables et ceux qui sont installés."""
    from vfe_vision.adapters.models.store import ModelStore
    from vfe_vision.core.config import get_settings

    store = ModelStore(get_settings().models_dir)
    failed = False
    for status in store.statuses():
        if status.spec.kind == "runtime" and sys.platform != "win32":
            continue  # cuBLAS: Windows and NVIDIA only
        mark = "installé" if status.installed else "absent"
        typer.echo(f"{status.spec.id:<24} {mark:<9} {status.spec.size / 1_048_576:7.0f} Mo  "
                   f"{status.spec.label}")  # fmt: skip
        if verify and status.installed:
            problems = store.verify(status.spec)
            failed |= bool(problems)
            for problem in problems:
                typer.echo(f"    ! {problem}")
    raise typer.Exit(1 if failed else 0)


@app.command()
def openapi(
    output: Annotated[Path, typer.Option(help="Fichier de sortie.")] = Path("openapi.json"),
) -> None:
    """Exporte le schéma OpenAPI (source du client TypeScript)."""
    from vfe_vision.api.app import create_app
    from vfe_vision.core.config import Settings

    schema = create_app(Settings(), start_worker=False).openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    output.write_bytes(text.encode("utf-8"))  # LF on every platform (stable diffs)
    typer.echo(f"Schéma OpenAPI écrit dans {output}")


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
        f"Windows (Smart App Control) refuse l'extension compilée « {error.name} » : "
        f"l'application emploie désormais sa version en Python, plus lente et équivalente. "
        f"Fichier mis de côté : {aside}",
        err=True,
    )
    with subprocess.Popen([sys.executable, "-m", "vfe_vision", *sys.argv[1:]]) as again:
        while True:
            try:
                return again.wait()
            except KeyboardInterrupt:  # Ctrl+C reached the new process too: wait for its end
                continue
