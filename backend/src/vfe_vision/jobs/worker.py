"""The analysis worker: a single process (file lock) that executes queued jobs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import anyio
from filelock import FileLock, Timeout

from vfe_vision.adapters.asr.client import SubprocessRecognizer
from vfe_vision.adapters.embeddings.gemma import EmbedderLoader
from vfe_vision.adapters.exiftool.runner import ExifTool
from vfe_vision.adapters.ffmpeg.tools import Ffmpeg
from vfe_vision.adapters.geo.nominatim import NominatimClient
from vfe_vision.adapters.geo.offline import GeoNamesGazetteer
from vfe_vision.adapters.gpu.nvidia_smi import NvidiaSmi
from vfe_vision.adapters.lmstudio.budget import TokenBudget
from vfe_vision.adapters.lmstudio.catalog import pick_vision_instance
from vfe_vision.adapters.lmstudio.client import LmStudioClient
from vfe_vision.adapters.models.store import ModelStore
from vfe_vision.adapters.weather.open_meteo import OpenMeteoClient
from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import CancelledError, ConflictError, VfeError
from vfe_vision.core.logging import get_logger
from vfe_vision.db.lmstudio_link import LinkReader
from vfe_vision.db.models import Job, LibraryRoot, Video
from vfe_vision.db.preferences import load_preferences
from vfe_vision.db.session import Database
from vfe_vision.domain.enums import JobKind, JobStatus, VideoStatus
from vfe_vision.domain.lmstudio_link import is_local
from vfe_vision.jobs import bench, queue
from vfe_vision.jobs.events import DbEventSink
from vfe_vision.jobs.relink import relink_videos
from vfe_vision.jobs.scan import scan_root
from vfe_vision.jobs.sidecars import import_first, write_after
from vfe_vision.jobs.timeline_bins import sync_timeline
from vfe_vision.pipeline import vision_profile
from vfe_vision.pipeline.gpu import GpuGate
from vfe_vision.pipeline.registry import StageRegistry
from vfe_vision.pipeline.runner import PipelineRunner
from vfe_vision.pipeline.stage import ProgressFn, Resource, StageContext, Toolbox, VideoRef
from vfe_vision.pipeline.stages import default_registry
from vfe_vision.ports.asr import SpeechRecognizer
from vfe_vision.ports.embeddings import EmbedderSource
from vfe_vision.ports.geocoding import ReverseGeocoder
from vfe_vision.ports.gpu import VramMeter
from vfe_vision.ports.weather import WeatherProvider
from vfe_vision.storage.artifacts import ArtifactStore

log = get_logger(__name__)
AUTO_SCAN_FIRST_S = 20.0  # after a start, once the queue is back
AUTO_SCAN_INTERVAL_S = 120.0  # folders with automatic update: looked at this often

POLL_INTERVAL_S = 0.5
# Jobs only waiting for the transcription engine do not count against max_concurrent_videos,
# within this multiple of it (their other stages are done: they hold no memory worth counting).
MAX_WAITING_FACTOR = 4
HEARTBEAT_INTERVAL_S = 2.0
# Short jobs that never wait for a video slot: the vision model's calibration (the shared LM
# Studio budget only), the update of a timeline bin (files looked at, no analysis).
LIGHT_KINDS = frozenset({JobKind.PROBE_VISION, JobKind.SYNC_TIMELINE, JobKind.RELINK_VIDEOS})
# Jobs that run with nothing else running: the model bench loads and unloads vision models, an
# analysis running meanwhile would be done with a model on trial.
ALONE_KINDS = frozenset({JobKind.BENCH_MODELS})
INDEX_THREADS = 4  # EmbeddingGemma: 95 ms per 320-character passage on 4 threads, 132 on 2


@dataclass(slots=True)
class _Running:
    token: CancelToken
    kind: JobKind
    shutdown: bool = False


@dataclass(slots=True)
class Worker:
    settings: Settings
    registry: StageRegistry = field(default_factory=default_registry)
    lmstudio: LmStudioClient | None = None  # injectable for tests
    weather: WeatherProvider | None = None
    geocoder: ReverseGeocoder | None = None
    asr: SpeechRecognizer | None = None
    embedder: EmbedderSource | None = None
    vram: VramMeter | None = None  # what the model bench measures the card with
    bench_settle_s: float = bench.SETTLE_S  # the bench's pause after a load (tests: none)
    _running: dict[str, _Running] = field(default_factory=dict, init=False)
    _scan_token: CancelToken = field(default_factory=CancelToken, init=False)

    async def run(self, stop: anyio.Event) -> None:
        """Process jobs until ``stop`` is set. Returns immediately if another worker runs."""
        lock = FileLock(str(self.settings.worker_lock_path))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            log.warning("another worker already owns the job queue", lock=str(lock.lock_file))
            return
        db = Database(self.settings.db_path)
        sink = DbEventSink(db)
        # The address chosen on the System page, followed without a restart.
        lmstudio = self.lmstudio or LmStudioClient(
            LinkReader(
                db,
                self.settings.lmstudio_url,
                self.settings.lmstudio_token.get_secret_value()
                if self.settings.lmstudio_token
                else None,
            ),
            timeout_s=self.settings.lmstudio_timeout_s,
        )
        weather = self.weather or OpenMeteoClient(
            historical_forecast_url=self.settings.open_meteo_historical_url,
            archive_url=self.settings.open_meteo_archive_url,
            api_key=self.settings.open_meteo_api_key.get_secret_value()
            if self.settings.open_meteo_api_key
            else None,
        )
        geocoder = self.geocoder or NominatimClient(base_url=self.settings.nominatim_url)
        vram = NvidiaSmi(self.settings.nvidia_smi_path)
        models = ModelStore(self.settings.models_dir)
        tools = Toolbox(
            db=db,
            artifacts=ArtifactStore(self.settings.artifacts_dir),
            ffmpeg=Ffmpeg(self.settings.ffmpeg_path, self.settings.ffprobe_path),
            exiftool=ExifTool(self.settings.exiftool_path),
            lmstudio=lmstudio,
            lm_budget=TokenBudget(capacity=8192, max_concurrency=1),
            weather=weather,
            geocoder=geocoder,
            gazetteer=GeoNamesGazetteer(self.settings.geonames_dir),
            gpu=GpuGate(vram, margin_mib=self.settings.gpu_vram_margin_mib),
            models=models,
            # A busy CPU transcribes slower than real time: the stall watchdog is the guard.
            # The same probe watches a transcription the gate lent the GPU to.
            asr=self.asr or SubprocessRecognizer(timeout_per_audio_s=3.0, vram=vram),
            asr_threads=self.settings.asr_threads,
            # The index is built last, when little else runs: four threads for its passages.
            embedder=self.embedder or EmbedderLoader(models, threads=INDEX_THREADS),
        )
        runner = PipelineRunner(
            self.registry,
            limits={
                Resource.CPU: anyio.Semaphore(self.settings.cpu_workers),
                Resource.GPU: anyio.Semaphore(1),
                Resource.NETWORK: anyio.Semaphore(1),  # services allow ~1 request/s anyway
                Resource.ASR: anyio.Semaphore(1),  # one Whisper child at a time
            },
            events=sink,
        )
        try:
            requeued = queue.requeue_interrupted(db)
            bench.settle_interrupted(db)
            with db.write() as session:
                settled = queue.settle_waiting_videos(session)
            log.info("worker started", pid=os.getpid(), requeued=requeued, settled=settled)
            sink.emit("worker.started", data={"pid": os.getpid(), "requeued": requeued})
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._heartbeat_loop, db, stop)
                self._scan_token = CancelToken()
                tg.start_soon(self._auto_scan_loop, db, sink, stop, self._scan_token)
                while not stop.is_set():
                    limit = self.settings.max_concurrent_videos
                    heavy = sum(1 for r in self._running.values() if r.kind not in LIGHT_KINDS)
                    active = heavy - runner.waiting
                    room = active < limit and heavy < limit * MAX_WAITING_FACTOR
                    light = sum(1 for r in self._running.values() if r.kind in LIGHT_KINDS)
                    alone = any(r.kind in ALONE_KINDS for r in self._running.values())
                    if not alone and (room or not light):
                        job = await anyio.to_thread.run_sync(
                            partial(
                                queue.claim_next,
                                db,
                                os.getpid(),
                                kinds=None if room else LIGHT_KINDS,
                                alone=ALONE_KINDS,
                                busy=bool(self._running),
                            )
                        )
                        if job is not None:
                            self._running[job.id] = _Running(CancelToken(), job.kind)
                            tg.start_soon(self._execute, job, tools, runner, sink)
                            continue
                    with anyio.move_on_after(POLL_INTERVAL_S):
                        await stop.wait()
                self._scan_token.cancel("Arrêt du worker")
                for running in self._running.values():
                    running.shutdown = True
                    running.token.cancel("Arrêt du worker")
        finally:
            sink.emit("worker.stopped", data={"pid": os.getpid()})
            sink.close()
            tools.exiftool.close()
            for client in (weather, geocoder):
                if isinstance(client, OpenMeteoClient | NominatimClient):
                    client.close()
            if self.lmstudio is None:
                await lmstudio.aclose()
            db.dispose()
            lock.release()
            log.info("worker stopped")

    def _meter(self, lmstudio_url: str) -> VramMeter | None:
        """What the model bench measures the memory with: this computer's card, which says
        nothing of a model loaded by an LM Studio running elsewhere."""
        return NvidiaSmi(self.settings.nvidia_smi_path) if is_local(lmstudio_url) else None

    @staticmethod
    async def _auto_scan_loop(
        db: Database, sink: DbEventSink, stop: anyio.Event, token: CancelToken
    ) -> None:
        """Folders with automatic update: files added, changed, moved or gone are found without
        a click (then analysed, as after any scan of such a folder). Quiet when nothing changed:
        no job, no event. A failing pass never stops the worker and its analyses."""
        delay = AUTO_SCAN_FIRST_S
        while not stop.is_set():
            with anyio.move_on_after(delay):
                await stop.wait()
            if stop.is_set():
                return
            delay = AUTO_SCAN_INTERVAL_S
            for root_id in await anyio.to_thread.run_sync(queue.auto_scan_roots, db):
                if stop.is_set():
                    return
                try:
                    report = await anyio.to_thread.run_sync(
                        partial(scan_root, db, root_id, cancel=token, automatic=True)
                    )
                except CancelledError:  # the worker is stopping
                    return
                except ConflictError:  # « Rescan » is at it
                    continue
                except VfeError as exc:
                    log.warning("automatic scan failed", root_id=root_id, error=exc.detail)
                    continue
                except Exception:  # e.g. the folder removed meanwhile: logged, the worker goes on
                    log.exception("automatic scan crashed", root_id=root_id)
                    continue
                if report.changes:
                    log.info("automatic scan found changes", root_id=root_id, new=report.new)
                    sink.emit("library.scanned", data={"root_id": root_id, "automatic": True})

    async def _heartbeat_loop(self, db: Database, stop: anyio.Event) -> None:
        while not stop.is_set():
            ids = list(self._running)
            cancelled = await anyio.to_thread.run_sync(queue.heartbeat, db, ids)
            for job_id in cancelled:
                if (running := self._running.get(job_id)) is not None:
                    running.token.cancel()
            with anyio.move_on_after(HEARTBEAT_INTERVAL_S):
                await stop.wait()

    async def _execute(
        self, job: Job, tools: Toolbox, runner: PipelineRunner, sink: DbEventSink
    ) -> None:
        running = self._running[job.id]
        job_log = log.bind(job_id=job.id, kind=job.kind.value, video_id=job.video_id)

        def progress(fraction: float, message: str | None) -> None:
            sink.emit(
                "job.progress",
                job_id=job.id,
                video_id=job.video_id,
                data={"progress": round(fraction, 4), "message": message},
            )

        sink.emit("job.started", job_id=job.id, video_id=job.video_id, data={"kind": job.kind})
        status, error, message = JobStatus.FAILED, None, None
        try:
            if job.kind == JobKind.ANALYZE_VIDEO:
                status, message = await self._analyze(job, tools, runner, running.token, progress)
            elif job.kind == JobKind.SCAN_ROOT:
                root_id = job.root_id or ""
                report = await anyio.to_thread.run_sync(
                    lambda: scan_root(tools.db, root_id, cancel=running.token, progress=progress)
                )
                status = JobStatus.SUCCEEDED
                message = (
                    f"{report.found} vidéos : {report.new} nouvelles, {report.changed} modifiées, "
                    f"{report.moved} déplacées, {report.offline} hors ligne"
                )
                sink.emit("library.scanned", job_id=job.id, data={"root_id": job.root_id})
            elif job.kind == JobKind.SYNC_TIMELINE:
                sync = partial(
                    sync_timeline, tools.db, job.payload, data_dir=self.settings.data_dir,
                    events=sink, job_id=job.id, cancel=running.token, progress=progress,
                )  # fmt: skip
                synced = await anyio.to_thread.run_sync(sync)
                status = JobStatus.PARTIAL if synced.errors else JobStatus.SUCCEEDED
                message = synced.message
            elif job.kind == JobKind.RELINK_VIDEOS:
                relink = partial(
                    relink_videos, tools.db, job.payload, data_dir=self.settings.data_dir,
                    events=sink, job_id=job.id, cancel=running.token, progress=progress,
                )  # fmt: skip
                relinked = await anyio.to_thread.run_sync(relink)
                status = JobStatus.SUCCEEDED
                message = relinked.message
            elif job.kind == JobKind.PROBE_VISION:
                progress(0.1, "Calibrage du modèle de vision")
                message = await self._probe_vision(tools, running.token)
                status = JobStatus.SUCCEEDED
            elif job.kind == JobKind.BENCH_MODELS:
                status, message = await bench.run_bench(
                    bench.BenchTools(
                        tools.db,
                        tools.artifacts,
                        tools.lmstudio,
                        self.vram or self._meter(tools.lmstudio.base_url),
                        self.bench_settle_s,
                    ),
                    job,
                    cancel=running.token,
                    progress=progress,
                )
        except CancelledError:
            status = JobStatus.CANCELLED
        except VfeError as exc:
            status, error = JobStatus.FAILED, exc.detail
            job_log.warning("job failed", error=exc.detail)
        except Exception as exc:  # noqa: BLE001 - a job bug must not kill the worker
            job_log.exception("job crashed")
            status, error = JobStatus.FAILED, f"{type(exc).__name__}: {exc}"
        finally:
            self._running.pop(job.id, None)

        if status == JobStatus.CANCELLED and running.shutdown:
            # Interrupted by a worker shutdown: resume on next start.
            await anyio.to_thread.run_sync(queue.requeue_interrupted, tools.db)
        else:
            await anyio.to_thread.run_sync(
                lambda: queue.finish(tools.db, job.id, status, error=error, message=message)
            )
        sink.emit(
            "job.finished",
            job_id=job.id,
            video_id=job.video_id,
            data={"status": status.value, "error": error, "message": message},
        )
        job_log.info("job finished", status=status.value)

    @staticmethod
    async def _probe_vision(tools: Toolbox, token: CancelToken) -> str:
        """Measure the loaded vision model now (System page « Recalibrate », doctor --vision)."""
        prefs = await anyio.to_thread.run_sync(load_preferences, tools.db)
        picked = pick_vision_instance(await tools.lmstudio.list_models(), prefs.vision_model)
        if picked is None:
            raise VfeError("Aucun modèle de vision chargé dans LM Studio.")
        model, instance = picked
        profile = await vision_profile.measure(
            tools.db, tools.lmstudio, tools.lm_budget, model, instance, cancel=token
        )
        return vision_profile.summary(profile)

    async def _analyze(
        self,
        job: Job,
        tools: Toolbox,
        runner: PipelineRunner,
        token: CancelToken,
        progress: ProgressFn,
    ) -> tuple[JobStatus, str | None]:
        if job.video_id is None:
            raise VfeError("Job d'analyse sans vidéo")
        video_ref = await anyio.to_thread.run_sync(self._prepare_video, tools.db, job.video_id)
        prefs = await anyio.to_thread.run_sync(load_preferences, tools.db)
        ctx = StageContext(
            video=video_ref,
            prefs=prefs,
            tools=tools,
            cancel=token,
            progress=progress,
            log=log.bind(video=video_ref.filename, job_id=job.id),
            job_focus=job.payload.get("focus"),
        )
        imported = None
        if job.payload.get("force") is not True:  # « Redo everything »: nothing to take over
            imported = await anyio.to_thread.run_sync(
                partial(import_first, ctx, runner.registry, runner.events, job_id=job.id)
            )
        report = await runner.run(
            ctx,
            job_id=job.id,
            stages=job.payload.get("stages"),
            force=job.payload.get("force", False),
            refresh=job.payload.get("refresh", False),
        )
        if report.cancelled:
            video_status, job_status = VideoStatus.PARTIAL, JobStatus.CANCELLED
        elif report.failed_required:
            video_status, job_status = VideoStatus.FAILED, JobStatus.FAILED
        elif report.incomplete:
            video_status, job_status = VideoStatus.PARTIAL, JobStatus.PARTIAL
        else:
            video_status, job_status = VideoStatus.READY, JobStatus.SUCCEEDED

        def finish_video() -> None:
            with tools.db.write() as session:
                video = session.get_one(Video, video_ref.id)
                # Another analysis queued meanwhile (e.g. a folder « Complete »): the video
                # is not settled yet, it waits for that job.
                waiting = queue.has_queued_analysis(session, video_ref.id, besides=job.id)
                video.status = VideoStatus.QUEUED if waiting else video_status
                video.last_analyzed_at = datetime.now(UTC)

        await anyio.to_thread.run_sync(finish_video)
        if job_status in {JobStatus.SUCCEEDED, JobStatus.PARTIAL}:
            await anyio.to_thread.run_sync(
                partial(
                    write_after, tools.db, video_ref.id, runner.events, job_id=job.id, log=ctx.log
                )
            )
        errors = "; ".join(f"{k} : {v}" for k, v in report.errors.items())
        if report.failed_required:
            raise VfeError(errors or "Analyse en échec")
        if imported is not None and imported.imported:
            return job_status, errors or f"Analyses reprises de « {imported.path.name} »"
        return job_status, errors or None

    @staticmethod
    def _prepare_video(db: Database, video_id: str) -> VideoRef:
        with db.write() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise VfeError(f"Vidéo introuvable : {video_id}")
            missing = not Path(video.path).is_file()
            video.status = VideoStatus.OFFLINE if missing else VideoStatus.ANALYZING
            path = video.path
        if missing:  # raised after the commit so the OFFLINE status is kept
            raise VfeError(f"Fichier introuvable : {path}")
        with db.read() as session:
            video = session.get_one(Video, video_id)
            root = session.get(LibraryRoot, video.root_id)
            return VideoRef(
                id=video.id,
                path=Path(video.path),
                filename=video.filename,
                fingerprint=video.fingerprint,
                duration_s=video.duration_s,
                root_focus=root.analysis_focus if root else None,
                root_clock_offset_s=root.clock_offset_s if root else None,
                root_timezone=root.default_timezone if root else None,
                root_latitude=root.default_latitude if root else None,
                root_longitude=root.default_longitude if root else None,
            )
