"""Scripted stand-in for the ASR child: a real process speaking the runner's protocol, no model.

Run as ``python -X utf8 asr_child_fake.py <scenario> [pid_file]``. Standard library only, so it
starts in a few tens of milliseconds. ``pid_file`` receives ``{"pid": …, "grandchild": …}`` in
the scenarios that must leave nothing behind when killed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 3


def emit(event: str, **fields: Any) -> None:
    line = json.dumps({"v": PROTOCOL_VERSION, "event": event, **fields}, ensure_ascii=False)
    sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def raw(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def read_request() -> dict[str, Any]:
    request: dict[str, Any] = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    return request


def segment(idx: int, start: float, end: float, text: str, **extra: Any) -> dict[str, Any]:
    words = text.split()
    step = (end - start) / max(1, len(words))
    return {
        "idx": idx,
        "start": start,
        "end": end,
        "text": text,
        "avg_logprob": -0.2,
        "no_speech_prob": 0.0,
        "compression_ratio": 1.3,
        "temperature": 0.0,
        "language": "fr",
        "words": [
            [round(start + i * step, 3), round(start + (i + 1) * step, 3), " " + w, 0.95]
            for i, w in enumerate(words)
        ],
        "suspect": False,
        "reasons": [],
        "pass": 1,
        **extra,
    }


def spawn_grandchild() -> subprocess.Popen[bytes]:
    """A long-lived non-Python process, like the ffmpeg decoder of the real runner."""
    args = ["ping", "-n", "120", "127.0.0.1"] if sys.platform == "win32" else ["sleep", "120"]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_pids(pid_file: str | None, grandchild: subprocess.Popen[bytes] | None) -> None:
    if pid_file:
        payload = {"pid": os.getpid(), "grandchild": grandchild.pid if grandchild else None}
        Path(pid_file).write_text(json.dumps(payload), encoding="utf-8")


def scenario_ok(pid_file: str | None) -> int:
    print("stray output before the protocol", flush=True)  # a library printing to stdout
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    request = read_request()
    raw(b'{"v": 1, "event": "phase"\n')  # truncated JSON
    raw(b"[1, 2, 3]\n")  # valid JSON, not an event
    raw(b'{"v": 99, "event": "done", "stats": {}}\n')  # another protocol version
    raw(b"\n")
    emit("phase", name="decode")
    emit("phase", name="vad")
    emit("audio", duration_s=10.0, speech_s=7.5, chunks=2)
    emit("phase", name="load")
    emit("loaded", load_s=0.01, model="fake/whisper")
    emit("phase", name="language")
    emit("info", language="fr", probability=0.97, top=[["fr", 0.97], ["en", 0.02]])
    emit("phase", name="transcribe")
    emit("segment", **segment(0, 0.5, 3.0, "Bonjour à tous"))
    emit("progress", fraction=0.3)
    emit("segment", **segment(1, 6.0, 9.5, "on fait cuire le riz", suspect=True))
    emit("progress", fraction=0.95)
    emit("pass2", clips=[[2.0, 7.0]], gaps=[[3.0, 6.0]])
    emit("segment", **segment(2, 3.2, 5.8, "ou un petit oiseau", **{"pass": 2}))
    emit("done", stats={"request": request, "model": "fake/whisper"})
    return 0


def scenario_no_speech(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    emit("phase", name="decode")
    emit("audio", duration_s=6.0, speech_s=0.2, chunks=1)
    emit("no_speech", speech_s=0.2)
    emit("done", stats={"model": "fake/whisper"})
    return 0


def scenario_hang(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    grandchild = spawn_grandchild()
    write_pids(pid_file, grandchild)
    emit("phase", name="decode")
    time.sleep(600)
    return 0


def scenario_mute(pid_file: str | None) -> int:
    """Never says hello (e.g. stuck importing a DLL)."""
    write_pids(pid_file, None)
    time.sleep(600)
    return 0


def scenario_exit3(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    emit("phase", name="decode")
    sys.stderr.write("Traceback (most recent call last):\nRuntimeError: boom in the child\n")
    sys.stderr.flush()
    return 3


def scenario_dll(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    sys.stderr.write(
        "ImportError: DLL load failed while importing _ext: "
        "Une stratégie de contrôle d'application a bloqué ce fichier.\n"
    )
    sys.stderr.flush()
    emit("error", type="ImportError", message="DLL load failed while importing _ext")
    return 2


def scenario_error(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    emit("error", type="NoAudioStream", message="aucune piste audio dans ce fichier")
    return 2


def scenario_no_done(pid_file: str | None) -> int:
    """Exits 0 with partial output, as a child killed by its Job Object does."""
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    emit("audio", duration_s=10.0, speech_s=7.5, chunks=2)
    emit("segment", **segment(0, 0.5, 3.0, "Bonjour"))
    return 0


def scenario_slow(pid_file: str | None) -> int:
    """Keeps making progress (never stalls) until killed."""
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    grandchild = spawn_grandchild()
    write_pids(pid_file, grandchild)
    emit("audio", duration_s=3600.0, speech_s=3000.0, chunks=100)
    emit("loaded", load_s=0.01, model="fake/whisper")
    emit("info", language="fr", probability=0.9, top=[["fr", 0.9]])
    for i in range(3000):
        emit("progress", fraction=i / 3000)
        time.sleep(0.05)
    return 0


def scenario_busy(pid_file: str | None) -> int:
    """Silent for a while but computing (a starved low-priority child), then finishes."""
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    emit("audio", duration_s=6.0, speech_s=0.2, chunks=1)
    until = time.monotonic() + 4.0
    total = 0
    while time.monotonic() < until:  # CPU work, no event
        total += sum(i * i for i in range(10_000))
    emit("no_speech", speech_s=0.2)
    emit("done", stats={"model": "fake/whisper", "work": total % 7})
    return 0


def _ready(request: dict[str, Any]) -> bool:
    """A cuda request: ready, then wait for the parent's load (False: refused)."""
    emit("audio", duration_s=10.0, speech_s=7.5, chunks=2)
    if request.get("device") != "cuda":
        emit("error", type="BadRequest", message=f"device {request.get('device')!r}")
        return False
    emit("ready")
    return sys.stdin.buffer.readline().strip() == b"load"


def _warm_up(request: dict[str, Any]) -> bool:
    """Ready, loaded, warm, then wait for the parent's go (False: refused)."""
    if not _ready(request):
        return False
    emit("loaded", load_s=0.01, model="fake/whisper")
    emit("warm", load_s=0.01)
    return sys.stdin.buffer.readline().strip() == b"go"


def scenario_gpu_ok(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    request = read_request()
    if not _warm_up(request):
        return 2
    emit("info", language="fr", probability=0.97, top=[["fr", 0.97]])
    emit("segment", **segment(0, 0.5, 3.0, "Bonjour à tous"))
    emit("done", stats={
        "model": "fake/whisper", "device": request["device"],
        "cuda_dll_dir": request.get("cuda_dll_dir"),
        "visible": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "order": os.environ.get("CUDA_DEVICE_ORDER"),
        "allocator": os.environ.get("CT2_CUDA_ALLOCATOR"),
    })  # fmt: skip
    return 0


def scenario_gpu_long(pid_file: str | None) -> int:
    """Warm, then keeps transcribing for a long while (the memory watch must stop it)."""
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    if not _warm_up(read_request()):
        return 2
    write_pids(pid_file, None)
    emit("info", language="fr", probability=0.97, top=[["fr", 0.97]])
    for i in range(600):
        emit("progress", fraction=i / 600)
        time.sleep(0.05)
    return 0


def scenario_gpu_slow_load(pid_file: str | None) -> int:
    """Loads for a long while before its warm-up (the memory must be watched meanwhile)."""
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    if not _ready(read_request()):
        return 2
    write_pids(pid_file, None)
    emit("phase", name="load")
    time.sleep(30)
    return 0


def scenario_gpu_bad_audio(pid_file: str | None) -> int:
    """The audio cannot be decoded: nothing to do with the GPU."""
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    read_request()
    emit("error", type="AudioDecodeError", message="décodage audio impossible")
    return 2


def scenario_gpu_unavailable(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    if not _ready(read_request()):
        return 2
    emit("error", type="CudaUnavailable", message="cublas64_12.dll ne se charge pas")
    return 2


def scenario_gpu_crash(pid_file: str | None) -> int:
    emit("hello", pid=os.getpid(), protocol=PROTOCOL_VERSION)
    if not _warm_up(read_request()):
        return 2
    sys.stderr.write("CUDA driver crash\n")
    sys.stderr.flush()
    return 3


SCENARIOS = {
    "ok": scenario_ok,
    "gpu_ok": scenario_gpu_ok,
    "gpu_long": scenario_gpu_long,
    "gpu_unavailable": scenario_gpu_unavailable,
    "gpu_slow_load": scenario_gpu_slow_load,
    "gpu_bad_audio": scenario_gpu_bad_audio,
    "gpu_crash": scenario_gpu_crash,
    "no_speech": scenario_no_speech,
    "hang": scenario_hang,
    "mute": scenario_mute,
    "exit3": scenario_exit3,
    "dll": scenario_dll,
    "error": scenario_error,
    "no_done": scenario_no_done,
    "slow": scenario_slow,
    "busy": scenario_busy,
}


def main() -> int:
    name = sys.argv[1]
    pid_file = sys.argv[2] if len(sys.argv) > 2 else None
    return SCENARIOS[name](pid_file)


if __name__ == "__main__":
    sys.exit(main())
