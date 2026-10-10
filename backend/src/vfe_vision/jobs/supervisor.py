"""Start, watch and stop the worker process from the API process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque

from vfe_vision.core.config import Settings
from vfe_vision.core.logging import get_logger
from vfe_vision.core.native_modules import REFUSED_EXIT
from vfe_vision.core.procs import KillOnCloseJob

log = get_logger(__name__)

MAX_RESTARTS = 5
RESTART_WINDOW_S = 300.0
STOP_TIMEOUT_S = 15.0


def worker_env(settings: Settings) -> dict[str, str]:
    """Environment for the child: the same infrastructure settings as the parent."""
    env = dict(os.environ)
    env.update(
        {
            "VFE_DATA_DIR": str(settings.data_dir),
            "VFE_FFMPEG_PATH": settings.ffmpeg_path,
            "VFE_FFPROBE_PATH": settings.ffprobe_path,
            "VFE_EXIFTOOL_PATH": settings.exiftool_path,
            "VFE_LMSTUDIO_URL": settings.lmstudio_url,
            "VFE_MODEL_SERVER": settings.model_server,
            "VFE_MODEL_SERVER_PARALLEL": str(settings.model_server_parallel),
            "VFE_MODEL_SERVER_VISION": "true" if settings.model_server_vision else "false",
            "VFE_LOG_LEVEL": settings.log_level,
            "VFE_LOG_FORMAT": settings.log_format,
            "VFE_CPU_WORKERS": str(settings.cpu_workers),
            "VFE_MAX_CONCURRENT_VIDEOS": str(settings.max_concurrent_videos),
            "PYTHONUTF8": "1",
        }
    )
    if settings.lmstudio_token is not None:
        env["VFE_LMSTUDIO_TOKEN"] = settings.lmstudio_token.get_secret_value()
    return env


class WorkerSupervisor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._proc: subprocess.Popen[bytes] | None = None
        self._job = KillOnCloseJob()
        self._stopping = threading.Event()
        self._restarts: deque[float] = deque()
        self._monitor: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc and self._proc.poll() is None else None

    def start(self) -> None:
        self._stopping.clear()
        self._spawn()
        self._monitor = threading.Thread(target=self._watch, name="worker-monitor", daemon=True)
        self._monitor.start()

    def _spawn(self) -> None:
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creationflags = 0
        with self._lock:
            # POSIX: its own session (the terminal's Ctrl+C does not reach it) and the job's
            # lifeline, which ends it with its children if this process dies without stopping it.
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "vfe_vision", "worker"],
                env=self._job.child_env(worker_env(self._settings)),
                creationflags=creationflags,
                start_new_session=sys.platform != "win32",
                **self._job.popen_kwargs(own_group=False),
            )
            self._job.assign(self._proc)
        log.info("worker process started", pid=self._proc.pid)

    def _watch(self) -> None:
        while not self._stopping.is_set():
            proc = self._proc
            if proc is None:
                return
            code = proc.wait()
            if self._stopping.is_set():
                return
            if code == REFUSED_EXIT:  # Windows refuses one of its files: starting again won't do
                log.critical("worker refused by Windows (Smart App Control), analyses stopped")
                return
            now = time.monotonic()
            self._restarts.append(now)
            while self._restarts and now - self._restarts[0] > RESTART_WINDOW_S:
                self._restarts.popleft()
            if len(self._restarts) > MAX_RESTARTS:
                log.critical("worker keeps crashing, giving up", exit_code=code)
                return
            delay = min(30.0, 2.0 ** len(self._restarts))
            log.error("worker exited unexpectedly, restarting", exit_code=code, delay_s=delay)
            if self._stopping.wait(delay):
                return
            self._spawn()

    def stop(self) -> None:
        """Ask the worker to finish gracefully (jobs are re-queued), then force if needed."""
        self._stopping.set()
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            self._job.close()
            return
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=STOP_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError):
            log.warning("worker did not stop in time, killing it", pid=proc.pid)
            proc.kill()
            proc.wait(timeout=5)
        finally:
            self._job.close()
        log.info("worker process stopped", exit_code=proc.returncode)
