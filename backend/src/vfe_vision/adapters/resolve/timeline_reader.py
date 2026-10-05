"""Read the project open in DaVinci Resolve, in a short-lived child process.

Run by ``adapters/resolve/reader.py`` with the application's Python, in isolated mode::

    python -I timeline_reader.py <fusionscript.dll> project
    python -I timeline_reader.py <fusionscript.dll> timeline <timeline id | ->

It loads Resolve's scripting library, connects to the running Resolve (external scripting,
preference « Local »), reads, and prints ONE JSON object (ASCII only) on stdout:
``{"ok": true, ...}`` or ``{"ok": false, "error": <code>, "message": <text>}``. It never changes
anything in Resolve. Standalone on purpose: the standard library only, nothing of the
application, so that Resolve's library lives and dies with this process.

``VFE_RESOLVE_DEADLINE_S`` makes it end itself after that many seconds, through a native
watchdog that works even while a call into Resolve's library holds the interpreter (a Resolve
busy with a modal dialog may never answer; the parent's kill reaches the uv launcher, not always
this interpreter).
"""

import faulthandler
import importlib.machinery
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path

TRACK_TYPES = ("video", "audio")
COUNT_BUDGET_S = 3.0  # counting the clips of every timeline stops after this (big projects)
NESTED_DEPTH = 3  # timelines inside timelines read this deep
CONTAINER_TYPES = ("compound", "multicam", "timeline")
GRAPHICS_TYPES = ("generator", "fusion", "title")
VIDEO_ID_KEY = "vfe_video_id"  # written by our markers script (apply_payload_v1.py)


class ReadError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def end_by_deadline():
    deadline = number(os.environ.get("VFE_RESOLVE_DEADLINE_S"))
    if deadline and deadline > 0:
        faulthandler.dump_traceback_later(deadline, exit=True)


def load_library(path):
    if not Path(path).is_file():
        raise ReadError("not_installed", "fusionscript library not found: " + path)
    try:
        loader = importlib.machinery.ExtensionFileLoader("fusionscript", path)
        spec = importlib.util.spec_from_loader("fusionscript", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    except (ImportError, OSError) as exc:
        raise ReadError("not_installed", "cannot load " + path + ": " + str(exc)) from exc
    return module


def connect(library):
    host = os.environ.get("VFE_RESOLVE_HOST", "")  # Resolve on another computer
    resolve = library.scriptapp("Resolve", host) if host else library.scriptapp("Resolve")
    if resolve is None:
        raise ReadError("no_connection", "Resolve did not answer (not running, or scripts refused)")
    return resolve


def call(obj, name, *args):
    """``obj.name(*args)``, or None when the method is missing or fails (older versions)."""
    method = getattr(obj, name, None)
    if method is None:
        return None
    try:
        return method(*args)
    except Exception:  # noqa: BLE001 - Resolve raises odd types; a missing value is enough
        return None


def number(value, default=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def integer(value, default=None):
    result = number(value)
    return default if result is None else round(result)


def text(value):
    return "" if value is None else str(value)


# ---------------------------------------------------------------- project and timelines
def open_project(resolve):
    manager = call(resolve, "GetProjectManager")
    if manager is None:
        raise ReadError("starting", "the project manager is not ready (starting or loading)")
    project = call(manager, "GetCurrentProject")
    if project is None:
        raise ReadError("no_project", "no project is open")
    return manager, project


def project_parts(resolve):
    manager, project = open_project(resolve)
    database = call(manager, "GetCurrentDatabase") or {}
    studio = call(resolve, "IsStudio")
    return project, {
        "product": text(call(resolve, "GetProductName")),
        "version": text(call(resolve, "GetVersionString")),
        "studio": True if studio is None else bool(studio),
        "database": {"type": text(database.get("DbType")), "name": text(database.get("DbName"))},
        "project": {
            "id": text(call(project, "GetUniqueId")),
            "name": text(call(project, "GetName")),
        },
    }


def project_id(resolve):
    manager = call(resolve, "GetProjectManager")
    project = call(manager, "GetCurrentProject") if manager is not None else None
    return text(call(project, "GetUniqueId")) if project is not None else ""


def timelines(project):
    count = integer(call(project, "GetTimelineCount"), 0)
    for index in range(1, count + 1):
        timeline = call(project, "GetTimelineByIndex", index)
        if timeline is not None:
            yield timeline


def setting(timeline, key, settings):
    value = call(timeline, "GetSetting", key)
    if value in (None, ""):
        value = settings().get(key)
    return value


def timeline_info(timeline, current_id, *, count_clips=True):
    cached = {}

    def settings():
        if "all" not in cached:
            cached["all"] = call(timeline, "GetSettings") or {}
        return cached["all"]

    video_tracks = integer(call(timeline, "GetTrackCount", "video"), 0)
    clips = None
    if count_clips:
        clips = sum(len(items(timeline, "video", index)) for index in range(1, video_tracks + 1))
    timeline_id = text(call(timeline, "GetUniqueId"))
    return {
        "id": timeline_id,
        "name": text(call(timeline, "GetName")),
        "fps": number(setting(timeline, "timelineFrameRate", settings), 0.0),
        "drop_frame": text(setting(timeline, "timelineDropFrameTimecode", settings)) == "1",
        "start_frame": integer(call(timeline, "GetStartFrame"), 0),
        "end_frame": integer(call(timeline, "GetEndFrame"), 0),
        "start_timecode": text(call(timeline, "GetStartTimecode")) or "00:00:00:00",
        "width": integer(setting(timeline, "timelineResolutionWidth", settings)),
        "height": integer(setting(timeline, "timelineResolutionHeight", settings)),
        "video_tracks": video_tracks,
        "audio_tracks": integer(call(timeline, "GetTrackCount", "audio"), 0),
        "video_clips": clips,
        "is_current": bool(timeline_id) and timeline_id == current_id,
    }


def timeline_rate(timeline):
    """A timeline's frame rate (GetSetting, else GetSettings), None when unreadable."""
    value = call(timeline, "GetSetting", "timelineFrameRate")
    if value in (None, ""):
        value = (call(timeline, "GetSettings") or {}).get("timelineFrameRate")
    rate = number(value)
    return rate if rate and rate > 0 else None


def items(timeline, track_type, track):
    """The clips of a track (transitions left out: they use no file)."""
    found = call(timeline, "GetItemListInTrack", track_type, track) or []
    return [item for item in found if text(call(item, "GetType")) != "transition"]


def track_counts(timeline):
    return [
        len(call(timeline, "GetItemListInTrack", track_type, track) or [])
        for track_type in TRACK_TYPES
        for track in range(1, integer(call(timeline, "GetTrackCount", track_type), 0) + 1)
    ]


# ---------------------------------------------------------------- clips
def timecode_seconds(timecode, fps):
    """``HH:MM:SS:FF`` → seconds as Resolve counts a clip's source time: its frame count
    (drop-frame aware) over the real rate, so 01:02:01:17 at 29.97 is 111647 / 29.97 s, not
    3721.57 s (0 when unreadable)."""
    raw = text(timecode)
    parts = raw.replace(";", ":").split(":")
    nominal = round(fps or 0)
    if len(parts) != 4 or nominal <= 0:
        return 0.0
    try:
        hours, minutes, secs, frames = (int(p) for p in parts)
    except ValueError:
        return 0.0
    count = ((hours * 60 + minutes) * 60 + secs) * nominal + frames
    if ";" in raw and nominal in (30, 60):
        total_minutes = hours * 60 + minutes
        count -= nominal // 15 * (total_minutes - total_minutes // 10)
    return count / fps


class Pool:
    """What a media pool item says, read once per item (a file used many times, and the
    audio linked to each of its video clips, share it)."""

    def __init__(self):
        self.known = {}

    def info(self, pool_item):
        if pool_item is None:
            return None
        uid = text(call(pool_item, "GetUniqueId"))
        if uid and uid in self.known:
            return self.known[uid]
        props = call(pool_item, "GetClipProperty")
        props = props if isinstance(props, dict) else {}
        fps = number(props.get("FPS"))
        video_id = call(pool_item, "GetThirdPartyMetadata", VIDEO_ID_KEY)
        info = {
            "uid": uid or None,
            "path": text(props.get("File Path")) or None,
            "type": text(props.get("Type")) or None,
            "fps": fps,
            "start_tc_s": timecode_seconds(props.get("Start TC"), fps),
            "video_id": video_id if isinstance(video_id, str) and video_id else None,
            "item": pool_item,
        }
        if uid:
            self.known[uid] = info
        return info


def kind_of(item, info):
    if text(call(item, "GetType")) == "generator" or info is None:
        return "graphics"
    kind = (info["type"] or "").casefold()
    if any(word in kind for word in CONTAINER_TYPES):
        return "container"
    if not info["path"] and any(word in kind for word in GRAPHICS_TYPES):
        return "graphics"
    return "file"


def source_range(item, info, timeline_fps, record_frames):
    """Seconds of the file the clip shows between its cuts, from its first frame. The left
    offset and the duration both count timeline frames, whatever the clip's rate (mixed rates
    play at real speed: a 30 fps clip placed from its frame 135 in a 24 fps timeline has a left
    offset of 108, Resolve 21.1.1). GetSourceStartTime/EndTime also count the handles a
    transition blends in, and GetSourceStartFrame/EndFrame can be a frame off: they are only a
    fallback."""
    left = integer(call(item, "GetLeftOffset"))
    if left is not None and timeline_fps:
        start = left / timeline_fps
        return start, start + record_frames / timeline_fps
    start_tc_s = info["start_tc_s"] if info else 0.0
    start = max(0.0, number(call(item, "GetSourceStartTime"), 0.0) - start_tc_s)
    return start, max(start, number(call(item, "GetSourceEndTime"), 0.0) - start_tc_s)


def clip_frames(left, clip_fps, timeline_fps):
    """A left offset (timeline frames) in the clip's own frames, within a frame at mixed rates."""
    if left is None or not clip_fps or not timeline_fps:
        return left
    return round(left * clip_fps / timeline_fps)


def use_of(item, info, *, timeline_fps, track_type, track, track_name, track_enabled):
    fps = info["fps"] if info else None
    record_start = integer(call(item, "GetStart"), 0)
    record_end = integer(call(item, "GetEnd"), 0)
    source_start, source_end = source_range(item, info, timeline_fps, record_end - record_start)
    enabled = call(item, "GetClipEnabled")
    return {
        "track_type": track_type,
        "track": track,
        "track_name": track_name,
        "track_enabled": track_enabled,
        "timeline_item_id": text(call(item, "GetUniqueId")) or None,
        "media_pool_item_id": info["uid"] if info else None,
        "name": text(call(item, "GetName")),
        "enabled": True if enabled is None else bool(enabled),
        "record_start_frame": record_start,
        "record_end_frame": record_end,
        "source_start_s": source_start,
        "source_end_s": source_end,
        "source_start_frame": clip_frames(integer(call(item, "GetLeftOffset")), fps, timeline_fps),
        "clip_fps": fps,
        "nested_in": None,
    }


def clip_entry(info, use, kind):
    return {
        "file_path": info["path"] if info and kind == "file" else None,
        "clip_type": info["type"] if info else None,
        "kind": kind,
        "vfe_video_id": info["video_id"] if info else None,
        "use": use,
    }


def nested(outer_use, inner, inner_clips):
    """The uses of a nested timeline's clips, moved onto the outer timeline: only what shows
    through the outer clip, positions in outer frames (both timelines at the same frame rate,
    as Resolve nests them by default)."""
    left = outer_use["source_start_frame"] or 0
    window_start = integer(call(inner, "GetStartFrame"), 0) + left
    shown = outer_use["record_end_frame"] - outer_use["record_start_frame"]
    window_end = window_start + shown
    # Frames of the inner timeline; the outer clip's rate when it does not say (nested
    # timelines take their parent's rate by default). Unknown: nothing trimmed.
    inner_fps = timeline_rate(inner) or outer_use["clip_fps"]
    for entry in inner_clips:
        use = entry["use"]
        start = max(use["record_start_frame"], window_start)
        end = min(use["record_end_frame"], window_end)
        if end <= start:
            continue
        cut_head = (start - use["record_start_frame"]) / inner_fps if inner_fps else 0.0
        cut_tail = (use["record_end_frame"] - end) / inner_fps if inner_fps else 0.0
        moved = dict(use)
        moved.update(
            {
                "track_type": outer_use["track_type"],
                "track": outer_use["track"],
                "track_name": outer_use["track_name"],
                "track_enabled": outer_use["track_enabled"] and use["track_enabled"],
                "enabled": outer_use["enabled"] and use["enabled"],
                "record_start_frame": outer_use["record_start_frame"] + (start - window_start),
                "record_end_frame": outer_use["record_start_frame"] + (end - window_start),
                "source_start_s": use["source_start_s"] + cut_head,
                "source_end_s": max(use["source_start_s"], use["source_end_s"] - cut_tail),
                "nested_in": use["nested_in"] or outer_use["name"],
            }
        )
        yield {**entry, "use": moved}


def timeline_clips(timeline, pool, *, tracks_known=True, depth=0, seen=()):
    """Every clip of every track, nested timelines read inside (up to NESTED_DEPTH).

    Resolve 21.1 answers GetIsTrackEnabled False for every track of a timeline that is not the
    one open: only the current timeline's answer is kept
    (``tracks_known``), the others' tracks count as switched on."""
    clips = []
    errors = 0
    timeline_fps = timeline_rate(timeline)
    for track_type in TRACK_TYPES:
        for track in range(1, integer(call(timeline, "GetTrackCount", track_type), 0) + 1):
            track_name = text(call(timeline, "GetTrackName", track_type, track)) or None
            enabled = None
            if tracks_known:
                enabled = call(timeline, "GetIsTrackEnabled", track_type, track)
            track_enabled = True if enabled is None else bool(enabled)
            for item in items(timeline, track_type, track):
                try:
                    info = pool.info(call(item, "GetMediaPoolItem"))
                    use = use_of(
                        item,
                        info,
                        timeline_fps=timeline_fps,
                        track_type=track_type,
                        track=track,
                        track_name=track_name,
                        track_enabled=track_enabled,
                    )
                    kind = kind_of(item, info)
                    inner = call(info["item"], "GetTimeline") if kind == "container" else None
                    inner_id = text(call(inner, "GetUniqueId")) if inner is not None else ""
                    if inner is not None and depth < NESTED_DEPTH and inner_id not in seen:
                        inner_clips, inner_errors = timeline_clips(
                            inner,
                            pool,
                            tracks_known=False,
                            depth=depth + 1,
                            seen=(*seen, inner_id),
                        )
                        errors += inner_errors
                        clips.extend(nested(use, inner, inner_clips))
                        continue
                    clips.append(clip_entry(info, use, kind))
                except Exception:  # noqa: BLE001 - one odd item never hides the others
                    errors += 1
    return clips, errors


# ---------------------------------------------------------------- commands
def read_project(resolve):
    project, result = project_parts(resolve)
    current = call(project, "GetCurrentTimeline")
    current_id = text(call(current, "GetUniqueId")) if current is not None else ""
    result["current_timeline_id"] = current_id or None
    started = time.monotonic()
    result["timelines"] = [
        timeline_info(t, current_id, count_clips=time.monotonic() - started < COUNT_BUDGET_S)
        for t in timelines(project)
    ]
    return result


def read_timeline(resolve, wanted):
    project, result = project_parts(resolve)
    current = call(project, "GetCurrentTimeline")
    current_id = text(call(current, "GetUniqueId")) if current is not None else ""
    if wanted in ("", "-"):
        if current is None:
            raise ReadError("no_timeline", "the project has no current timeline")
        timeline = current
    else:
        timeline = next(
            (t for t in timelines(project) if text(call(t, "GetUniqueId")) == wanted), None
        )
    if timeline is None:
        raise ReadError("timeline_not_found", "timeline not found: " + (wanted or "(current)"))
    counts = track_counts(timeline)
    result["timeline"] = timeline_info(timeline, current_id)
    open_now = bool(current_id) and text(call(timeline, "GetUniqueId")) == current_id
    result["clips"], result["errors"] = timeline_clips(timeline, Pool(), tracks_known=open_now)
    if project_id(resolve) != result["project"]["id"] or track_counts(timeline) != counts:
        raise ReadError("changed_during_read", "the project or the timeline changed while read")
    return result


def main(argv):
    if len(argv) < 3 or argv[2] not in ("project", "timeline"):
        raise ReadError("usage", "usage: timeline_reader.py <fusionscript> project|timeline [id]")
    resolve = connect(load_library(argv[1]))
    if argv[2] == "project":
        return read_project(resolve)
    return read_timeline(resolve, argv[3] if len(argv) > 3 else "-")


if __name__ == "__main__":
    end_by_deadline()
    try:
        answer = {"ok": True, **main(sys.argv)}
    except ReadError as exc:
        answer = {"ok": False, "error": exc.code, "message": exc.message}
    except Exception as exc:  # noqa: BLE001 - reported to the parent, which decides
        answer = {
            "ok": False,
            "error": "api_error",
            "message": type(exc).__name__ + ": " + str(exc),
        }
    sys.stdout.write(json.dumps(answer, ensure_ascii=True))
    sys.stdout.flush()
