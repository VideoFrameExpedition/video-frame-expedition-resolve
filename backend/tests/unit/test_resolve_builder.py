"""Building a timeline in DaVinci Resolve: what the child does in the project open in
Resolve, and what the user is told. The child runs against a fake scripting library."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from vfe_vision.adapters.resolve import builder, reader
from vfe_vision.adapters.resolve.builder import (
    BUILDER_SCRIPT,
    NO_MEDIA,
    NOTHING_PLACED,
    TIMELINE_REFUSED,
    ResolveTimelineBuilder,
)
from vfe_vision.core.config import Settings
from vfe_vision.core.errors import ExternalToolError, ResolveUnavailableError
from vfe_vision.domain.markers import chapter_start
from vfe_vision.domain.timeline_build import TimelineFormat, TimelineRequest, rate_named

FAKE_LIBRARY = textwrap.dedent(
    '''
    """Stands for Resolve's fusionscript: the project « VFE Vision - essai », a timeline « VFE
    test », a media pool with old.mp4 at its top and Deja.MP4 in a bin « Rushes ». Imports fail
    for a path holding « missing ». FAKE_RESOLVE_MODE picks a situation; the state is written to
    FAKE_STATE_FILE at the end. dict_silent: the dict form of ImportMedia imports nothing;
    dict_unreturned: it imports but returns nothing; append_list_only: the dict form of
    AppendToTimeline appends nothing; append_nothing: no form does; no_inherit: appended clips
    do not take the markers of their media pool clip; subtitle_known: the bin « Video Frame Expedition » has
    an earlier copy of « x - plans.srt », which Resolve would hand back as it was. Subtitle clips
    go to the one enabled subtitle track (lost with none or several), after what it holds;
    source frames on one abort the process, as Resolve 21.1 does. Every clip lasts 3000 frames
    on the timeline, which starts at 108000. old.mp4 holds a marker of the user at frame 0 and
    one of an earlier build at 300."""

    import atexit
    import json
    import os
    import sys

    MODE = os.environ.get("FAKE_RESOLVE_MODE", "")
    STATE = {"imported": [], "appended": [], "added_folders": [], "deleted": [], "metadata": {},
             "settings_calls": [], "import_forms": [], "append_forms": []}


    def _save():
        with open(os.environ["FAKE_STATE_FILE"], "w", encoding="utf-8") as out:
            json.dump(STATE, out)


    atexit.register(_save)


    def _add(markers, frame, color, name, note, duration, custom):
        if frame in markers:
            return False
        markers[frame] = {"color": color, "name": name, "note": note, "duration": duration,
                          "customData": custom}
        return True


    class Clip:
        def __init__(self, path, markers=None):
            self.path, self.markers = path, dict(markers or {})

        def GetClipProperty(self, key=None):
            props = {"File Path": self.path, "FPS": 29.97, "Frames": 3000}
            return props if key is None else props[key]

        def GetName(self):
            return self.path.replace("/", "\\\\").split("\\\\")[-1].rsplit(".", 1)[0]

        def GetMarkers(self):
            return dict(self.markers)

        def AddMarker(self, *marker):
            added = _add(self.markers, *marker)
            STATE.setdefault("clip_markers", {})[self.path] = self.markers
            return added

        def DeleteMarkerAtFrame(self, frame):
            STATE.setdefault("deleted_markers", []).append(frame)
            return self.markers.pop(frame, None) is not None

        def SetThirdPartyMetadata(self, data):
            STATE["metadata"][self.path] = data
            return True


    class Folder:
        def __init__(self, name, clips=(), subs=()):
            self.name, self.clips, self.subs = name, list(clips), list(subs)

        def GetName(self):
            return self.name

        def GetClipList(self):
            return list(self.clips)

        def GetSubFolderList(self):
            return list(self.subs)


    class Item:
        def __init__(self, clip, start=0):
            self.clip, self.start = clip, start
            self.markers = {} if MODE == "no_inherit" else dict(clip.markers)

        def GetStart(self):
            return self.start

        def GetEnd(self):
            return self.start + (100 if self.clip.path.endswith(".srt") else 3000)

        def GetType(self):
            return "video"

        def GetMediaPoolItem(self):
            return self.clip

        def GetMarkers(self):
            return dict(self.markers)

        def AddMarker(self, *marker):
            STATE.setdefault("item_markers", {}).setdefault(self.clip.path, []).append(marker[0])
            return _add(self.markers, *marker)


    class Timeline:
        def __init__(self, name):
            self.name, self.items, self.subtitles = name, [], []
            self.settings = {"timelineFrameRate": 25.0, "timelineResolutionWidth": "1920",
                             "timelineResolutionHeight": "1080"}

        def GetName(self):
            return self.name

        def GetUniqueId(self):
            return "tl-" + self.name

        def GetSettings(self):
            return dict(self.settings)

        def SetSettings(self, values):
            STATE["settings_calls"].append(values)
            if "timelineFrameRate" in values and (MODE == "keep_rate" or self.items):
                return False
            for key, value in values.items():
                if key == "timelineFrameRate":
                    self.settings[key] = float(value)
                elif key != "useCustomSettings":
                    self.settings[key] = value
            return True

        def GetItemListInTrack(self, kind, index):
            if kind == "subtitle":
                return list(self.subtitles[index - 1]["items"])
            return list(self.items) if (kind, index) == ("video", 1) else []

        def GetStartFrame(self):
            return 108000

        def GetTrackCount(self, kind):
            return len(self.subtitles) if kind == "subtitle" else 1

        def AddTrack(self, kind, options=None):
            if kind == "subtitle":
                enabled = not any(track["enabled"] for track in self.subtitles)
                self.subtitles.append({"name": "Subtitle", "enabled": enabled, "items": []})
                self._keep()
            return True

        def SetTrackName(self, kind, index, name):
            self.subtitles[index - 1]["name"] = name
            self._keep()
            return True

        def SetTrackEnable(self, kind, index, enabled):
            self.subtitles[index - 1]["enabled"] = enabled
            self._keep()
            return True

        def _keep(self):
            STATE["subtitle_tracks"] = [[t["name"], t["enabled"]] for t in self.subtitles]


    ROOT = Folder("Master", clips=[Clip("D:\\\\r\\\\old.mp4", {
                      0: {"color": "Red", "name": "à moi", "note": "", "duration": 1,
                          "customData": ""},
                      300: {"color": "Blue", "name": "old", "note": "", "duration": 1,
                            "customData": "vfe:chapter:9"}})],
                  subs=[Folder("Rushes", clips=[Clip("D:\\\\R\\\\Deja.MP4")])])
    if MODE == "has_bin":
        ROOT.subs.append(Folder("Video Frame Expedition"))
    if MODE == "subtitle_known":
        ROOT.subs.append(Folder("Video Frame Expedition", clips=[Clip("C:\\\\d\\\\x - plans.srt")]))


    def _pool_clips(folder=None):
        folder = folder or ROOT
        clips = list(folder.clips)
        for sub in folder.subs:
            clips += _pool_clips(sub)
        return clips


    class MediaPool:
        def __init__(self):
            self.current = ROOT

        def GetRootFolder(self):
            return ROOT

        def GetCurrentFolder(self):
            return self.current

        def SetCurrentFolder(self, folder):
            self.current = folder
            STATE["current_folder"] = folder.GetName()
            return True

        def AddSubFolder(self, parent, name):
            folder = Folder(name)
            parent.subs.append(folder)
            STATE["added_folders"].append(name)
            return folder

        def DeleteClips(self, clips):
            for folder in [ROOT, *ROOT.subs]:
                folder.clips = [clip for clip in folder.clips if clip not in clips]
            STATE.setdefault("deleted_clips", []).extend(clip.path for clip in clips)
            return True

        def DeleteFolders(self, folders):
            for folder in folders:
                ROOT.subs.remove(folder)
                STATE["deleted"].append(folder.GetName())
            return True

        def ImportMedia(self, infos):
            if MODE == "old_forms" and isinstance(infos[0], dict):
                raise TypeError("unsupported form")
            form = "dict" if isinstance(infos[0], dict) else "list"
            STATE["import_forms"].append(form)
            if MODE == "dict_silent" and form == "dict":
                return []
            found = []
            for info in infos:
                path = info["FilePath"] if isinstance(info, dict) else info
                if "missing" in path or MODE == "nothing":
                    continue
                known = [clip for clip in _pool_clips() if clip.path == path]
                if path.endswith(".srt") and known:  # handed back as it was
                    found.append(known[0])
                    continue
                if MODE == "renamed":  # Resolve keeps the file under another path
                    path = "E:\\\\mounted\\\\" + path.split("\\\\")[-1]
                clip = Clip(path)
                self.current.clips.append(clip)
                found.append(clip)
                STATE["imported"].append(path)
            if all(p.endswith(".srt") for p in infos if isinstance(p, str)):
                found.reverse()  # Resolve does not keep the order given
            return [] if MODE == "dict_unreturned" and form == "dict" else found

        def CreateEmptyTimeline(self, name):
            if MODE == "refuse" or any(t.name == name for t in PROJECT.timelines):
                return None
            timeline = Timeline(name)
            PROJECT.timelines.append(timeline)
            STATE["timeline_folder"] = self.current.GetName()
            return timeline

        def DeleteTimelines(self, timelines):
            for timeline in timelines:
                PROJECT.timelines.remove(timeline)
                STATE.setdefault("deleted_timelines", []).append(timeline.name)
            return True

        def AppendToTimeline(self, infos):
            form = "dict" if isinstance(infos[0], dict) else "list"
            STATE["append_forms"].append(form)
            if MODE == "append_nothing" or (MODE == "append_list_only" and form == "dict"):
                return []
            timeline = PROJECT.current
            items = []
            for info in infos:
                clip = info["mediaPoolItem"] if isinstance(info, dict) else info
                asked = info if isinstance(info, dict) else {}
                if clip.path.endswith(".srt"):
                    if "startFrame" in asked or "endFrame" in asked:
                        sys.stdout.flush()
                        os._exit(134)  # SIGABRT
                    enabled = [t for t in timeline.subtitles if t["enabled"]]
                    if len(enabled) != 1:
                        STATE["subtitles_lost"] = STATE.get("subtitles_lost", 0) + 1
                        continue
                    track = enabled[0]
                    start = max([108000] + [i.GetEnd() for i in track["items"]])
                    track["items"].append(Item(clip, start))
                    STATE.setdefault("laid", []).append(
                        [clip.path, timeline.subtitles.index(track) + 1, start])
                    continue
                start = asked.get("recordFrame", max([108000] + [i.GetEnd() for i in timeline.items]))
                item = Item(clip, start)
                timeline.items.append(item)
                items.append(item)
                STATE["appended"].append(clip.path)
                STATE.setdefault("record_frames", []).append(start)
            return items


    class Project:
        def __init__(self):
            self.timelines = [Timeline("VFE test")]
            self.current = self.timelines[0]
            self.pool = MediaPool()

        def GetName(self):
            return "VFE Vision - essai"

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
            if MODE == "stuck":  # a dialog open in Resolve: the open timeline stays
                return False
            self.current = timeline
            STATE["current_timeline"] = timeline.name
            return True


    PROJECT = Project()


    class Manager:
        def GetCurrentProject(self):
            return None if MODE == "no_project" else PROJECT


    class Resolve:
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

REQUEST: dict[str, Any] = {
    "name": "VFE test",
    "rate": "29.97",
    "width": 3840,
    "height": 2160,
    "folder": "Video Frame Expedition",
    "clips": [
        {"video_id": "v-new", "path": "D:\\r\\new1.mp4"},
        {"video_id": "v-old", "path": "D:\\r\\old.mp4"},
        {"video_id": "v-gone", "path": "D:\\r\\missing.mp4"},
        {"video_id": "v-deja", "path": "d:/r/deja.mp4"},  # in the media pool, written otherwise
    ],
}


def _fake(tmp_path: Path) -> tuple[Path, Path]:
    fake = tmp_path / "fusionscript.py"
    fake.write_text(FAKE_LIBRARY, encoding="utf-8")
    shim = tmp_path / "run_builder.py"
    shim.write_text(SHIM.format(child=str(BUILDER_SCRIPT)), encoding="utf-8")
    return fake, shim


def _child(
    tmp_path: Path, request: Any = None, *, mode: str = ""
) -> tuple[dict[str, Any], dict[str, Any]]:
    fake, shim = _fake(tmp_path)
    state = tmp_path / "state.json"
    done = subprocess.run(
        [sys.executable, str(shim), str(fake), "build"],
        input=json.dumps(REQUEST if request is None else request).encode(),
        capture_output=True,
        check=True,
        timeout=60,
        env={"FAKE_RESOLVE_MODE": mode, "FAKE_STATE_FILE": str(state), "SYSTEMROOT": "C:\\Windows",
             "PATH": ""},
    )  # fmt: skip
    assert done.stdout.isascii()
    return json.loads(done.stdout), json.loads(state.read_text(encoding="utf-8"))


def test_child_builds_the_timeline(tmp_path: Path) -> None:
    answer, state = _child(tmp_path)
    assert answer["ok"] is True
    assert answer["project"] == {"id": "prj-1", "name": "VFE Vision - essai"}
    assert answer["timeline"]["name"] == "VFE test (2)"  # « VFE test » was taken
    assert state["current_timeline"] == "VFE test (2)"  # and it is the open one now
    # Only what the media pool lacked is imported, into the bin, with the timeline.
    assert state["imported"] == ["D:\\r\\new1.mp4"]
    assert state["added_folders"] == ["Video Frame Expedition"]
    assert state["timeline_folder"] == "Video Frame Expedition"
    assert state["current_folder"] == "Master"  # the user's bin given back
    assert state["import_forms"] == ["dict"]  # Resolve 21.1's canonical form
    assert state["append_forms"] == ["dict"]
    assert answer["tried"] == [
        "ImportMedia dict: 1 returned, 1 in the bin", "AppendToTimeline dict: 3 on the tracks",
    ]  # fmt: skip
    # In the chosen order, the file Resolve could not open left out.
    assert state["appended"] == ["D:\\r\\new1.mp4", "D:\\r\\old.mp4", "D:\\R\\Deja.MP4"]
    assert (answer["clips"], answer["imported"], answer["reused"]) == (3, 1, 2)
    assert answer["missing"] == ["D:\\r\\missing.mp4"]
    assert answer["folder"] == "Video Frame Expedition"
    # Our id on the clips we added only (the user's own clips are left as they are).
    assert state["metadata"] == {"D:\\r\\new1.mp4": {"vfe_video_id": "v-new"}}
    # The rate first (on the empty timeline), then the size.
    assert state["settings_calls"][0] == {"useCustomSettings": "1", "timelineFrameRate": "29.97"}
    assert answer["timeline"] | {"id": ""} == {
        "id": "", "name": "VFE test (2)", "fps": 29.97, "width": 3840, "height": 2160,
    }  # fmt: skip


def test_child_reports_the_rate_resolve_kept(tmp_path: Path) -> None:
    answer, _ = _child(tmp_path, mode="keep_rate")
    assert answer["timeline"]["fps"] == 25.0  # the project's
    assert (answer["timeline"]["width"], answer["timeline"]["height"]) == (3840, 2160)


def test_child_uses_the_bin_already_there(tmp_path: Path) -> None:
    _, state = _child(tmp_path, mode="has_bin")
    assert state["added_folders"] == []
    assert state["timeline_folder"] == "Video Frame Expedition"


def test_child_speaks_to_an_older_resolve(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, mode="old_forms")
    assert answer["ok"] is True
    assert state["import_forms"] == ["list"]
    assert state["imported"] == ["D:\\r\\new1.mp4"]


def test_child_imports_with_the_list_when_the_dict_form_makes_nothing(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, mode="dict_silent")  # Resolve 21.1.0, from this script
    assert answer["ok"] is True
    assert state["import_forms"] == ["dict", "list"]
    assert state["imported"] == ["D:\\r\\new1.mp4"]
    assert (answer["clips"], answer["imported"], answer["reused"]) == (3, 1, 2)
    assert answer["tried"][:2] == [
        "ImportMedia dict: 0 returned, 0 in the bin", "ImportMedia list: 1 returned, 1 in the bin",
    ]  # fmt: skip


def test_child_reads_the_bin_back_rather_than_import_twice(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, mode="dict_unreturned")
    assert state["import_forms"] == ["dict"]  # made, though not returned: no second import
    assert state["imported"] == ["D:\\r\\new1.mp4"]
    assert state["appended"] == ["D:\\r\\new1.mp4", "D:\\r\\old.mp4", "D:\\R\\Deja.MP4"]
    assert state["metadata"] == {"D:\\r\\new1.mp4": {"vfe_video_id": "v-new"}}
    assert answer["tried"][0] == "ImportMedia dict: 0 returned, 1 in the bin"


def test_child_appends_with_the_list_when_the_dict_form_places_nothing(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, mode="append_list_only")
    assert state["append_forms"] == ["dict", "list"]
    assert state["appended"] == ["D:\\r\\new1.mp4", "D:\\r\\old.mp4", "D:\\R\\Deja.MP4"]
    assert answer["clips"] == 3
    assert answer["tried"][-1] == "AppendToTimeline list: 3 on the tracks"


def test_child_takes_back_a_timeline_resolve_left_empty(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, mode="append_nothing")
    assert (answer["ok"], answer["error"]) == (False, "nothing_placed")
    assert state["append_forms"] == ["dict", "list"]
    assert state["deleted_timelines"] == ["VFE test (2)"]
    assert state["imported"] == ["D:\\r\\new1.mp4"]  # kept in the bin for the next try
    assert state["deleted"] == []
    assert "AppendToTimeline list: 0 on the tracks" in answer["message"]


def test_child_never_appends_to_the_timeline_already_open(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, mode="stuck")
    assert (answer["ok"], answer["error"]) == (False, "timeline_not_current")
    assert state["appended"] == []  # « VFE test », still open, is left as it was
    assert state["deleted_timelines"] == ["VFE test (2)"]  # the empty one made is gone
    assert state["current_folder"] == "Master"


def test_child_finds_a_file_resolve_keeps_under_another_path(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, mode="renamed")
    assert state["appended"][0] == "E:\\mounted\\new1.mp4"
    assert (answer["clips"], answer["imported"], answer["missing"]) == (
        3, 1, ["D:\\r\\missing.mp4"],
    )  # fmt: skip
    assert state["metadata"] == {"E:\\mounted\\new1.mp4": {"vfe_video_id": "v-new"}}


def _marker(t_s: float, name: str, number: int = 1) -> dict[str, Any]:
    return {"t_s": t_s, "name": name, "note": "", "color": "Blue",
            "custom_data": f"vfe:chapter:{number}"}  # fmt: skip


MARKED: dict[str, Any] = {
    **REQUEST,
    "marker_kinds": ["chapter"],
    "clips": [
        {**REQUEST["clips"][0], "markers": [_marker(0.0, "Intro"), _marker(10.0, "Suite", 2)]},
        {**REQUEST["clips"][1], "markers": [_marker(0.0, "Début")]},
        {**REQUEST["clips"][3], "markers": [_marker(200.0, "Après la fin")]},  # 3000 frames
    ],
}


def test_child_marks_where_chapters_start(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, MARKED)
    assert (answer["ok"], answer["markers"], answer["markers_missed"]) == (True, 3, 1)
    clips = state["clip_markers"]
    assert {frame: m["name"] for frame, m in clips["D:\\r\\new1.mp4"].items()} == {
        "0": "Intro", "299": "Suite",  # 10 s at 29.97
    }  # fmt: skip
    # The user's marker kept (ours goes to the next frame), the earlier build's replaced.
    assert {frame: m["name"] for frame, m in clips["D:\\r\\old.mp4"].items()} == {
        "0": "à moi", "1": "Début",
    }  # fmt: skip
    assert state["deleted_markers"] == [300]
    assert "item_markers" not in state  # the timeline's clips took them on append
    assert answer["tried"][-1] == (
        "markers: 3 on the clips, 1 missed; timeline clips: 2 took them, 0 given them"
    )


def test_child_gives_the_timeline_clips_their_markers(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, MARKED, mode="no_inherit")
    assert state["item_markers"] == {"D:\\r\\new1.mp4": [0, 299], "D:\\r\\old.mp4": [1]}
    assert answer["tried"][-1].endswith("timeline clips: 0 took them, 2 given them")


def test_child_marks_suggested_stretches_as_ranges(tmp_path: Path) -> None:
    request = {
        **REQUEST,
        "marker_kinds": ["highlight", "avoid"],
        "clips": [
            {**REQUEST["clips"][0], "markers": [
                {**_marker(5.0, "Moment fort 1"), "duration_s": 5.0, "color": "Green",
                 "custom_data": "vfe:highlight:1"},
                {**_marker(99.0, "À éviter"), "duration_s": 5.0, "color": "Red",
                 "custom_data": "vfe:avoid:1"},  # 5 s would run past the clip's 3000 frames
            ]},
            {**REQUEST["clips"][1], "markers": [_marker(20.0, "Début")]},
        ],
    }  # fmt: skip
    answer, state = _child(tmp_path, request)
    new1 = state["clip_markers"]["D:\\r\\new1.mp4"]
    assert {frame: (m["duration"], m["color"]) for frame, m in new1.items()} == {
        "149": (150, "Green"), "2967": (33, "Red"),
    }  # fmt: skip
    # The earlier build's chapter marker stays: this build writes no chapters.
    assert "deleted_markers" not in state
    assert answer["markers"] == 3


def test_child_gives_markers_in_the_timeline_frames(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, {**MARKED, "rate": "25"}, mode="no_inherit")
    # 299 frames of the clip at 29.97 are 249 of the timeline at 25.
    assert state["item_markers"] == {"D:\\r\\new1.mp4": [0, 249], "D:\\r\\old.mp4": [1]}
    assert answer["timeline"]["fps"] == 25.0


TRACKS = [{"name": "Transcription", "path": "C:\\d\\x - transcription.srt"},
          {"name": "Plans", "path": "C:\\d\\x - plans.srt"}]  # fmt: skip


def test_child_lays_the_subtitle_tracks(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, {**REQUEST, "subtitle_tracks": TRACKS})
    assert answer["subtitles"] == ["x - transcription", "x - plans"]
    assert answer["subtitles_laid"] == ["Transcription", "Plans"]
    # Each file alone enabled, on the empty timeline: both at its start, each on its track.
    assert state["laid"] == [
        ["C:\\d\\x - transcription.srt", 1, 108000], ["C:\\d\\x - plans.srt", 2, 108000],
    ]  # fmt: skip
    assert "subtitles_lost" not in state
    assert state["subtitle_tracks"] == [["Transcription", True], ["Plans", True]]
    # Then the clips end to end, each at the frame where the one before ends.
    assert state["record_frames"] == [108000, 111000, 114000]
    assert state["append_forms"] == ["dict"] * 5
    assert answer["tried"][-1] == "laid: 2 subtitle tracks, 3 clips at their frames (0 at the end)"


def test_child_replaces_an_earlier_copy_of_a_subtitle_file(tmp_path: Path) -> None:
    answer, state = _child(tmp_path, {**REQUEST, "subtitle_tracks": TRACKS}, mode="subtitle_known")
    # Resolve would hand the copy back as it was: deleted first, the file imported anew.
    assert state["deleted_clips"] == ["C:\\d\\x - plans.srt"]
    assert state["imported"][-2:] == ["C:\\d\\x - transcription.srt", "C:\\d\\x - plans.srt"]
    assert answer["subtitles_laid"] == ["Transcription", "Plans"]
    assert "subtitles: 2 of 2 imported, 1 earlier copies replaced" in answer["tried"]


def test_child_only_imports_the_loose_subtitle_files(tmp_path: Path) -> None:
    files = ["C:\\d\\a.srt", "C:\\d\\a_SHOTS.srt"]
    answer, state = _child(tmp_path, {**REQUEST, "subtitle_files": files})
    assert answer["subtitles"] == ["a", "a_SHOTS"]
    assert answer["subtitles_laid"] == []
    assert "laid" not in state
    assert "subtitle_tracks" not in state
    assert state["append_forms"] == ["dict"]  # the clips appended together, as without subtitles


def test_child_leaves_markers_alone_without_chapters(tmp_path: Path) -> None:
    answer, state = _child(tmp_path)
    assert (answer["markers"], answer["markers_missed"]) == (0, 0)
    assert "clip_markers" not in state
    assert "deleted_markers" not in state  # the earlier build's marker stays


def test_child_when_no_file_opens(tmp_path: Path) -> None:
    request = {**REQUEST, "clips": [{"video_id": "x", "path": "D:\\r\\missing.mp4"}]}
    answer, state = _child(tmp_path, request)
    assert (answer["ok"], answer["error"]) == (False, "no_media")
    assert state["import_forms"] == ["dict", "list"]
    assert answer["message"] == (
        "Resolve could not open any of the 1 files (ImportMedia dict: 0 returned, 0 in the bin; "
        "ImportMedia list: 0 returned, 0 in the bin)"
    )
    assert state["deleted"] == ["Video Frame Expedition"]  # the empty bin it made is gone
    assert state["current_folder"] == "Master"
    assert "current_timeline" not in state  # no timeline made


@pytest.mark.parametrize(
    ("request_", "mode", "code"),
    [
        (None, "refuse", "timeline_refused"),
        (None, "no_project", "no_project"),
        ({"name": "x", "clips": []}, "", "usage"),
        ("not json", "", "usage"),
    ],
)
def test_child_errors(tmp_path: Path, request_: Any, mode: str, code: str) -> None:
    fake, shim = _fake(tmp_path)
    done = subprocess.run(
        [sys.executable, str(shim), str(fake), "build"],
        input=(request_ if isinstance(request_, str) else json.dumps(request_ or REQUEST)).encode(),
        capture_output=True,
        check=True,
        timeout=60,
        env={"FAKE_RESOLVE_MODE": mode, "FAKE_STATE_FILE": str(tmp_path / "s.json"),
             "SYSTEMROOT": "C:\\Windows", "PATH": ""},
    )  # fmt: skip
    answer = json.loads(done.stdout)
    assert (answer["ok"], answer["error"]) == (False, code)


# ---------------------------------------------------------------- the parent, with the fake
def _request(*paths: str) -> TimelineRequest:
    rate = rate_named("29.97")
    assert rate is not None
    return TimelineRequest(
        name="Été 2026",
        format=TimelineFormat(rate, 2160, 3840),
        clips=tuple((f"v{i}", path) for i, path in enumerate(paths)),
        folder="Video Frame Expedition",
        markers={"v0": (chapter_start(1, 2.0, "Été à la plage", "résumé"),)},
    )


@pytest.fixture
def fake_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ResolveTimelineBuilder, Path]:
    fake, shim = _fake(tmp_path)
    monkeypatch.setattr(builder, "BUILDER_SCRIPT", shim)
    monkeypatch.setattr(reader, "resolve_running", lambda: True)
    monkeypatch.setenv("FAKE_STATE_FILE", str(tmp_path / "state.json"))
    settings = Settings(data_dir=tmp_path / "data", resolve_script_lib=str(fake))
    return ResolveTimelineBuilder(settings), tmp_path / "state.json"


def test_builder_builds_through_the_child(
    fake_builder: tuple[ResolveTimelineBuilder, Path],
) -> None:
    source, state_file = fake_builder
    built = source.build_timeline(_request("D:\\r\\été.mp4", "D:\\r\\old.mp4"))
    assert built.timeline_name == "Été 2026"  # accents reach Resolve and come back
    assert built.project.name == "VFE Vision - essai"
    assert (built.fps, built.width, built.height) == (29.97, 2160, 3840)
    assert (built.clips, built.imported, built.reused, built.missing) == (2, 1, 1, ())
    assert (built.markers, built.markers_missed) == (1, 0)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["imported"] == ["D:\\r\\été.mp4"]
    marker = state["clip_markers"]["D:\\r\\été.mp4"]["59"]  # 2 s at 29.97
    assert (marker["name"], marker["note"], marker["color"], marker["customData"]) == (
        "Été à la plage", "résumé", "Blue", "vfe:chapter:1",
    )  # accents reach Resolve  # fmt: skip


def test_builder_lays_the_subtitle_tracks(
    fake_builder: tuple[ResolveTimelineBuilder, Path],
) -> None:
    source, state_file = fake_builder
    request = replace(
        _request("D:\\r\\été.mp4"),
        subtitle_tracks=(("Transcription", "D:\\d\\Été - transcription.srt"),
                         ("Plans", "D:\\d\\Été - plans.srt")),
    )  # fmt: skip
    built = source.build_timeline(request)
    assert built.subtitles == ("Été - transcription", "Été - plans")
    assert built.subtitles_laid == ("Transcription", "Plans")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert [track for _, track, _ in state["laid"]] == [1, 2]


def test_builder_says_why_nothing_was_built(
    fake_builder: tuple[ResolveTimelineBuilder, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = fake_builder
    with pytest.raises(ExternalToolError) as caught:
        source.build_timeline(_request("D:\\r\\missing.mp4"))
    assert caught.value.detail == NO_MEDIA
    assert caught.value.extra["cause"].startswith("Resolve could not open any of the 1 files (")
    monkeypatch.setenv("FAKE_RESOLVE_MODE", "refuse")
    with pytest.raises(ExternalToolError) as caught:
        source.build_timeline(_request("D:\\r\\old.mp4"))
    assert caught.value.detail == TIMELINE_REFUSED
    monkeypatch.setenv("FAKE_RESOLVE_MODE", "append_nothing")
    with pytest.raises(ExternalToolError) as caught:
        source.build_timeline(_request("D:\\r\\old.mp4"))
    assert caught.value.detail == NOTHING_PLACED
    monkeypatch.setenv("FAKE_RESOLVE_MODE", "no_project")
    with pytest.raises(ResolveUnavailableError) as unavailable:
        source.build_timeline(_request("D:\\r\\old.mp4"))
    assert unavailable.value.extra["reason"] == "no_project"


def test_builder_on_another_computer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake, shim = _fake(tmp_path)
    monkeypatch.setattr(builder, "BUILDER_SCRIPT", shim)
    monkeypatch.setattr(reader, "reachable", lambda host: True)
    monkeypatch.setenv("FAKE_STATE_FILE", str(tmp_path / "state.json"))
    settings = Settings(data_dir=tmp_path / "data", resolve_script_lib=str(fake))
    source = ResolveTimelineBuilder(settings, host=lambda: "mac-studio")
    with pytest.raises(ExternalToolError) as caught:
        source.build_timeline(_request("/Volumes/cats/missing.mp4"))
    assert "« mac-studio »" in caught.value.detail
    assert "Connexions" in caught.value.detail
