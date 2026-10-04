"""The assistant's Resolve tools in the child: a NEW timeline from an edit list —
clips by media pool id or path, seconds turned into frames at each clip's own rate, the last
frame never passed, Transform values set and read back — and the fixed markers script. The child
runs against a fake scripting library."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from vfe_vision.adapters.resolve.editor import EDITOR_SCRIPT, edit_result_from_json

FAKE_LIBRARY = textwrap.dedent(
    '''
    """Stands for Resolve's fusionscript: the project « cats 2026 » with a timeline « Chats » (the
    open one) and a media pool holding a.mp4 (uid pool-a, 29.97 i/s, 300 frames) and b.mp4
    (pool-b, 59.94 i/s). Imports make a clip of 25 i/s, except for a path holding « missing ».
    FAKE_RESOLVE_MODE: append_nothing (no clip placed), props_ignored (RotationAngle not kept).
    The state is written to FAKE_STATE_FILE at the end."""
    import atexit, json, os

    MODE = os.environ.get("FAKE_RESOLVE_MODE", "")
    STATE = {"appended": [], "imported": [], "deleted": [], "settings": [], "props": []}
    atexit.register(lambda: open(os.environ["FAKE_STATE_FILE"], "w").write(json.dumps(STATE)))


    class Clip:
        def __init__(self, uid, path, fps, frames):
            self.uid, self.path, self.fps, self.frames = uid, path, fps, frames

        def GetUniqueId(self):
            return self.uid

        def GetName(self):
            return self.path.rsplit("\\\\", 1)[-1]

        def GetClipProperty(self, key=None):
            return {"File Path": self.path, "FPS": self.fps, "Frames": str(self.frames)}


    class Folder:
        def __init__(self, name, clips=()):
            self.name, self.clips, self.subs = name, list(clips), []

        def GetName(self):
            return self.name

        def GetClipList(self):
            return list(self.clips)

        def GetSubFolderList(self):
            return list(self.subs)


    class Item:
        count = 0

        def __init__(self, kind, start, duration):
            Item.count += 1
            self.uid, self.kind, self.start, self.duration = f"item-{Item.count}", kind, start, duration
            self.props = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0, "RotationAngle": 0.0,
                          "Scaling": 0}

        def GetUniqueId(self):
            return self.uid

        def GetType(self):
            return self.kind

        def GetStart(self):
            return self.start

        def GetEnd(self):
            return self.start + self.duration

        def GetDuration(self):
            return self.duration

        def SetProperties(self, props):
            STATE["props"].append(props)
            for key, value in props.items():
                if not (MODE == "props_ignored" and key == "RotationAngle"):
                    self.props[key] = value
            return True

        def GetProperties(self, key=None):
            return dict(self.props)


    class Timeline:
        def __init__(self, name, uid):
            self.name, self.uid = name, uid
            self.settings = {"timelineFrameRate": "29.97", "timelineResolutionWidth": "1920",
                             "timelineResolutionHeight": "1080"}
            self.video, self.audio = [], []

        def GetName(self):
            return self.name

        def GetUniqueId(self):
            return self.uid

        def GetSettings(self):
            return dict(self.settings)

        def GetSetting(self, key):
            return self.settings.get(key, "")

        def SetSettings(self, values):
            STATE["settings"].append(values)
            self.settings.update({k: v for k, v in values.items() if k != "useCustomSettings"})
            return False  # Resolve says no and applies the size (measured)

        def GetStartFrame(self):
            return 108000

        def GetEndFrame(self):
            return max([i.GetEnd() for i in self.video] or [108000])

        def GetTrackCount(self, kind):
            return 1

        def GetItemListInTrack(self, kind, track):
            return list(self.video if kind == "video" else self.audio)


    class Pool:
        def __init__(self, project):
            self.project = project
            self.root = Folder("Master", [Clip("pool-a", "D:\\\\r\\\\a.mp4", "29.97", 300),
                                          Clip("pool-b", "D:\\\\r\\\\b.mp4", "59.94", 600)])
            self.current = self.root

        def GetRootFolder(self):
            return self.root

        def GetCurrentFolder(self):
            return self.current

        def SetCurrentFolder(self, folder):
            self.current = folder
            return True

        def AddSubFolder(self, parent, name):
            folder = Folder(name)
            parent.subs.append(folder)
            return folder

        def ImportMedia(self, paths):
            made = []
            for path in paths:
                if isinstance(path, dict) or "missing" in path:
                    continue
                clip = Clip(f"pool-{len(STATE['imported'])}-new", path, "25", 250)
                self.current.clips.append(clip)
                STATE["imported"].append(path)
                made.append(clip)
            return made

        def CreateEmptyTimeline(self, name):
            if any(t.name == name for t in self.project.timelines):
                return None
            timeline = Timeline(name, f"tl-{len(self.project.timelines) + 1}")
            self.project.timelines.append(timeline)
            return timeline

        def AppendToTimeline(self, infos):
            timeline = self.project.current
            if MODE == "append_nothing":
                return []
            made = []
            for info in infos:
                clip, start, end = info["mediaPoolItem"], info["startFrame"], info["endFrame"]
                duration = round((end - start) * 29.97 / float(clip.fps))
                at = timeline.GetEndFrame()
                video = Item("video", at, duration)
                timeline.video.append(video)
                made.append(video)
                if info.get("mediaType") != 1:
                    timeline.audio.append(Item("audio", at, duration))
                STATE["appended"].append([clip.uid, start, end, info.get("mediaType")])
            return made

        def DeleteTimelines(self, timelines):
            for timeline in timelines:
                self.project.timelines.remove(timeline)
                STATE["deleted"].append(timeline.name)
            return True


    class Project:
        def __init__(self):
            self.timelines = [Timeline("Chats", "tl-user")]
            self.current = self.timelines[0]
            self.pool = Pool(self)

        def GetName(self):
            return "cats 2026"

        def GetUniqueId(self):
            return "prj-1"

        def GetMediaPool(self):
            return self.pool

        def GetTimelineCount(self):
            return len(self.timelines)

        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]

        def GetCurrentTimeline(self):
            return self.current

        def SetCurrentTimeline(self, timeline):
            self.current = timeline
            STATE["current"] = timeline.name
            return True

        def GetSetting(self, key):
            return "scaleToFit" if key == "timelineInputResMismatchBehavior" else ""


    PROJECT = Project()


    class Manager:
        def GetCurrentProject(self):
            return PROJECT


    class Resolve:
        SCALE_FIT = 2

        def GetProjectManager(self):
            return Manager()


    def scriptapp(name, host=None):
        return Resolve()
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

ITEMS: list[dict[str, Any]] = [
    {"uid": "pool-a", "path": None, "in_s": 1.0, "out_s": 3.0,
     "props": {"ZoomX": 3.1605, "ZoomY": 3.1605, "Pan": 0.0, "Tilt": -480.5}},
    {"uid": None, "path": "d:/R/B.mp4", "in_s": 0.5, "out_s": 1.5, "video_only": True},
    {"uid": None, "path": "D:\\r\\new.mp4", "in_s": 2.0, "out_s": 4.0,
     "props": {"ZoomX": 1.7778, "ZoomY": 1.7778, "Pan": 0.0, "Tilt": 0.0, "RotationAngle": -90.0}},
    {"uid": "pool-a", "in_s": 9.5, "out_s": 12.0},  # past the last frame (300): clamped
    {"uid": "pool-a", "in_s": 11.0, "out_s": 12.0},  # starts after the end: empty
    {"uid": None, "path": "D:\\r\\missing.mp4", "in_s": 0.0, "out_s": 1.0},
]  # fmt: skip


def _child(
    tmp_path: Path, command: str, request: Any, *, mode: str = ""
) -> tuple[dict[str, Any], dict[str, Any]]:
    fake = tmp_path / "fusionscript.py"
    fake.write_text(FAKE_LIBRARY, encoding="utf-8")
    shim = tmp_path / "run_editor.py"
    shim.write_text(SHIM.format(child=str(EDITOR_SCRIPT)), encoding="utf-8")
    state = tmp_path / "state.json"
    done = subprocess.run(
        [sys.executable, str(shim), str(fake), command],
        input=json.dumps(request).encode(),
        capture_output=True,
        check=True,
        timeout=60,
        env={"FAKE_RESOLVE_MODE": mode, "FAKE_STATE_FILE": str(state), "SYSTEMROOT": "C:\\Windows",
             "PATH": ""},
    )  # fmt: skip
    assert done.stdout.isascii()
    return json.loads(done.stdout), json.loads(state.read_text(encoding="utf-8"))


def _edit(name: str = "Chats") -> dict[str, Any]:
    return {"name": name, "folder": "Video Frame Expedition", "width": 1920, "height": 1080,
            "items": ITEMS}  # fmt: skip


def test_child_builds_a_new_timeline_from_the_edit_list(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, "edit", _edit())
    assert answer["ok"] is True, answer
    assert answer["timeline"]["name"] == "Chats (2)"  # the user's « Chats » is never touched
    assert state["current"] == "Chats (2)"
    # Frames at each clip's own rate, end exclusive, never past the last frame.
    assert state["appended"] == [
        ["pool-a", 30, 90, None],  # 1.0-3.0 s at 29.97
        ["pool-b", 30, 90, 1],  # found by its path written otherwise; 59.94 i/s; picture only
        ["pool-0-new", 50, 100, None],  # imported now (25 i/s)
        ["pool-a", 285, 300, None],  # 9.5 s = 285, out clamped to the 300 frames
    ]
    assert state["imported"] == ["D:\\r\\new.mp4"]
    assert answer["imported"] == 1
    assert sorted(answer["missing"], key=lambda m: m["n"]) == [
        {"n": 5, "why": "empty_range"}, {"n": 6, "why": "not_in_pool"},
    ]  # fmt: skip
    rows = {row["n"]: row for row in answer["items"]}
    assert set(rows) == {1, 2, 3, 4}
    assert all(row["duration_ok"] for row in rows.values())
    assert rows[1]["props_ok"] is True
    assert rows[3]["props_ok"] is True
    assert "props_ok" not in rows[2]
    assert rows[2]["duration"] == 30  # 60 frames at 59.94 play 30 timeline frames
    assert answer["audio_items"] == 3  # not the picture-only one
    assert answer["timeline"]["width"] == 1920
    assert state["deleted"] == []


def test_child_says_when_a_transform_did_not_hold(tmp_path: Path) -> None:
    answer, _ = _child(tmp_path, "edit", _edit("Neuf"), mode="props_ignored")
    assert answer["timeline"]["name"] == "Neuf"
    rows = {row["n"]: row for row in answer["items"]}
    assert rows[1]["props_ok"] is True
    assert rows[3]["props_ok"] is False  # RotationAngle refused


def test_child_takes_back_an_empty_timeline(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, "edit", _edit("Vide"), mode="append_nothing")
    assert (answer["ok"], answer["error"]) == (False, "nothing_placed")
    assert state["deleted"] == ["Vide"]


def test_child_refuses_an_edit_list_of_nothing_resolve_has(tmp_path: Path) -> None:
    request = {"name": "X", "folder": "B", "items": [{"path": "D:\\missing.mp4", "in_s": 0,
                                                      "out_s": 1}]}  # fmt: skip
    answer, _ = _child(tmp_path, "edit", request)
    assert (answer["ok"], answer["error"]) == (False, "no_media")


def test_child_runs_only_the_fixed_markers_script(tmp_path: Path) -> None:
    script = (
        "SCRIPT_VERSION = 1\nresult = {'project': project.GetName(), 'ok': resolve is not None}\n"
    )
    answer, _ = _child(tmp_path, "markers", {"script": script})
    assert answer == {"ok": True, "result": {"project": "cats 2026", "ok": True}}
    refused, _ = _child(tmp_path, "markers", {"script": "print('hello')"})
    assert (refused["ok"], refused["error"]) == (False, "usage")


def test_result_parsing() -> None:
    result = edit_result_from_json({
        "project": {"id": "p", "name": "cats"},
        "timeline": {"id": "t", "name": "Chats - vfe v1", "fps": 29.97, "width": 1920,
                     "height": 1080, "start_frame": 108000, "end_frame": 108299},
        "items": [{"n": 1, "id": "i", "record_start": 108000, "record_end": 108299,
                   "duration_ok": True, "props_ok": False}],
        "missing": [{"n": 2, "why": "empty_range"}], "not_placed": 0, "imported": 0,
        "audio_items": 1, "scaling": "scaleToFit",
    })  # fmt: skip
    assert result.duration_s == pytest.approx(299 / 29.97)
    assert result.items[0].props_ok is False
    assert result.skipped[0].why == "empty_range"
