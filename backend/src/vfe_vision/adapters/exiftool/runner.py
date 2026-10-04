"""Persistent ExifTool process (``-stay_open``): one Perl start-up for a whole library scan.

Arguments are sent through stdin as UTF-8 with ``-charset filename=utf8`` so that accented Windows
paths survive (the legacy application broke on them).
"""

from __future__ import annotations

import json
import secrets
import subprocess
import threading
from pathlib import Path
from typing import Any

from vfe_vision.core.errors import ExternalToolError
from vfe_vision.core.procs import CREATE_NO_WINDOW

# -G3:1: embedded samples (GoPro GPMF, DJI, Insta360 GPS…) come as "Doc<N>:<group>:Tag"; main
# document tags keep their plain "<group>:Tag" names (ExifTool omits the "Main" family-3 group).
COMMON_ARGS = [
    "-json", "-G3:1", "-n", "-ee3", "-api", "LargeFileSupport=1", "-charset", "filename=utf8",
]  # fmt: skip


class ExifTool:
    def __init__(self, path: str = "exiftool", *, timeout_s: float = 120.0) -> None:
        self.path = path
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._counter = 0

    def _start(self) -> subprocess.Popen[bytes]:
        try:
            proc = subprocess.Popen(
                [self.path, "-stay_open", "True", "-@", "-", "-common_args", *COMMON_ARGS],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise ExternalToolError(
                f"ExifTool est introuvable : {self.path}", tool="exiftool"
            ) from exc
        return proc

    def read(self, file: Path) -> dict[str, Any]:
        """All metadata of ``file`` (groups as prefixes, numeric values, embedded tracks)."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = self._start()
            proc = self._proc
            if proc.stdin is None or proc.stdout is None:
                raise ExternalToolError("ExifTool sans flux d'entrée/sortie", tool="exiftool")
            self._counter += 1
            # A random execute number: a tag value cannot contain the marker by chance or design.
            token = f"{secrets.randbelow(10**12)}{self._counter}"
            marker = f"{{ready{token}}}".encode()
            proc.stdin.write(f"{file}\n-execute{token}\n".encode())
            proc.stdin.flush()
            output = self._read_until(proc, marker)
        try:
            data = json.loads(output.decode("utf-8", errors="replace") or "[{}]")
        except json.JSONDecodeError as exc:
            raise ExternalToolError(
                f"Sortie ExifTool illisible pour {file.name}", tool="exiftool"
            ) from exc
        return data[0] if data else {}

    def _read_until(self, proc: subprocess.Popen[bytes], marker: bytes) -> bytes:
        stdout = proc.stdout
        if stdout is None:
            raise ExternalToolError("ExifTool sans flux de sortie", tool="exiftool")
        result: list[bytes] = []
        error: list[BaseException] = []

        def reader() -> None:
            buffer = b""
            try:
                while marker not in buffer:
                    chunk = stdout.read1(65536)  # type: ignore[attr-defined]
                    if not chunk:
                        error.append(EOFError("ExifTool s'est arrêté"))
                        return
                    buffer += chunk
                result.append(buffer[: buffer.index(marker)])
            except OSError as exc:
                error.append(exc)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(self.timeout_s)
        if thread.is_alive() or error or not result:
            self.close(force=True)
            raise ExternalToolError("ExifTool ne répond plus", tool="exiftool")
        return result[0]

    def version(self) -> str | None:
        try:
            out = subprocess.run(
                [self.path, "-ver"], capture_output=True, timeout=20, check=True,
                creationflags=CREATE_NO_WINDOW,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.decode().strip() or None

    def close(self, *, force: bool = False) -> None:
        with self._lock if not force else _NullLock():
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            if not force and proc.stdin is not None:
                proc.stdin.write(b"-stay_open\nFalse\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if proc.poll() is None:
            proc.kill()


class _NullLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None
