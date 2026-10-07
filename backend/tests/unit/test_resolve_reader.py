"""Reading DaVinci Resolve: what the child answers, and what the user is told when
Resolve cannot be read. The child itself runs against a fake scripting library."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from vfe_vision.adapters.resolve import reader, timeline_reader
from vfe_vision.adapters.resolve.reader import (
    CHANGED,
    CHILD_SCRIPT,
    NO_PROJECT,
    NO_TIMELINE,
    NOT_INSTALLED,
    NOT_RESPONDING,
    NOT_RUNNING,
    SCRIPTING_OFF,
    STARTING,
    ResolveReader,
    TimelineSnapshots,
    answer_of,
    project_from_json,
    timeline_content_from_json,
)
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import (
    ConflictError,
    ExternalToolError,
    NotFoundError,
    ResolveUnavailableError,
)
from vfe_vision.domain.resolve_timeline import ClipKind

PROJECT: dict[str, Any] = {
    "ok": True,
    "product": "DaVinci Resolve Studio",
    "version": "21.1.0.17",
    "database": {"type": "Disk", "name": "Local Database"},
    "project": {"id": "7c52df83", "name": "cats 2026"},
    "current_timeline_id": "f50b89b8",
    "timelines": [
        {
            "id": "f50b89b8", "name": "Timeline 1", "fps": 29.97, "drop_frame": False,
            "start_frame": 108000, "end_frame": 249838, "start_timecode": "01:00:00:00",
            "width": 1920, "height": 1080, "video_tracks": 1, "audio_tracks": 1,
            "video_clips": 98, "is_current": True,
        }
    ],
}  # fmt: skip


def _error(code: str, running: bool | None = True) -> Exception:
    with pytest.raises(Exception) as caught:  # noqa: PT011 - the type is what is checked
        answer_of(
            json.dumps({"ok": False, "error": code, "message": "x"}),
            resolve_running=lambda: running,
        )
    return caught.value


def test_a_good_answer_is_returned() -> None:
    assert (
        answer_of(json.dumps(PROJECT), resolve_running=lambda: True)["project"]["name"]
        == "cats 2026"
    )


@pytest.mark.parametrize(
    ("code", "running", "reason", "detail"),
    [
        ("not_installed", True, "not_installed", NOT_INSTALLED),
        ("no_connection", False, "not_running", NOT_RUNNING),
        ("no_connection", True, "scripting_off", SCRIPTING_OFF),
        ("no_connection", None, "scripting_off", SCRIPTING_OFF),
        ("no_project", True, "no_project", NO_PROJECT),
        ("starting", True, "starting", STARTING),
        ("no_timeline", True, "no_timeline", NO_TIMELINE),
    ],
)
def test_why_resolve_cannot_be_read(
    code: str, running: bool | None, reason: str, detail: str
) -> None:
    error = _error(code, running)
    assert isinstance(error, ResolveUnavailableError)
    assert error.status == 503
    assert error.detail == detail
    assert error.extra["reason"] == reason


def test_a_timeline_gone_is_not_found() -> None:
    assert isinstance(_error("timeline_not_found"), NotFoundError)


def test_a_timeline_changed_while_read_is_a_conflict() -> None:
    error = _error("changed_during_read")
    assert isinstance(error, ConflictError)
    assert error.detail == CHANGED


@pytest.mark.parametrize(
    "stdout", ["", "not json", "[1, 2]", json.dumps({"ok": False, "error": "api_error"})]
)
def test_anything_else_is_a_tool_error(stdout: str) -> None:
    with pytest.raises(ExternalToolError):
        answer_of(stdout, resolve_running=lambda: True)


def test_project_answer() -> None:
    info = project_from_json(PROJECT)
    assert info.project.name == "cats 2026"
    assert info.database.name == "Local Database"
    assert info.current_timeline_id == "f50b89b8"
    (timeline,) = info.timelines
    assert timeline.is_current
    assert timeline.video_clips == 98
    assert round(timeline.duration_s) == 4733


def test_a_project_without_id_is_refused() -> None:
    with pytest.raises(ExternalToolError):
        project_from_json({**PROJECT, "project": {"name": "x"}})


def test_timeline_answer_keeps_every_clip() -> None:
    data = {
        **{k: PROJECT[k] for k in ("product", "version", "database", "project")},
        "timeline": PROJECT["timelines"][0],
        "clips": [
            {
                "file_path": r"D:\cats 2026\hdr\20260607_191341.mp4",
                "clip_type": "Video + Audio",
                "use": {"track_type": "video", "track": 1, "record_start_frame": 108000, "record_end_frame": 108662,
                        "source_start_s": 0.0, "source_end_s": 22.0887, "media_pool_item_id": "72234098",
                        "name": "20260607_191341.mp4", "enabled": True},
            },
            {"file_path": None, "clip_type": None, "use": {"name": "Titre", "record_start_frame": 108662}},
            "garbage",
        ],
        "errors": 0,
    }  # fmt: skip
    content = timeline_content_from_json(data)
    assert content.timeline.name == "Timeline 1"
    assert [c.file_path for c in content.clips] == [r"D:\cats 2026\hdr\20260607_191341.mp4", None]
    assert content.clips[0].use.media_pool_item_id == "72234098"


def test_a_missing_library_is_told_without_starting_a_child(tmp_path: Path) -> None:
    source = ResolveReader(
        Settings(data_dir=tmp_path, resolve_script_lib=str(tmp_path / "none.dll"))
    )
    with pytest.raises(ResolveUnavailableError) as caught:
        source.project()
    assert caught.value.extra["reason"] == "not_installed"


# ---------------------------------------------------------------- the child, on a fake library
FAKE_LIBRARY = textwrap.dedent(
    '''
    """Stands for Resolve's fusionscript: a project « cats 2026 » with a timeline « Montage été »
    (a clip, a title, a transition, a nested timeline, a multicam clip, a broken item, a disabled
    track, linked audio). FAKE_RESOLVE_MODE picks a situation."""

    import atexit
    import os
    import time

    MODE = os.environ.get("FAKE_RESOLVE_MODE", "")
    CALLS = {"props": 0, "project_id": 0}
    if os.environ.get("FAKE_PID_FILE"):
        with open(os.environ["FAKE_PID_FILE"], "w") as out:
            out.write(str(os.getpid()))
    if os.environ.get("FAKE_COUNT_FILE"):
        atexit.register(lambda: open(os.environ["FAKE_COUNT_FILE"], "w").write(str(CALLS["props"])))


    class Pool:
        def __init__(self, uid, path, kind="Video + Audio", timeline=None, video_id=None):
            self.uid, self.path, self.kind, self.timeline, self.video_id = uid, path, kind, timeline, video_id

        def GetClipProperty(self, key=None):
            CALLS["props"] += 1
            props = {"File Path": self.path, "Type": self.kind, "FPS": "29.97", "Start TC": "00:00:10:00"}
            return props if key is None else props[key]

        def GetUniqueId(self):
            return self.uid

        def GetTimeline(self):
            return self.timeline

        def GetThirdPartyMetadata(self, key=None):
            return self.video_id or ""


    class Item:
        def __init__(self, name, start, end, pool, kind="video", left=0, broken=False):
            self.name, self.start, self.end, self.pool = name, start, end, pool
            self.kind, self.left, self.broken = kind, left, broken

        def GetName(self):
            if self.broken:
                raise RuntimeError("broken item")
            return self.name

        def GetType(self):
            return self.kind

        def GetUniqueId(self):
            return "item-" + self.name + "-" + str(self.start)

        def GetStart(self):
            return self.start

        def GetEnd(self):
            return self.end

        def GetMediaPoolItem(self):
            return self.pool

        def GetSourceStartTime(self):
            return 10.0 + self.left / 29.97

        def GetSourceEndTime(self):
            return 10.0 + (self.left + self.end - self.start) / 29.97

        def GetLeftOffset(self):
            return self.left

        def GetClipEnabled(self):
            return True


    class Timeline:
        def __init__(self, uid, name, tracks, disabled=()):
            self.uid, self.name, self.tracks, self.disabled = uid, name, tracks, disabled

        def GetName(self):
            return self.name

        def GetUniqueId(self):
            return self.uid

        def GetSetting(self, key):
            return {"timelineFrameRate": 29.97, "timelineDropFrameTimecode": "0",
                    "timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080"}[key]

        def GetStartFrame(self):
            return 108000

        def GetEndFrame(self):
            return 108300

        def GetStartTimecode(self):
            return "01:00:00:00"

        def GetTrackCount(self, kind):
            return len([t for t in self.tracks if t[0] == kind])

        def _track(self, kind, index):
            return [t for t in self.tracks if t[0] == kind][index - 1]

        def GetTrackName(self, kind, index):
            return kind.title() + " " + str(index)

        def GetIsTrackEnabled(self, kind, index):
            return (kind, index) not in self.disabled

        def GetItemListInTrack(self, kind, index):
            items = self._track(kind, index)[1]
            if MODE == "changing" and CALLS["project_id"] > 1:
                return items + items[:1]  # someone edits the timeline while it is read
            return items


    ETE = "D:\\\\r\\\\\\u00e9t\\u00e9.mp4"
    A = Pool("p1", ETE, video_id="01video")
    INNER = Timeline("t2", "Imbriqu\\u00e9e", [
        ("video", [Item("b.mp4", 108000, 108100, Pool("p5", "D:\\\\r\\\\b.mp4")),
                   Item("c.mp4", 108100, 108200, Pool("p6", "D:\\\\r\\\\c.mp4"))]),
    ])
    if MODE == "big":
        POOLS = [Pool("big%d" % i, "D:\\\\r\\\\big%d.mp4" % i) for i in range(100)]
        V1 = [Item("big%d" % i, 108000 + i, 108001 + i, POOLS[i % 100]) for i in range(3000)]
        A1 = [Item("big%d" % i, 108000 + i, 108001 + i, POOLS[i % 100], kind="audio") for i in range(3000)]
        MAIN = Timeline("t1", "Grosse", [("video", V1), ("audio", A1)])
    else:
        MAIN = Timeline("t1", "Montage \\u00e9t\\u00e9", [
            ("video", [
                Item("a.mp4", 108000, 108075, A),
                Item("Transition", 108070, 108080, None, kind="transition"),
                Item("Titre", 108075, 108100, None, kind="generator"),
                Item("Nest", 108100, 108160, Pool("p3", "", kind="Timeline", timeline=INNER), left=30),
                Item("MC", 108160, 108200, Pool("p4", "", kind="Multicam Clip")),
                Item("x", 108200, 108250, Pool("p2", "", kind="Video"), broken=True),
            ]),
            ("video", [Item("d.mov", 108000, 108050, Pool("p7", "D:\\\\r\\\\d.mov"))]),
            ("audio", [Item("a.mp4", 108000, 108075, A, kind="audio")]),
        ], disabled={("video", 2)})


    class Project:
        def GetName(self):
            return "cats 2026"

        def GetUniqueId(self):
            CALLS["project_id"] += 1
            if MODE == "changing" and CALLS["project_id"] > 1:
                return "p-other"
            return "p-cats"

        def GetTimelineCount(self):
            return 1

        def GetTimelineByIndex(self, index):
            return MAIN

        def GetCurrentTimeline(self):
            if MODE == "other_open":
                return INNER
            return None if MODE == "no_timeline" else MAIN


    class Manager:
        def GetCurrentProject(self):
            return None if MODE == "no_project" else Project()

        def GetCurrentDatabase(self):
            return {"DbType": "Disk", "DbName": "Local Database"}


    class Resolve:
        def GetProjectManager(self):
            return None if MODE == "starting" else Manager()

        def GetProductName(self):
            return "DaVinci Resolve Studio"

        def GetVersionString(self):
            return "21.1.0.17"

        def IsStudio(self):
            return True


    def scriptapp(name, host=None):
        if MODE == "hang":
            time.sleep(3600)  # a modal dialog open in Resolve: nothing ever comes back
        return None if MODE == "down" else Resolve()
    '''
)

SHIM = textwrap.dedent(
    """
    import importlib.machinery, runpy, sys
    importlib.machinery.ExtensionFileLoader = importlib.machinery.SourceFileLoader
    sys.argv = [{child!r}, *sys.argv[1:]]
    runpy.run_path({child!r}, run_name="__main__")
    """
)


def _fake(tmp_path: Path) -> tuple[Path, Path]:
    """The fake library and a shim running the child with it (the child loads an extension
    module; a plain .py stands in through a source loader)."""
    fake = tmp_path / "fusionscript.py"
    fake.write_text(FAKE_LIBRARY, encoding="utf-8")
    shim = tmp_path / "run_child.py"
    shim.write_text(SHIM.format(child=str(CHILD_SCRIPT)), encoding="utf-8")
    return fake, shim


def _child(tmp_path: Path, *args: str, mode: str = "", **env: str) -> dict[str, Any]:
    fake, shim = _fake(tmp_path)
    done = subprocess.run(
        [sys.executable, str(shim), str(fake), *args],
        capture_output=True,
        check=True,
        timeout=60,
        env={"FAKE_RESOLVE_MODE": mode, "SYSTEMROOT": "C:\\Windows", "PATH": "", **env},
    )
    assert done.stdout.isascii()
    result: dict[str, Any] = json.loads(done.stdout)
    return result


def test_child_lists_the_project(tmp_path: Path) -> None:
    info = project_from_json(_child(tmp_path, "project"))
    assert info.project.name == "cats 2026"
    assert info.studio
    (timeline,) = info.timelines
    assert timeline.name == "Montage été"
    assert timeline.video_clips == 6  # both video tracks, the transition left out
    assert timeline.is_current


def test_child_reads_every_kind_of_item(tmp_path: Path) -> None:
    answer = _child(tmp_path, "timeline", "t1")
    assert answer["errors"] == 0  # a method that fails gives an empty value, not an error
    content = timeline_content_from_json(answer)
    by_name = {(c.use.name, c.use.track_type): c for c in content.clips}
    clip = by_name[("a.mp4", "video")]
    assert clip.file_path == "D:\\r\\été.mp4"
    assert clip.kind == ClipKind.FILE
    assert clip.vfe_video_id == "01video"  # written in Resolve by our markers script
    assert clip.use.source_start_s == pytest.approx(0.0)  # the clip's start timecode removed
    assert clip.use.source_end_s == pytest.approx(75 / 29.97)
    assert clip.use.media_pool_item_id == "p1"
    assert ("a.mp4", "audio") in by_name  # linked audio, read too
    assert by_name[("Titre", "video")].kind == ClipKind.GRAPHICS
    assert by_name[("MC", "video")].kind == ClipKind.CONTAINER
    assert "Transition" not in {c.use.name for c in content.clips}
    assert by_name[("", "video")].file_path is None  # its name failed: kept, without a name
    assert by_name[("d.mov", "video")].use.track_enabled is False  # V2 is switched off


def test_track_switches_count_only_on_the_open_timeline(tmp_path: Path) -> None:
    # Resolve 21.1 says every track of a timeline that is not open is switched off.
    content = timeline_content_from_json(_child(tmp_path, "timeline", "t1", mode="other_open"))
    assert all(clip.use.track_enabled for clip in content.clips)


def test_child_reads_inside_nested_timelines(tmp_path: Path) -> None:
    content = timeline_content_from_json(_child(tmp_path, "timeline", "t1"))
    nested = [c for c in content.clips if c.use.nested_in]
    # The outer clip shows inner frames 108030–108090: b.mp4 from its frame 30, c.mp4 not at all.
    (b,) = nested
    assert b.file_path == "D:\\r\\b.mp4"
    assert b.use.nested_in == "Nest"
    assert (b.use.record_start_frame, b.use.record_end_frame) == (108100, 108160)
    assert b.use.track == 1
    assert b.use.source_start_s == pytest.approx(30 / 29.97)


def test_child_reads_each_media_pool_item_once(tmp_path: Path) -> None:
    count = tmp_path / "count.txt"
    started = time.monotonic()
    answer = _child(tmp_path, "timeline", "t1", mode="big", FAKE_COUNT_FILE=str(count))
    assert time.monotonic() - started < 30
    assert len(answer["clips"]) == 6000
    assert int(count.read_text()) == 100  # one property read per media pool item


@pytest.mark.parametrize(
    ("args", "mode", "code"),
    [
        (("project",), "down", "no_connection"),
        (("project",), "starting", "starting"),
        (("project",), "no_project", "no_project"),
        (("timeline", "-"), "no_timeline", "no_timeline"),
        (("timeline", "nope"), "", "timeline_not_found"),
        (("timeline", "t1"), "changing", "changed_during_read"),
    ],
)
def test_child_errors(tmp_path: Path, args: tuple[str, ...], mode: str, code: str) -> None:
    answer = _child(tmp_path, *args, mode=mode)
    assert answer["ok"] is False
    assert answer["error"] == code


def test_child_without_library(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-I", str(CHILD_SCRIPT), str(tmp_path / "missing.dll"), "project"],
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert json.loads(done.stdout)["error"] == "not_installed"


# ---------------------------------------------------------------- the parent, with the fake
@pytest.fixture
def fake_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ResolveReader:
    fake, shim = _fake(tmp_path)
    monkeypatch.setattr(reader, "CHILD_SCRIPT", shim)
    monkeypatch.setattr(reader, "resolve_running", lambda: True)
    return ResolveReader(Settings(data_dir=tmp_path / "data", resolve_script_lib=str(fake)))


def test_reader_reads_through_the_child(fake_reader: ResolveReader) -> None:
    assert fake_reader.project().project.name == "cats 2026"
    content = fake_reader.timeline(None)
    assert content.timeline.id == "t1"
    assert content.errors == 0


def test_a_resolve_that_never_answers_is_left_behind(
    fake_reader: ResolveReader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "pid.txt"
    monkeypatch.setenv("FAKE_RESOLVE_MODE", "hang")
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    monkeypatch.setattr(reader, "PROJECT_TIMEOUT_S", 2.0)
    started = time.monotonic()
    with pytest.raises(ResolveUnavailableError) as caught:
        fake_reader.project()
    assert caught.value.extra["reason"] == "not_responding"
    assert caught.value.detail == NOT_RESPONDING
    assert time.monotonic() - started < 15
    assert not _running(int(pid_file.read_text()))  # no reader left connected to Resolve
    assert reader._calls.acquire(blocking=False)  # and the next read may start
    reader._calls.release()


def test_a_second_read_waits_briefly_then_says_busy(
    fake_reader: ResolveReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader, "LOCK_WAIT_S", 0.1)
    assert reader._calls.acquire(blocking=False)
    try:
        with pytest.raises(ResolveUnavailableError) as caught:
            fake_reader.project()
    finally:
        reader._calls.release()
    assert caught.value.extra["reason"] == "busy"


def _running(pid: int) -> bool:
    if sys.platform == "win32":
        done = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return f'"{pid}"' in done.stdout
    else:
        try:
            os.kill(pid, 0)  # no signal: only tells whether the process exists
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        # A zombie (ended, not yet reaped) no longer runs anything.
        done = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        state = done.stdout.strip()
        return bool(state) and not state.startswith("Z")


# ---------------------------------------------------------------- read once, import after
def test_snapshots_are_kept_a_little_while() -> None:
    content = timeline_content_from_json(
        {
            **{k: PROJECT[k] for k in ("product", "version", "database", "project")},
            "timeline": PROJECT["timelines"][0],
            "clips": [],
        }
    )
    snapshots = TimelineSnapshots(ttl_s=60, size=2)
    first = snapshots.remember(content)
    assert snapshots.recall(first) is content
    snapshots.remember(content)
    snapshots.remember(content)
    assert snapshots.recall(first) is None  # pushed out by newer reads
    assert snapshots.recall("unknown") is None
    stale = TimelineSnapshots(ttl_s=-1)  # always too old
    old = stale.remember(content)
    assert stale.recall(old) is None


# ---------------------------------------------------------------- source ranges (Resolve)
source_range: Callable[..., tuple[float, float]] = timeline_reader.source_range
clip_frames: Callable[..., int | None] = timeline_reader.clip_frames
timecode_seconds: Callable[[str, float], float] = timeline_reader.timecode_seconds
nested: Callable[..., Iterator[dict[str, Any]]] = timeline_reader.nested


class _Item:
    """A timeline item as Resolve 21.1 answered for « vfe test montage » (29.97 fps)."""

    def __init__(self, left: int | None, start: int, end: int, times: tuple[float, float]) -> None:
        self.left, self.start, self.end, self.times = left, start, end, times

    def GetLeftOffset(self) -> int | None:  # noqa: N802 - Resolve's API
        return self.left

    def GetSourceStartTime(self) -> float:  # noqa: N802
        return self.times[0]

    def GetSourceEndTime(self) -> float:  # noqa: N802
        return self.times[1]


def test_a_source_range_is_the_cut_not_the_transition_handles() -> None:
    # charpentière.mp4, Start TC 01:02:01:17, dissolves on both sides: Resolve's source times
    # count 7 frames of handle each side; ffprobe puts frame 120 at 4.004 s and 300 at 10.010 s.
    info = {"fps": 29.97, "start_tc_s": timecode_seconds("01:02:01:17", 29.97)}
    item = _Item(120, 108120, 108300, (3729.0253, 3735.5318))
    start, end = source_range(item, info, 29.97, 180)
    assert (start, end) == pytest.approx((4.004, 10.010), abs=1e-3)
    # Without a left offset: the times, the start timecode counted in frames (handles included).
    start, end = source_range(_Item(None, 0, 0, item.times), info, 29.97, 180)
    assert (start, end) == pytest.approx((3.733, 10.240), abs=1e-3)


@pytest.mark.parametrize(
    ("left", "clip_fps", "seconds", "frame"),
    [
        (108, 30.0, 4.5, 135),  # placed from its frame 135 (4.5 s)
        (1193, 29.97, 49.708, 1490),  # placed from its frame 1491: Resolve truncates the offset
    ],
)
def test_a_source_range_at_mixed_rates(
    left: int, clip_fps: float, seconds: float, frame: int
) -> None:
    # As Resolve 21.1.1 answered on 2026-10-06 for phone clips in a 24 fps timeline: the left
    # offset counts TIMELINE frames, like the duration (120 frames = 5 s at real speed).
    info = {"fps": clip_fps, "start_tc_s": 0.0}
    start, end = source_range(_Item(left, 86400, 86520, (0, 0)), info, 24.0, 120)
    assert (start, end) == pytest.approx((seconds, seconds + 5.0), abs=1e-3)
    assert clip_frames(left, clip_fps, 24.0) == frame


def test_clip_frames_without_rates_keep_the_offset() -> None:
    assert clip_frames(120, 29.97, 29.97) == 120
    assert clip_frames(50, None, 25.0) == 50
    assert clip_frames(None, 30.0, 24.0) is None


@pytest.mark.parametrize(
    ("timecode", "fps", "seconds"),
    [
        ("01:02:01:17", 29.97, 111647 / 29.97),
        ("00:00:10:00", 25.0, 10.0),
        ("00:10:00;00", 29.97, 17982 / 29.97),  # drop frame: 18 frames dropped in 10 minutes
        ("nope", 25.0, 0.0),
    ],
)
def test_timecode_seconds_as_resolve_counts_them(timecode: str, fps: float, seconds: float) -> None:
    assert timecode_seconds(timecode, fps) == pytest.approx(seconds)


class _Inner:
    """A nested timeline at 25 fps whose GetSetting answers nothing (GetSettings does)."""

    def GetSetting(self, _key: str) -> str:  # noqa: N802 - Resolve's API
        return ""

    def GetSettings(self) -> dict[str, str]:  # noqa: N802
        return {"timelineFrameRate": "25"}

    def GetStartFrame(self) -> int:  # noqa: N802
        return 0


def test_a_nested_timeline_is_trimmed_at_its_own_frame_rate() -> None:
    inner_use = {
        "record_start_frame": 0, "record_end_frame": 200, "source_start_s": 10.0,
        "source_end_s": 18.0, "track_enabled": True, "enabled": True, "nested_in": None,
    }  # fmt: skip
    outer = {
        "source_start_frame": 50, "record_start_frame": 1000, "record_end_frame": 1100,
        "clip_fps": 25.0, "track_type": "video", "track": 1, "track_name": "V1",
        "track_enabled": True, "enabled": True, "name": "Compound",
    }  # fmt: skip
    [moved] = nested(outer, _Inner(), [{"use": inner_use}])
    use = moved["use"]
    assert (use["source_start_s"], use["source_end_s"]) == pytest.approx((12.0, 16.0))
    assert (use["record_start_frame"], use["record_end_frame"]) == (1000, 1100)


# ---------------------------------------------------------------- Resolve on another computer
def test_resolve_on_another_computer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake, shim = _fake(tmp_path)
    monkeypatch.setattr(reader, "CHILD_SCRIPT", shim)
    settings = Settings(data_dir=tmp_path / "data", resolve_script_lib=str(fake))
    remote = ResolveReader(settings, host=lambda: "127.0.0.1")
    with socket.create_server(("127.0.0.1", 0)) as server:  # Resolve's scripting server, there
        monkeypatch.setattr(reader, "SCRIPT_PORT", server.getsockname()[1])
        assert remote.project().project.name == "cats 2026"
        monkeypatch.setenv("FAKE_RESOLVE_MODE", "down")  # there, but scripts not set to Network
        with pytest.raises(ResolveUnavailableError, match="Réseau") as refused:
            remote.project()
        assert refused.value.extra["reason"] == "scripting_off"
    started = time.monotonic()
    with pytest.raises(ResolveUnavailableError, match="ne répond pas") as absent:
        remote.project()  # the computer is off: told at once, not after 40 s
    assert absent.value.extra["reason"] == "not_reachable"
    assert time.monotonic() - started < 5
    missing = Settings(data_dir=tmp_path / "data", resolve_script_lib=str(tmp_path / "none.dll"))
    with pytest.raises(ResolveUnavailableError, match="gardez DaVinci Resolve installé"):
        ResolveReader(missing, host=lambda: "mac.local").project()
