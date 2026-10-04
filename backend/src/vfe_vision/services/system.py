"""Environment diagnostics (``vfe doctor`` and the System page)."""

from __future__ import annotations

import importlib
import platform
import shutil
import sys
from enum import StrEnum

import anyio
import sqlalchemy as sa
from pydantic import BaseModel

from vfe_vision import __version__
from vfe_vision.adapters.lmstudio.catalog import ModelInfo, pick_vision_instance
from vfe_vision.adapters.models.catalog import DEFAULTS, spec
from vfe_vision.adapters.models.store import ModelStore
from vfe_vision.core.errors import (
    ConflictError,
    ExternalToolError,
    ServiceUnavailableError,
    VfeError,
)
from vfe_vision.core.procs import run_process
from vfe_vision.db.models import Job, Transcript
from vfe_vision.db.preferences import load_preferences
from vfe_vision.domain.enums import JobKind
from vfe_vision.domain.lmstudio_link import is_local
from vfe_vision.domain.vision_profile import VisionProfile, prior_for
from vfe_vision.jobs import queue
from vfe_vision.pipeline.stages.transcript import CUDA_RUNTIME, GPU_NEED_MIB
from vfe_vision.pipeline.vision_profile import model_fingerprint, stored_profile
from vfe_vision.services.container import AppContainer

LOW_CONTEXT_TOKENS = 6000
LOW_DISK_BYTES = 5 * 1024**3


class CheckStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class Check(BaseModel):
    id: str
    label: str
    status: CheckStatus
    detail: str
    hint: str | None = None


class DoctorReport(BaseModel):
    version: str
    python: str
    platform: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(check.status != CheckStatus.ERROR for check in self.checks)


def _tool_version(label: str, args: list[str], check_id: str, hint: str) -> Check:
    try:
        result = run_process(args, timeout_s=20, tool_name=label)
    except ExternalToolError as exc:
        return Check(
            id=check_id, label=label, status=CheckStatus.ERROR, detail=exc.detail, hint=hint
        )
    text = (result.stdout_text or result.stderr_text).strip().splitlines()
    return Check(id=check_id, label=label, status=CheckStatus.OK, detail=text[0] if text else "ok")


_SMART_APP_CONTROL_HINT = (
    "Windows Smart App Control peut bloquer une DLL inconnue le temps de vérifier sa réputation : "
    "relancez la commande ; si le blocage persiste, réinstallez le paquet concerné. Une exclusion "
    "de l'antivirus Defender n'a aucun effet sur Smart App Control."
)


def _failed_imports(modules: list[str]) -> list[str]:
    failed: list[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            failed.append(f"{name} ({exc})")
    return failed


def _native_modules() -> Check:
    """Import native extensions early: Smart App Control may block unknown DLLs.

    ``win32job`` (pywin32, pulled in by the MCP SDK) only serves the SDK's stdio client, which
    the app does not use (its own Job Objects go through ``ctypes``): a block is a warning.
    """
    # av and tokenizers are the native parts of faster-whisper (the transcription child).
    required = ["cv2", "onnxruntime", "ctranslate2", "av", "tokenizers", "numpy"]
    optional = ["win32job"] if sys.platform == "win32" else []
    failed = _failed_imports(required)
    if failed:
        return Check(
            id="native",
            label="Modules natifs",
            status=CheckStatus.ERROR,
            detail="Chargement impossible : " + "; ".join(failed),
            hint=_SMART_APP_CONTROL_HINT,
        )
    failed = _failed_imports(optional)
    if failed:
        return Check(
            id="native",
            label="Modules natifs",
            status=CheckStatus.WARNING,
            detail="Module facultatif bloqué : " + "; ".join(failed),
            hint=_SMART_APP_CONTROL_HINT,
        )
    return Check(
        id="native",
        label="Modules natifs",
        status=CheckStatus.OK,
        detail=", ".join(required + optional),
    )


def _gpu() -> Check:
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
    except Exception:  # noqa: BLE001 - no NVIDIA driver is a normal situation
        return Check(
            id="gpu", label="GPU", status=CheckStatus.WARNING, detail="Aucun GPU NVIDIA détecté"
        )
    free_mb = memory.free / 2**20
    detail = (
        f"{name if isinstance(name, str) else name.decode()} — "
        f"{memory.used / 2**30:.1f} / {memory.total / 2**30:.1f} Go utilisés, "
        f"{free_mb:.0f} Mo libres"
    )
    return Check(
        id="gpu",
        label="GPU",
        status=CheckStatus.OK,
        detail=detail,
        hint="Le GPU appartient au modèle de vision de LM Studio ; il n'est prêté (décodage, "
        "transcription) que si la mémoire libre le permet, avec une marge.",
    )


def _free_vram_mib() -> int | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        memory = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
    except Exception:  # noqa: BLE001 - no NVIDIA driver is a normal situation
        return None
    return int(memory.free / 2**20)


def _gigabytes(mib: int) -> str:
    """Two decimals: a refusal never shows the same figure for « free » and « needed »."""
    return f"{mib / 1024:.2f} Go".replace(".", ",")


def _last_gpu_fallback(c: AppContainer) -> str | None:
    """Why the latest transcription left the GPU for the CPU, if it did (worker-side facts)."""
    with c.db.read() as session:
        row = session.execute(
            sa.select(Transcript.params, Transcript.updated_at)
            .order_by(Transcript.updated_at.desc())
            .limit(1)
        ).one_or_none()
    if row is None:
        return None
    stats = (row.params or {}).get("stats") or {}
    when = f"{row.updated_at:%d/%m %H:%M} UTC"
    if stats.get("gpu_fallback"):
        return (
            f"la dernière transcription ({when}) a été refaite sur le processeur : "
            f"{stats['gpu_fallback']}"
        )
    if stats.get("gpu_skipped"):
        return (
            f"la dernière transcription ({when}) n'a pas essayé le GPU, en pause après : "
            f"{stats['gpu_skipped']}"
        )
    return None


def _gpu_transcription(c: AppContainer) -> Check:
    """Whether a transcription would be lent the GPU right now."""
    prefs = load_preferences(c.db)
    label = "Transcription sur le GPU"
    need = GPU_NEED_MIB.get(prefs.whisper_model, 1664) + c.settings.gpu_vram_margin_mib
    wanted = f"{_gigabytes(need)} libres"
    if not prefs.gpu_transcription:
        return Check(
            id="gpu_transcription", label=label, status=CheckStatus.OK,
            detail="Désactivée : la transcription reste sur le processeur.",
            hint=f"Activable ici ; elle ne sert que si le modèle de vision laisse {wanted}.",
        )  # fmt: skip
    if ModelStore(c.settings.models_dir).installed(spec(CUDA_RUNTIME)) is None:
        return Check(
            id="gpu_transcription", label=label, status=CheckStatus.WARNING,
            detail="Activée, mais cuBLAS 12 n'est pas installé : transcription sur le processeur.",
            hint="vfe models cuda-runtime (553 Mo, NVIDIA).",
        )  # fmt: skip
    free = _free_vram_mib()
    if free is None:
        return Check(
            id="gpu_transcription", label=label, status=CheckStatus.WARNING,
            detail="Mémoire graphique illisible : transcription sur le processeur.",
        )  # fmt: skip
    have = f"{_gigabytes(free)} libres"
    fallback = _last_gpu_fallback(c)
    if free < need:
        return Check(
            id="gpu_transcription", label=label, status=CheckStatus.WARNING,
            detail=f"En ce moment {have}, il en faut {wanted} (il manque {need - free} Mio) : "
            "transcription sur le processeur.",
            hint="Un contexte plus court dans LM Studio libère de la mémoire (environ 0,56 Go "
            "par tranche de 4 096 jetons avec Qwen3-VL) ; l'application ne recharge jamais le "
            "modèle elle-même.",
        )  # fmt: skip
    if fallback is not None:
        return Check(
            id="gpu_transcription", label=label, status=CheckStatus.WARNING,
            detail=f"Mémoire suffisante en ce moment ({have} pour {wanted}), mais : {fallback}.",
            hint="Après un échec, le GPU n'est plus essayé pendant 10 minutes (6 heures si CUDA "
            "ne se charge pas) ; redémarrer l'application lève cette pause.",
        )  # fmt: skip
    return Check(
        id="gpu_transcription", label=label, status=CheckStatus.OK,
        detail=f"Prête : {have} pour {wanted} nécessaires.",
        hint="Vérifié à chaque vidéo : sans assez de mémoire, le processeur prend le relais.",
    )  # fmt: skip


async def _lmstudio(c: AppContainer) -> list[Check]:
    checks: list[Check] = []
    url = c.lmstudio.base_url  # the one chosen on this page, or the installation's
    remote = not is_local(url)
    if remote:
        checks.append(
            Check(
                id="lmstudio_remote",
                label="LM Studio distant",
                status=CheckStatus.WARNING,
                detail=f"{url} n'est pas une adresse locale : les images y seront envoyées.",
                hint="Adresse choisie dans la carte « LM Studio » de cette page. Sur un réseau "
                "local, les images y voyagent sans chiffrement (Tailscale, lui, chiffre).",
            )
        )
    try:
        models = await c.lmstudio.list_models()
    except VfeError as exc:
        return [
            *checks,
            Check(
                id="lmstudio",
                label="LM Studio",
                status=CheckStatus.ERROR,
                detail=exc.detail,
                hint="Vérifiez l'adresse dans la carte « LM Studio » de cette page, et que "
                "l'autre ordinateur est allumé."
                if remote
                else "Ouvrez LM Studio → Developer → Start server, "
                "puis chargez un modèle de vision.",
            ),
        ]
    prefs = load_preferences(c.db)
    picked = pick_vision_instance(models, prefs.vision_model)
    vision_models = [m.key for m in models if m.vision]
    if picked is None:
        checks.append(
            Check(
                id="lmstudio",
                label="LM Studio",
                status=CheckStatus.WARNING,
                detail=f"Serveur joignable, aucun modèle de vision chargé ({len(vision_models)} "
                "disponibles).",
                hint="Chargez un modèle de vision (ex. qwen/qwen3-vl-8b) entièrement sur le GPU.",
            )
        )
        return checks
    model, instance = picked
    context = instance.context_length or 0
    checks.append(await anyio.to_thread.run_sync(_vision_profile_check, c, model))
    status = CheckStatus.WARNING if context and context < LOW_CONTEXT_TOKENS else CheckStatus.OK
    checks.append(
        Check(
            id="lmstudio",
            label="LM Studio",
            status=status,
            detail=f"{model.display_name} chargé (instance « {instance.id} », contexte "
            f"{context or '?'} tokens, {instance.parallel or 1} requêtes parallèles)",
            hint="Contexte court : augmentez-le dans LM Studio si la VRAM le permet."
            if status == CheckStatus.WARNING
            else None,
        )
    )
    return checks


def _vision_profile_check(c: AppContainer, model: ModelInfo) -> Check:
    """How positions are read for the loaded model (read-only: never probes)."""
    profile = stored_profile(c.db, model)
    label = "Positions du modèle de vision"
    recalibrate = "Recalibrez depuis la page Système ou avec « vfe doctor --vision --force »."
    if profile is not None:
        g = profile.grounding
        when = f"{profile.probed_at:%d/%m/%Y}"
        if profile.reasoning_tokens_seen:
            return Check(
                id="vision_profile", label=label, status=CheckStatus.WARNING,
                detail=f"{model.display_name} raisonne ({profile.reasoning_tokens_seen} jetons "
                "au calibrage du " + when + ") : les analyses sont plus lentes.",
                hint="Coupez le raisonnement de ce modèle dans LM Studio, puis recalibrez.",
            )  # fmt: skip
        if g.enabled:
            return Check(
                id="vision_profile", label=label, status=CheckStatus.OK,
                detail=f"Vérifiées le {when} : {convention_text(profile)}, IoU "
                f"{_decimal(g.mean_iou)}" + ("" if g.precise else " (approximatives)"),
            )  # fmt: skip
        return Check(
            id="vision_profile", label=label, status=CheckStatus.WARNING,
            detail=f"Désactivées pour {model.display_name} depuis le {when} : {g.reason}.",
            hint=recalibrate,
        )  # fmt: skip
    if prior_for(model.key, model.architecture) is not None:
        return Check(
            id="vision_profile", label=label, status=CheckStatus.OK,
            detail=f"{model.display_name} : convention connue (Qwen3-VL), non recalibrée.",
        )  # fmt: skip
    return Check(
        id="vision_profile", label=label, status=CheckStatus.WARNING,
        detail=f"{model.display_name} n'est pas encore calibré : il le sera à la première "
        "analyse (quelques secondes).",
        hint="Pour le faire maintenant : page Système « Recalibrer », ou « vfe doctor --vision ».",
    )  # fmt: skip


def _decimal(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}".replace(".", ",")


def convention_text(profile: VisionProfile) -> str:
    g = profile.grounding
    if g.convention is None:
        return "convention inconnue"
    order = "[y1, x1, y2, x2]" if g.convention.y_first else "[x1, y1, x2, y2]"
    scale = {"1000": "sur 0-1000", "px": "en pixels", "unit": "sur 0-1"}[
        g.convention.value.rsplit("_", 1)[-1]
    ]
    if g.convention.value.startswith("xywh"):
        order = "[x, y, largeur, hauteur]"
    elif g.convention.value.startswith("cxcywh"):
        order = "[centre x, centre y, largeur, hauteur]"
    return f"{order} {scale} ({g.box_field})"


class VisionStatus(BaseModel):
    """The loaded vision model and what is known of how it writes boxes."""

    model: str | None = None
    display_name: str | None = None
    fingerprint: str | None = None
    reasoning_capable: bool = False  # LM Studio lists reasoning options for it
    prior: bool = False  # a verified convention, used until a probe measures one
    profile: VisionProfile | None = None
    lmstudio_error: str | None = None  # LM Studio unreachable: start it, not load a model


async def vision_status(c: AppContainer) -> VisionStatus:
    try:
        models = await c.lmstudio.list_models()
    except VfeError as exc:
        return VisionStatus(lmstudio_error=exc.detail)
    prefs = await anyio.to_thread.run_sync(load_preferences, c.db)
    picked = pick_vision_instance(models, prefs.vision_model)
    if picked is None:
        return VisionStatus()
    model, _ = picked
    return VisionStatus(
        model=model.key,
        display_name=model.display_name,
        fingerprint=model_fingerprint(model),
        reasoning_capable=bool(model.reasoning_options),
        prior=prior_for(model.key, model.architecture) is not None,
        profile=await anyio.to_thread.run_sync(stored_profile, c.db, model),
    )


async def request_probe(c: AppContainer) -> Job:
    """Queue a calibration of the loaded vision model (a few seconds of GPU), or return the one
    already queued or running."""
    status = await vision_status(c)
    if status.lmstudio_error is not None:
        raise ServiceUnavailableError(status.lmstudio_error)
    if status.model is None:
        raise ConflictError(
            "Aucun modèle de vision chargé dans LM Studio : chargez-en un, puis recalibrez."
        )
    active = await anyio.to_thread.run_sync(queue.active_job, c.db, JobKind.PROBE_VISION)
    if active is not None:
        return active
    return await anyio.to_thread.run_sync(
        lambda: queue.enqueue(c.db, JobKind.PROBE_VISION, priority=10)
    )


def _models(c: AppContainer) -> Check:
    """Sound, speech, on-screen text, subject and search models (``vfe models``): the stages
    skip without them; the search index keeps its words only."""
    whisper = load_preferences(c.db).whisper_model
    store = ModelStore(c.settings.models_dir)
    wanted = [spec(whisper)] + [
        spec(DEFAULTS[kind]) for kind in ("yamnet", "ced", "ocr", "detector", "faces", "embeddings")
    ]
    missing = [item.id for item in wanted if store.installed(item) is None]
    if missing:
        # CED-small only adds a second opinion to the sounds: nothing is skipped without it,
        # and « Update » (not « Complete ») adds it to the videos already analysed.
        optional = DEFAULTS["ced"] in missing
        words_only = DEFAULTS["embeddings"] in missing
        required = [
            item for item in missing if item not in {DEFAULTS["ced"], DEFAULTS["embeddings"]}
        ]
        commands = list(
            dict.fromkeys(
                f"vfe models whisper --size {whisper.rsplit('/', 1)[-1].replace('large-v3-', '')}"
                if item.startswith("whisper/")
                else "vfe models subjects"
                if item.startswith(("detector/", "faces/"))
                else "vfe models sounds"
                if item == "yamnet" and optional
                else f"vfe models {item.split('/', 1)[0]}"
                for item in required
            )
        )
        hints = []
        if commands:
            hints.append(
                " ; ".join(commands) + ", puis « Compléter » (sans eux, ces étapes sont sautées)."
            )
        if optional and "vfe models sounds" not in commands:
            hints.append(
                "Facultatif : vfe models sounds, puis « Mettre à jour » pour ajouter le second "
                "avis de CED-small sur les sons (sans lui, YAMNet seul)."
            )
        if words_only:
            hints.append(
                "Recherche : vfe models search, puis « Compléter » pour chercher aussi par le sens "
                "(sans lui, la recherche ne trouve que les mots)."
            )
        return Check(
            id="models",
            label="Modèles son, texte, sujets et recherche",
            status=CheckStatus.WARNING,
            detail="Non installés : " + ", ".join(missing),
            hint=" ".join(hints),
        )
    return Check(
        id="models",
        label="Modèles son, texte, sujets et recherche",
        status=CheckStatus.OK,
        detail=", ".join(item.id for item in wanted),
    )


def _storage(c: AppContainer) -> Check:
    usage = shutil.disk_usage(c.settings.data_dir)
    status = CheckStatus.WARNING if usage.free < LOW_DISK_BYTES else CheckStatus.OK
    return Check(
        id="storage",
        label="Données",
        status=status,
        detail=f"{c.settings.data_dir} — {usage.free / 2**30:.0f} Go libres",
    )


async def doctor(c: AppContainer) -> DoctorReport:
    s = c.settings
    blocking_checks = await anyio.to_thread.run_sync(
        lambda: [
            _tool_version("ffmpeg", [s.ffmpeg_path, "-version"], "ffmpeg",
                          "Installez FFmpeg (winget install Gyan.FFmpeg) "
                          "ou réglez VFE_FFMPEG_PATH."),
            _tool_version("ffprobe", [s.ffprobe_path, "-version"], "ffprobe",
                          "ffprobe est fourni avec FFmpeg."),
            _tool_version("ExifTool", [s.exiftool_path, "-ver"], "exiftool",
                          "Installez ExifTool (winget install OliverBetz.ExifTool) ou réglez "
                          "VFE_EXIFTOOL_PATH."),
            _native_modules(),
            _gpu(),
            _gpu_transcription(c),
            _models(c),
            _storage(c),
        ]
    )  # fmt: skip
    return DoctorReport(
        version=__version__,
        python=platform.python_version(),
        platform=platform.platform(),
        checks=[*blocking_checks, *(await _lmstudio(c))],
    )
