"""Child processes: cancellable execution and Windows Job Objects (no orphans after a crash).

asyncio subprocesses are deliberately avoided (they are unavailable under uvicorn's selector
event loop on Windows); blocking helpers run in worker threads instead.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, ExternalToolError

_POLL_S = 0.25
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000 if sys.platform == "win32" else 0
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_s: float

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


def run_process(
    args: Sequence[str],
    *,
    timeout_s: float | None = None,
    cancel: CancelToken | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
    tool_name: str | None = None,
    job: KillOnCloseJob | None = None,
) -> ProcessResult:
    """Run ``args`` (never through a shell) and return its output.

    The process is killed if ``cancel`` fires or ``timeout_s`` elapses, and with ``job`` when
    the job's owner dies. With ``check=True`` a non-zero exit status raises
    :class:`ExternalToolError` including the tail of stderr.
    """
    name = tool_name or Path(args[0]).stem
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise ExternalToolError(f"{name} est introuvable : {args[0]}", tool=name) from exc
    if job is not None:
        job.assign(proc)

    pending_input = input_bytes
    while True:
        try:
            stdout, stderr = proc.communicate(input=pending_input, timeout=_POLL_S)
            break
        except subprocess.TimeoutExpired:
            pending_input = None  # already written by the first communicate() call
            if cancel is not None and cancel.cancelled:
                _kill(proc)
                raise CancelledError(cancel.reason or "Annulé") from None
            if timeout_s is not None and time.monotonic() - started > timeout_s:
                _kill(proc)
                raise ExternalToolError(
                    f"{name} n'a pas terminé en {timeout_s:.0f} s", tool=name
                ) from None

    result = ProcessResult(proc.returncode, stdout, stderr, time.monotonic() - started)
    if check and result.returncode != 0:
        tail = result.stderr_text.strip().splitlines()[-8:]
        raise ExternalToolError(
            f"{name} a échoué (code {result.returncode}) : " + " | ".join(tail),
            tool=name,
            returncode=result.returncode,
        )
    return result


def _kill(proc: subprocess.Popen[bytes]) -> None:
    proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.communicate(timeout=5)


class KillOnCloseJob:
    """A Windows Job Object that kills every assigned process when its last handle closes.

    The API process assigns the worker to such a job: if the API process dies (crash, Ctrl+C,
    reload), Windows terminates the worker and, transitively, every ffmpeg/ASR child it started.
    On other platforms this is a no-op.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )
            ]  # fmt: skip

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        job_object_extended_limit_information = 9
        kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle),
            job_object_extended_limit_information,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        self._handle = int(handle)
        self._kernel32 = kernel32

    def assign(self, proc: subprocess.Popen[bytes]) -> bool:
        """Attach a started process to the job; return False if unsupported."""
        if self._handle is None:
            return False
        import ctypes
        from ctypes import wintypes

        process_handle = getattr(proc, "_handle", None)
        if process_handle is None:
            return False
        ok = self._kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(self._handle), wintypes.HANDLE(int(process_handle))
        )
        return bool(ok) or ctypes.get_last_error() == 0

    def cpu_seconds(self) -> float | None:
        """CPU time (user + kernel) used so far by every process of the job, ``None`` when it
        cannot be told. A slow child on a busy machine still uses CPU; a hung one does not."""
        if self._handle is None:
            return None
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        info = Accounting()
        job_object_basic_accounting_information = 1
        ok = self._kernel32.QueryInformationJobObject(
            wintypes.HANDLE(self._handle),
            job_object_basic_accounting_information,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        if not ok:
            return None
        total: int = info.TotalUserTime + info.TotalKernelTime
        return total / 1e7  # 100 ns units

    def contains(self, pid: int) -> bool | None:
        """Whether process ``pid`` belongs to this job; ``None`` when it cannot be told
        (unsupported platform, closed job, process gone or not accessible)."""
        if self._handle is None:
            return None
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not process:
            return None
        try:
            result = wintypes.BOOL()
            if not kernel32.IsProcessInJob(process, self._handle, ctypes.byref(result)):
                return None
            return bool(result.value)
        finally:
            kernel32.CloseHandle(process)

    def assign_pid(self, pid: int) -> bool:
        """Attach a running process by id, e.g. the real interpreter behind the venv's
        ``python.exe`` launcher, which may have started before its launcher joined the job."""
        if self._handle is None:
            return False
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, 0, pid)
        if not process:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(self._handle, process))
        finally:
            kernel32.CloseHandle(process)

    def ensure_pid(self, pid: int) -> bool:
        """Make sure ``pid`` is in the job (assigning it when needed); False if it is not."""
        if self.contains(pid):
            return True
        return self.assign_pid(pid) and self.contains(pid) is not False

    def close(self) -> None:
        """Close the job handle, killing any process still assigned to it."""
        if self._handle is not None:
            import ctypes
            from ctypes import wintypes

            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = None
