"""Child processes: cancellable execution and process jobs (no orphans after a crash).

A :class:`KillOnCloseJob` ties children to the process that started them. On Windows it is a
Job Object: every assigned process, and its own children, dies when the job's last handle
closes, so a crash of the parent takes the whole tree with it. On macOS and Linux the job keeps
the process group of each assigned child (killed as one with ``killpg``) and holds a lifeline: a
pipe whose write end only the parent has. A child that watches it (:func:`watch_lifeline`) ends
with its group when the pipe closes, that is when the parent is gone, however it went.

asyncio subprocesses are deliberately avoided (they are unavailable under uvicorn's selector
event loop on Windows); blocking helpers run in worker threads instead.

The platform branches are written as ``if sys.platform == "win32": … else: …`` on purpose: the
type checker then reads each system's branch with that system's library, and never sees the
other one as dead code.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vfe_vision.core.cancel import CancelToken
from vfe_vision.core.errors import CancelledError, ExternalToolError

_POLL_S = 0.25
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000 if sys.platform == "win32" else 0
NICE_BELOW_NORMAL = 10  # POSIX: what :func:`lower_priority` adds to a child's niceness
LIFELINE_VARIABLE = "VFE_LIFELINE_FD"  # the lifeline's read end, as the child sees it
END_GRACE_S = 2.0  # SIGTERM to the group, then SIGKILL this much later
_CPU_CACHE_S = 1.0  # POSIX: ``ps`` is asked at most this often
_PS = "/bin/ps"  # macOS and Linux
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
            **(job.popen_kwargs() if job is not None else {}),
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


def lower_priority() -> None:
    """Give this process a below-normal priority (POSIX: niceness; on Windows the parent sets
    ``BELOW_NORMAL_PRIORITY_CLASS`` when it starts the child)."""
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.nice(NICE_BELOW_NORMAL)


def end_with_group() -> None:
    """End this process and every process of its group (its own children): SIGTERM, a short
    grace, SIGKILL, then exit without cleaning up. On Windows: exit alone."""
    if sys.platform == "win32":
        os._exit(1)
    else:
        group = os.getpgrp()
        with contextlib.suppress(OSError):
            os.killpg(group, signal.SIGTERM)
        time.sleep(END_GRACE_S)
        with contextlib.suppress(OSError):
            os.killpg(group, signal.SIGKILL)
        os._exit(1)


def watch_lifeline(on_lost: Callable[[], None] = end_with_group) -> bool:
    """In a child started with :meth:`KillOnCloseJob.child_env`: watch the lifeline from a
    thread and run ``on_lost`` when the parent is gone (the pipe closes). By default the child
    ends with its own process group, so its grandchildren (ffmpeg…) go too. Returns False when
    there is no lifeline to watch: on Windows (the Job Object does it), or in a child started by
    hand."""
    if sys.platform == "win32":
        return False
    else:
        raw = os.environ.get(LIFELINE_VARIABLE)
        if not raw:
            return False
        try:
            fd = int(raw)
            os.fstat(fd)  # the descriptor was not inherited: nothing to watch
        except (ValueError, OSError):
            return False

        def wait_for_eof() -> None:
            with contextlib.suppress(OSError):
                while os.read(fd, 1):  # the parent never writes: only the end of the pipe comes
                    pass
            on_lost()

        threading.Thread(target=wait_for_eof, name="lifeline", daemon=True).start()
        return True


def _clock_seconds(text: str) -> float:
    """Seconds of a ``ps`` TIME column: ``MM:SS.ss``, ``HH:MM:SS`` or ``D-HH:MM:SS``."""
    days, _, clock = text.rpartition("-")
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return float(days or 0) * 86400 + seconds


class KillOnCloseJob:
    """Children tied to this process: a Windows Job Object, or process groups and a lifeline
    on macOS and Linux (see the module).

    The API process assigns the worker to such a job: if the API process dies (crash, Ctrl+C,
    reload), the worker and, transitively, every ffmpeg/ASR child it started end with it.
    """

    def __init__(self) -> None:
        self._handle: int | None = None  # Windows: the Job Object
        self._members: dict[int, int | None] = {}  # POSIX: pid → its process group (ours: None)
        self._lifeline: tuple[int, int] | None = None  # POSIX: (read end, write end)
        self._cpu: tuple[float, float] | None = None  # POSIX: (monotonic time, CPU seconds)
        if sys.platform == "win32":
            self._windows_init()
        else:
            self._lifeline = os.pipe()

    def _windows_init(self) -> None:
        if sys.platform == "win32":
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

    # ------------------------------------------------------------ starting a child
    def popen_kwargs(self, *, own_group: bool = True) -> dict[str, Any]:
        """Extra arguments for ``subprocess.Popen`` so that the child can be assigned: on POSIX
        its own process group (unless the caller starts a new session, which makes one) and the
        lifeline's read end; nothing on Windows."""
        if self._lifeline is None:
            return {}
        kwargs: dict[str, Any] = {"pass_fds": (self._lifeline[0],)}
        if own_group:
            kwargs["process_group"] = 0
        return kwargs

    def child_env(self, env: Mapping[str, str] | None = None) -> dict[str, str]:
        """``env`` (this process's by default) plus the lifeline the child may watch."""
        result = dict(os.environ if env is None else env)
        if self._lifeline is not None:
            result[LIFELINE_VARIABLE] = str(self._lifeline[0])
        return result

    def assign(self, proc: subprocess.Popen[bytes]) -> bool:
        """Attach a started process to the job; return False if unsupported."""
        if sys.platform == "win32":
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
        else:
            if self._lifeline is None:
                return False
            return self._remember(proc.pid)

    def _remember(self, pid: int) -> bool:
        """POSIX: note the process and its group (never our own group: killing it would kill
        this process too; such a child is then killed alone)."""
        if sys.platform == "win32":
            return False
        else:
            try:
                group = os.getpgid(pid)
            except OSError:  # already gone
                return False
            self._members[pid] = group if group != os.getpgrp() else None
            return True

    # ------------------------------------------------------------ watching
    def cpu_seconds(self) -> float | None:
        """CPU time (user + kernel) used so far by every process of the job, ``None`` when it
        cannot be told. A slow child on a busy machine still uses CPU; a hung one does not."""
        if sys.platform == "win32":
            return self._windows_cpu_seconds() if self._handle is not None else None
        else:
            if self._lifeline is None or not self._members:
                return None
            now = time.monotonic()
            if self._cpu is not None and now - self._cpu[0] < _CPU_CACHE_S:
                return self._cpu[1]
            total = self._posix_cpu_seconds()
            if total is not None:
                self._cpu = (now, total)
            return total

    def _windows_cpu_seconds(self) -> float | None:
        if sys.platform == "win32":
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
        else:
            return None

    def _posix_cpu_seconds(self) -> float | None:
        """The CPU time of the members and of every process of their groups (``ps``: the only
        portable account of a live process on macOS and Linux)."""
        groups = {group for group in self._members.values() if group is not None}
        try:
            done = subprocess.run(
                [_PS, "-A", "-o", "pid=,pgid=,time="],
                capture_output=True, text=True, timeout=5, check=False,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        total = 0.0
        for line in done.stdout.splitlines():
            fields = line.split()
            if len(fields) != 3:
                continue
            try:
                pid, group = int(fields[0]), int(fields[1])
                if pid in self._members or group in groups:
                    total += _clock_seconds(fields[2])
            except ValueError:
                continue
        return total

    def contains(self, pid: int) -> bool | None:
        """Whether process ``pid`` belongs to this job; ``None`` when it cannot be told
        (unsupported platform, closed job, process gone or not accessible)."""
        if sys.platform == "win32":
            return self._windows_contains(pid) if self._handle is not None else None
        else:
            if self._lifeline is None:
                return None
            try:
                group = os.getpgid(pid)
            except OSError:
                return None
            groups = {g for g in self._members.values() if g is not None}
            return pid in self._members or group in groups

    def _windows_contains(self, pid: int) -> bool | None:
        if sys.platform == "win32":
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
        else:
            return None

    def assign_pid(self, pid: int) -> bool:
        """Attach a running process by id, e.g. the real interpreter behind the venv's
        ``python.exe`` launcher, which may have started before its launcher joined the job."""
        if sys.platform == "win32":
            return self._windows_assign_pid(pid) if self._handle is not None else False
        else:
            if self._lifeline is None:
                return False
            return self._remember(pid)

    def _windows_assign_pid(self, pid: int) -> bool:
        if sys.platform == "win32":
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
        else:
            return False

    def ensure_pid(self, pid: int) -> bool:
        """Make sure ``pid`` is in the job (assigning it when needed); False if it is not."""
        if self.contains(pid):
            return True
        return self.assign_pid(pid) and self.contains(pid) is not False

    # ------------------------------------------------------------ ending
    def close(self) -> None:
        """Close the job, killing any process still assigned to it (Windows: the Job Object's
        handle; POSIX: SIGKILL to each member's group, then the lifeline's ends)."""
        if sys.platform == "win32":
            if self._handle is not None:
                import ctypes
                from ctypes import wintypes

                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._handle))
                self._handle = None
        else:
            if self._lifeline is None:
                return
            read_end, write_end = self._lifeline
            self._lifeline = None
            for pid, group in list(self._members.items()):
                with contextlib.suppress(OSError):  # already gone
                    if group is not None:
                        os.killpg(group, signal.SIGKILL)
                    else:
                        os.kill(pid, signal.SIGKILL)
            self._members.clear()
            for fd in (write_end, read_end):
                with contextlib.suppress(OSError):
                    os.close(fd)
