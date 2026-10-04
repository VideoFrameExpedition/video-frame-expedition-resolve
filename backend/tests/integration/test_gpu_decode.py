"""GPU lending: only with room to spare next to the vision model, CPU otherwise.

No test here touches the GPU: the VRAM probe is fake and the hardware path is exercised with
a hardware acceleration ffmpeg does not know, which fails the way an unusable GPU does.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from vfe_vision.adapters.ffmpeg.tools import (
    Ffmpeg,
    gpu_decodes,
    gpu_download_format,
    iter_frames,
)
from vfe_vision.adapters.gpu.nvidia_smi import NvidiaSmi
from vfe_vision.pipeline.gpu import GpuGate, decode_need_mib

UNUSABLE_GPU = "vfe_no_such_accel"


class FakeProbe:
    def __init__(self, free: int | None) -> None:
        self.free = free
        self.calls = 0

    def free_mib(self) -> int | None:
        self.calls += 1
        return self.free


def test_decode_need_grows_with_the_picture() -> None:
    assert decode_need_mib(3840, 2160) == 585
    assert decode_need_mib(1920, 1080) == 372
    assert decode_need_mib(None, None) == decode_need_mib(3840, 2160)  # unknown: assume 4K


def test_the_gpu_is_lent_only_with_the_margin_left_free() -> None:
    need = decode_need_mib(3840, 2160)
    # The 8 GB-class vision model loaded: 270 MiB free, far from enough.
    with GpuGate(FakeProbe(270), margin_mib=1024).borrow(need) as gpu:
        assert gpu is False
    with GpuGate(FakeProbe(need + 1023), margin_mib=1024).borrow(need) as gpu:
        assert gpu is False
    with GpuGate(FakeProbe(need + 1024), margin_mib=1024).borrow(need) as gpu:
        assert gpu is True
    with GpuGate(FakeProbe(None)).borrow(need) as gpu:  # no NVIDIA GPU or driver
        assert gpu is False
    with GpuGate().borrow(need) as gpu:  # no probe configured (tests, other machines)
        assert gpu is False


def test_switched_off_or_busy_means_cpu_without_asking_the_driver() -> None:
    probe = FakeProbe(10_000)
    gate = GpuGate(probe, margin_mib=1024)
    with gate.borrow(500, enabled=False) as gpu:
        assert gpu is False
    assert probe.calls == 0

    inside = threading.Event()
    release = threading.Event()
    other: list[bool] = []

    def first() -> None:
        with gate.borrow(500) as gpu:
            assert gpu is True
            inside.set()
            release.wait(5)

    worker = threading.Thread(target=first)
    worker.start()
    assert inside.wait(5)
    with gate.borrow(500) as gpu:  # a second video does not wait for the GPU
        other.append(gpu)
    release.set()
    worker.join(5)
    assert other == [False]
    with gate.borrow(500) as gpu:  # free again
        assert gpu is True


def test_nvidia_smi_absent_is_asked_once(tmp_path: Path) -> None:
    probe = NvidiaSmi(str(tmp_path / "nvidia-smi.exe"))
    assert probe.free_mib() is None
    assert probe._absent


def test_the_gpu_is_only_asked_for_what_its_decoder_handles() -> None:
    assert gpu_decodes("hevc", "yuv420p10le")  # S26 Ultra HDR10+
    assert gpu_decodes("h264", "yuv420p")
    assert not gpu_decodes("apv", "yuv422p10le")  # S26 Ultra APV (Samsung Log): CPU
    assert not gpu_decodes("prores", "yuv422p10le")
    assert not gpu_decodes("hevc", "yuv422p10le")  # 4:2:2 needs a newer GPU
    assert not gpu_decodes("h264", "yuv420p10le")  # RTX 30: H.264 in 8 bits only
    assert not gpu_decodes("mpeg2video", "yuv420p")  # not bit-exact on the GPU
    assert not gpu_decodes("av1", "yuv420p")  # libdav1d is picked before the hardware
    assert not gpu_decodes(None, None)


def test_frames_are_thinned_on_the_gpu_only_when_safe() -> None:
    assert gpu_download_format("yuv420p10le", 0) == "p010le"  # S26 Ultra HDR10+ HEVC
    assert gpu_download_format("yuv420p", 360) == "nv12"
    assert gpu_download_format("yuv420p", 90) is None  # rotation needs frames in system memory
    assert gpu_download_format("yuv420p", 0, flipped=True) is None  # so does a mirror
    assert gpu_download_format("yuv422p10le", 0) is None  # not a format NVDEC hands back
    assert gpu_download_format(None, 0) is None


@pytest.mark.parametrize("gpu_download", [None, "nv12"])
def test_unusable_gpu_falls_back_to_the_cpu_for_frames(
    sample_video: Path, gpu_download: str | None
) -> None:
    report: dict[str, str] = {}
    frames = list(
        iter_frames(
            "ffmpeg", sample_video, fps=2, size=(64, 36), hwaccel=UNUSABLE_GPU,
            gpu_download=gpu_download, report=report,
        )
    )  # fmt: skip
    reference = list(iter_frames("ffmpeg", sample_video, fps=2, size=(64, 36)))
    assert report == {"decoder": "cpu"}
    assert len(frames) == len(reference) > 0
    assert all((a.rgb == b.rgb).all() for a, b in zip(frames, reference, strict=True))


def test_a_gpu_failing_midway_is_finished_by_the_cpu(
    sample_video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vfe_vision.adapters.ffmpeg import tools
    from vfe_vision.core.errors import ExternalToolError

    real = tools._stream_frames

    def flaky(args: list[str], *rest: Any) -> Any:
        frames = real(args, *rest)
        if "-hwaccel" not in args:
            yield from frames
            return
        for count, frame in enumerate(frames):
            if count == 3:
                raise ExternalToolError("NVDEC error", tool="ffmpeg")
            yield frame

    monkeypatch.setattr(tools, "_stream_frames", flaky)
    report: dict[str, str] = {}
    frames = list(
        iter_frames("ffmpeg", sample_video, fps=2, size=(64, 36), hwaccel="cuda", report=report)
    )
    reference = list(iter_frames("ffmpeg", sample_video, fps=2, size=(64, 36)))
    assert report == {"decoder": "cuda+cpu"}
    assert [f.index for f in frames] == [f.index for f in reference]  # no gap, no repeat
    assert all((a.rgb == b.rgb).all() for a, b in zip(frames, reference, strict=True))


def test_nvidia_smi_failures_fall_back_to_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    from vfe_vision.adapters.gpu import nvidia_smi

    calls = []

    def denied(*_args: Any, **_kwargs: Any) -> Any:
        calls.append(1)
        raise PermissionError("Accès refusé")

    monkeypatch.setattr(nvidia_smi, "run_process", denied)
    probe = NvidiaSmi("nvidia-smi")
    assert probe.free_mib() is None
    assert probe.free_mib() is None  # not asked again for a while
    assert calls == [1]


def test_unusable_gpu_falls_back_to_the_cpu_for_candidates(
    sample_video: Path, tmp_path: Path
) -> None:
    report: dict[str, str] = {}
    candidates = Ffmpeg().extract_candidates(
        sample_video, tmp_path / "c", long_side=160, hwaccel=UNUSABLE_GPU, report=report
    )
    assert report == {"decoder": "cpu"}
    assert candidates
    assert all(c.path.is_file() for c in candidates)


@pytest.mark.gpu
def test_real_gpu_decode_gives_the_cpu_pixels(sample_video: Path, tmp_path: Path) -> None:
    """Run by hand (-m gpu) only when the vision model leaves room: check nvidia-smi first."""
    free = NvidiaSmi().free_mib()
    if free is None or free < decode_need_mib(3840, 2160) + 1024:
        pytest.skip(f"not enough free VRAM next to the vision model ({free} MiB)")
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(rotated)],
        check=True,
    )  # fmt: skip
    for video, size, rotation in ((sample_video, (64, 36), 0), (rotated, (36, 64), 90)):
        cpu = [f.rgb for f in iter_frames("ffmpeg", video, fps=2, size=size)]
        download = gpu_download_format("yuv420p", rotation)
        report: dict[str, str] = {}
        gpu = [
            f.rgb
            for f in iter_frames(
                "ffmpeg", video, fps=2, size=size, hwaccel="cuda", gpu_download=download,
                report=report,
            )
        ]  # fmt: skip
        assert report == {"decoder": "cuda"}
        assert len(gpu) == len(cpu) > 0
        assert all((a == b).all() for a, b in zip(gpu, cpu, strict=True)), video.name
