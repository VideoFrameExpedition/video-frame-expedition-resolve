"""What changes on a Mac: the paths, the Resolve library, the process job, the desktop pieces.

The macOS branches are checked everywhere by pretending the platform; the process tests of the
POSIX job (process groups, lifeline) run on macOS and Linux only."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import textwrap
import time
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from vfe_vision.adapters import folder_picker, tailscale
from vfe_vision.adapters.gpu.nvidia_smi import NvidiaSmi
from vfe_vision.adapters.resolve import reader
from vfe_vision.api.routers import access
from vfe_vision.core import config, paths, procs
from vfe_vision.core.procs import KillOnCloseJob, ProcessResult
from vfe_vision.domain.clip_paths import canonical_path, match_key
from vfe_vision.services import system

POSIX = sys.platform != "win32"


class TestClipPaths:
    def test_a_mac_keeps_its_own_paths_and_refuses_a_pc_s(self) -> None:
        own = "/Volumes/Rushs/été/clip.MP4"
        assert canonical_path(own, style="posix") == own
        assert canonical_path("/Volumes/Rushs//été/./clip.MP4", style="posix") == own
        assert canonical_path("D:\\Rushs\\clip.MP4", style="posix") is None
        assert canonical_path(r"\\nas\rushs\clip.MP4", style="posix") is None
        assert canonical_path("relative/clip.MP4", style="posix") is None

    def test_a_pc_keeps_its_own_paths_and_refuses_a_mac_s(self) -> None:
        assert canonical_path("D:/Rushs/clip.MP4", style="windows") == "D:\\Rushs\\clip.MP4"
        assert canonical_path("/Volumes/Rushs/clip.MP4", style="windows") is None

    def test_accents_are_spelled_one_way(self) -> None:
        assert match_key("/Volumes/Rushs/e\u0301te\u0301.mp4") == match_key(
            "/Volumes/Rushs/été.mp4"
        )


class TestResolveOnAMac:
    def test_the_library_and_the_process_have_their_mac_names(self) -> None:
        library = reader.DEFAULT_LIBRARIES["darwin"]
        assert library.endswith("DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
        assert reader.RESOLVE_PROCESS["darwin"] == "Resolve"

    def test_pgrep_tells_whether_resolve_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []
        state = {"running": True}

        def fake_run(args: list[str], **_kwargs: Any) -> procs.ProcessResult:
            calls.append(args)
            running = state["running"]
            return procs.ProcessResult(0 if running else 1, b"4242\n" if running else b"", b"", 0.0)

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(reader, "run_process", fake_run)
        assert reader.resolve_running() is True
        state["running"] = False
        assert reader.resolve_running() is False
        assert calls == [["pgrep", "-x", "Resolve"]] * 2


class TestConnectionsOnAMac:
    def test_claude_desktop_lives_in_application_support(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        folder = tmp_path / "Library" / "Application Support" / "Claude"
        folder.mkdir(parents=True)
        (folder / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
        [entry] = access.claude_desktop_configs()
        assert (entry.kind, entry.installed, entry.exists) == ("classic", True, True)
        assert entry.path == str(folder / "claude_desktop_config.json")
        assert config.platform_name() == "macos"

    def test_resolve_s_mcp_is_looked_for_in_its_bundle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        found = access.resolve_mcp()
        assert Path(found.path) in access.RESOLVE_MCP_MACOS
        assert found.installed is Path(found.path).is_file()


class TestToolsOnAMac:
    def test_homebrew_folders_are_searched_for_a_tool(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        tool = tmp_path / "ffmpeg"
        tool.write_text("#!/bin/sh\n", encoding="utf-8")
        tool.chmod(0o755)
        found = partial(config.find_tool, places=(str(tmp_path),), ffmpeg_places=())
        assert found("ffmpeg") == str(tool)
        assert found("exiftool") == "exiftool"
        assert found(str(tool)) == str(tool)  # a path is kept as given

    def test_ffmpeg_s_full_build_comes_before_the_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
        for name in ("ffmpeg", "ffprobe"):
            (tmp_path / name).write_text("#!/bin/sh\n", encoding="utf-8")
            (tmp_path / name).chmod(0o755)
        full = (str(tmp_path),)
        assert config.find_tool("ffmpeg", ffmpeg_places=full) == str(tmp_path / "ffmpeg")
        assert config.find_tool("ffprobe", ffmpeg_places=full) == str(tmp_path / "ffprobe")
        assert config.find_tool("exiftool", ffmpeg_places=full) == "exiftool"  # in the PATH
        assert config.find_tool("ffmpeg", ffmpeg_places=()) == "ffmpeg"  # no full build

    @pytest.mark.parametrize(
        ("listing", "status"),
        [
            (b" T.. zscale    V->V    Apply resizing, colorspace and bit depth.\n", "ok"),
            (b" T.. scale     V->V    Scale the input video size.\n", "warning"),
        ],
    )
    def test_the_doctor_tells_whether_hdr_videos_can_be_converted(
        self, monkeypatch: pytest.MonkeyPatch, listing: bytes, status: str
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(
            system, "run_process", lambda *_a, **_k: ProcessResult(0, listing, b"", 0.1)
        )
        check = system._hdr_filter("ffmpeg")
        assert (check.id, check.status) == ("ffmpeg_hdr", status)
        if status == "warning":
            assert check.hint is not None
            assert "brew install ffmpeg-full" in check.hint

    def test_tailscale_s_places_on_a_mac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        first = tailscale.default_places()[0]
        assert first == Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")

    def test_no_nvidia_probe_on_a_mac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        probe = NvidiaSmi("nvidia-smi")
        assert probe.free_mib() is None
        assert probe.usage() is None

    def test_the_finder_s_dialog_is_an_applescript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        args, script = folder_picker.picker_command("Choisir un dossier", "/Users/me/Movies")
        assert args == [folder_picker.OSASCRIPT, "-", "Choisir un dossier", "/Users/me/Movies"]
        assert script is not None
        assert b"choose folder" in script
        assert b"POSIX path" in script


class TestVolumesOnAMac:
    def test_an_unmounted_volume_is_not_a_deleted_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert paths.unmounted_volume("/Volumes/Rushs absents de ce test/a.mp4") is True
        assert paths.unmounted_volume("/Users/me/Movies/a.mp4") is False
        assert paths.unmounted_volume("/Volumes") is False

    def test_the_volume_is_known_by_its_uuid(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plist = plistlib.dumps({"VolumeUUID": "0123ABCD-0000-4000-8000-000000000000"})

        def found(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            assert args[:3] == ["/usr/sbin/diskutil", "info", "-plist"]
            return subprocess.CompletedProcess(args, 0, plist, b"")

        monkeypatch.setattr(subprocess, "run", found)
        assert paths.macos_volume_id(tmp_path) == "0123ABCD-0000-4000-8000-000000000000"

        def not_a_disk(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args, 1, b"", b"Could not find disk")

        monkeypatch.setattr(subprocess, "run", not_a_disk)  # a network share
        assert paths.macos_volume_id(tmp_path) == f"mount:{paths.mount_point(tmp_path)}"


def test_ps_clock_values_become_seconds() -> None:
    assert procs._clock_seconds("0:01.23") == pytest.approx(1.23)
    assert procs._clock_seconds("01:02:03") == 3723
    assert procs._clock_seconds("1-00:00:10") == 86410


# ---------------------------------------------------------------- the POSIX job
def _alive(pid: int) -> bool:
    """Whether the process still runs (a zombie waiting to be reaped counts as gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    done = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return bool(done.stdout.strip()) and not done.stdout.strip().startswith("Z")


def _gone(pid: int, within_s: float = 5.0) -> bool:
    deadline = time.monotonic() + within_s
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


@pytest.mark.skipif(not POSIX, reason="process groups and the lifeline (macOS, Linux)")
class TestPosixJob:
    CHILD = textwrap.dedent(
        """
        import os, subprocess, sys, time
        from vfe_vision.core.procs import watch_lifeline
        grandchild = subprocess.Popen(["sleep", "60"])
        watched = watch_lifeline()
        print(os.getpid(), grandchild.pid, int(watched), flush=True)
        time.sleep(60)
        """
    )

    def _start(self, job: KillOnCloseJob) -> tuple[subprocess.Popen[bytes], int, int, bool]:
        proc = subprocess.Popen(
            [sys.executable, "-c", self.CHILD],
            stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=job.child_env(),
            **job.popen_kwargs(),
        )
        assert proc.stdout is not None
        pid, grandchild, watched = (int(x) for x in proc.stdout.readline().split())
        return proc, pid, grandchild, bool(watched)

    def test_closing_the_job_kills_the_child_and_its_grandchild(self) -> None:
        job = KillOnCloseJob()
        proc, pid, grandchild, _watched = self._start(job)
        try:
            assert job.assign(proc)
            assert job.contains(pid) is True
            assert job.contains(grandchild) is True  # the same process group
            assert job.contains(os.getpid()) is False
            assert job.ensure_pid(pid)
            cpu = job.cpu_seconds()
            assert cpu is not None
            assert cpu >= 0.0
        finally:
            job.close()
            proc.wait(10)
        assert _gone(pid)
        assert _gone(grandchild)
        assert job.contains(pid) is None  # closed job: cannot tell

    def test_a_child_watching_the_lifeline_ends_when_its_parent_is_gone(self) -> None:
        job = KillOnCloseJob()
        proc, pid, grandchild, watched = self._start(job)
        assert watched
        # The parent dies: its end of the pipe closes, nothing is killed on purpose.
        assert job._lifeline is not None
        read_end, write_end = job._lifeline
        job._lifeline = None
        os.close(write_end)
        os.close(read_end)
        assert proc.wait(15) is not None
        assert _gone(pid)
        assert _gone(grandchild)

    def test_a_child_of_our_own_group_is_killed_alone(self) -> None:
        job = KillOnCloseJob()
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL
        )
        try:
            assert job.assign(proc)  # no process_group: it shares ours
            assert job.contains(proc.pid) is True
            assert job.contains(os.getpid()) is False  # never our own group
        finally:
            job.close()
        assert proc.wait(10) is not None
        assert _gone(proc.pid)
