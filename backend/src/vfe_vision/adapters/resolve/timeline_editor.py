"""Edit in the project open in DaVinci Resolve for the assistant, in a short-lived child process:
a new timeline from an edit list, and the fixed markers script.

Run by ``adapters/resolve/editor.py`` with the application's Python, in isolated mode::

    python -I timeline_editor.py <fusionscript library> edit     < request (JSON, ASCII)
    python -I timeline_editor.py <fusionscript library> markers  < {"script": <fixed script>}

``edit``: ``name``, ``folder`` (the media pool bin that receives files not in the pool yet),
``width``/``height`` (optional: the new timeline's size), ``items`` in order
(``[{"uid", "path", "in_s", "out_s", "video_only", "props"}]``: the media pool clip by its unique
id, else by its file path, imported when the pool does not have it; the range in seconds of the
file; ``props`` the Transform values ZoomX, ZoomY, Pan, Tilt, RotationAngle). The rules measured
in Resolve 21.1 live here, so that nobody
writes them again: seconds become frames with the clip's own FPS read in Resolve (a « 30 fps »
phone clip is 29.97 there), ``endFrame`` is exclusive and never past the clip's last frame, clips
go in batches of 50, every duration and every Transform value is read back. The timeline is
always a NEW one (« name (2) » when the name is taken): an existing timeline is never changed.
The project is never saved: the user decides.

``markers``: runs the application's fixed script ``apply_payload_v1.py`` (built by
``adapters/resolve/script.py``: the data is a JSON literal, never code from the footage or a
model) with ``resolve`` and ``project`` defined, and returns its ``result``.

It prints ONE JSON object (ASCII only) on stdout, as the reader does. Standard library only; the
helpers are the reader's and the builder's, loaded from their files.
"""

import importlib.util
import json
import sys
from pathlib import Path

BATCH = 50  # clips per AppendToTimeline call
TOLERANCE = {"ZoomX": 0.001, "ZoomY": 0.001, "Pan": 0.2, "Tilt": 0.2, "RotationAngle": 0.01}
PROPS = tuple(TOLERANCE)


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = _load("vfe_timeline_reader", "timeline_reader.py")
builder = _load("vfe_timeline_builder", "timeline_builder.py")
ReadError, call, integer, number, text = (
    reader.ReadError, reader.call, reader.integer, reader.number, reader.text,
)  # fmt: skip
key = builder.key


# ---------------------------------------------------------------- the clips of the edit list
def pool_by_uid(root):
    found = {}
    for clip in builder.pool_clips(root, []):
        uid = text(call(clip, "GetUniqueId"))
        if uid:
            found.setdefault(uid, clip)
    return found


def find_clips(pool, root, items, folder_name, tried):
    """The media pool clip of each item (None when Resolve has none), importing the files the pool
    does not have into the bin ``folder_name``; and how many files were imported."""
    by_uid = pool_by_uid(root)
    by_path = builder.pool_index(root)
    wanted = []
    for item in items:
        clip = by_uid.get(text(item.get("uid"))) if item.get("uid") else None
        if clip is None and text(item.get("path")):
            clip = by_path.get(key(item["path"]))
        wanted.append(clip)
    to_import = list(dict.fromkeys(
        text(item["path"]) for item, clip in zip(items, wanted, strict=True)
        if clip is None and text(item.get("path"))
    ))  # fmt: skip
    imported = 0
    if to_import:
        previous = call(pool, "GetCurrentFolder")
        folder, _ = builder.bin_named(pool, root, folder_name)
        if folder is not None:
            call(pool, "SetCurrentFolder", folder)
        try:
            imported = len(builder.import_files(pool, to_import, tried))
        finally:
            if previous is not None:
                call(pool, "SetCurrentFolder", previous)
        by_path = builder.pool_index(root)
        names = {}
        for path_key, clip in by_path.items():
            names.setdefault(builder.file_name(path_key), clip)
        for index, item in enumerate(items):
            if wanted[index] is None and text(item.get("path")):
                wanted[index] = by_path.get(key(item["path"])) or names.get(
                    builder.file_name(key(item["path"]))
                )
    return wanted, imported


def clip_info(clip, item, timeline_fps):
    """What AppendToTimeline gets for one item, and the duration expected on the timeline (in
    its frames: a clip at another rate plays at real speed); or the reason it cannot go."""
    props = call(clip, "GetClipProperty")
    props = props if isinstance(props, dict) else {}
    fps = number(props.get("FPS"))
    if not fps or fps <= 0:
        return None, "no_fps"
    frames = integer(props.get("Frames"), 0) or 0
    start = max(0, round((number(item.get("in_s")) or 0.0) * fps))
    end = round((number(item.get("out_s")) or 0.0) * fps)
    if frames:
        end = min(end, frames)  # never past the last frame (the analysis may count one more)
    if end <= start:
        return None, "empty_range"
    info = {"mediaPoolItem": clip, "startFrame": start, "endFrame": end}  # endFrame exclusive
    if item.get("video_only"):
        info["mediaType"] = 1
    expected = round((end - start) * timeline_fps / fps) if timeline_fps else end - start
    return info, expected


# ---------------------------------------------------------------- the new timeline
def size_to(timeline, width, height):
    """The picture size asked (Resolve may answer False and still apply it: read back)."""
    if width and height:
        call(timeline, "SetSettings", {
            "useCustomSettings": "1",
            "timelineResolutionWidth": str(int(width)),
            "timelineResolutionHeight": str(int(height)),
        })  # fmt: skip
    after = call(timeline, "GetSettings") or {}
    return {
        "fps": number(after.get("timelineFrameRate")),
        "width": integer(after.get("timelineResolutionWidth")),
        "height": integer(after.get("timelineResolutionHeight")),
    }


def transform(resolve, item, props, fit_needed):
    """Set the Transform values and read them back: whether every one holds."""
    wanted = {name: float(props[name]) for name in PROPS if name in props}
    if fit_needed:  # the values assume the clip scaled to fit (the project's default)
        fit = getattr(resolve, "SCALE_FIT", None)
        if fit is not None:
            call(item, "SetProperties", {"Scaling": fit})
    call(item, "SetProperties", wanted)
    got = call(item, "GetProperties") or {}
    return all(
        number(got.get(name)) is not None and abs(number(got.get(name)) - value) <= TOLERANCE[name]
        for name, value in wanted.items()
    )


def edit(resolve, request):
    _, project = reader.open_project(resolve)
    pool = call(project, "GetMediaPool")
    root = call(pool, "GetRootFolder") if pool is not None else None
    if root is None:
        raise ReadError("starting", "the media pool is not ready")
    items = [item for item in request["items"] if isinstance(item, dict)]
    tried = []
    clips, imported = find_clips(pool, root, items, text(request.get("folder")) or "vfe", tried)
    missing = [
        {"n": index + 1, "why": "not_in_pool"} for index, clip in enumerate(clips) if clip is None
    ]
    if len(missing) == len(items):
        raise ReadError("no_media", f"none of the {len(items)} clips is in Resolve ({tried})")
    timeline = builder.create_timeline(pool, project, text(request["name"]))
    builder.open_new(pool, project, timeline)
    shaped = size_to(timeline, request.get("width"), request.get("height"))
    timeline_fps = shaped["fps"] or reader.timeline_rate(timeline)
    queued = []  # (n, item, info, expected)
    for index, (item, clip) in enumerate(zip(items, clips, strict=True)):
        if clip is None:
            continue
        info, expected = clip_info(clip, item, timeline_fps)
        if info is None:
            missing.append({"n": index + 1, "why": expected})
            continue
        queued.append((index + 1, item, info, expected))
    placed, short = [], 0
    for start in range(0, len(queued), BATCH):
        chunk = queued[start : start + BATCH]
        before = len(reader.items(timeline, "video", 1))
        call(pool, "AppendToTimeline", [info for _, _, info, _ in chunk])
        new = reader.items(timeline, "video", 1)[before:]
        short += max(0, len(chunk) - len(new))
        placed.extend(zip(chunk, new, strict=False))
    if not placed:
        call(pool, "DeleteTimelines", [timeline])
        raise ReadError("nothing_placed", f"Resolve placed none of the {len(queued)} clips")
    scaling = text(call(project, "GetSetting", "timelineInputResMismatchBehavior"))
    fit_needed = bool(scaling) and scaling != "scaleToFit"
    rows = []
    for (n, item, _, expected), timeline_item in placed:
        duration = integer(call(timeline_item, "GetDuration"), 0) or 0
        props = item.get("props") if isinstance(item.get("props"), dict) else None
        row = {
            "n": n,
            "id": text(call(timeline_item, "GetUniqueId")),
            "record_start": integer(call(timeline_item, "GetStart"), 0),
            "record_end": integer(call(timeline_item, "GetEnd"), 0),
            "duration": duration,
            "duration_ok": abs(duration - expected) <= 1,
        }
        if props:
            row["props_ok"] = transform(resolve, timeline_item, props, fit_needed)
        rows.append(row)
    return {
        "project": {
            "id": text(call(project, "GetUniqueId")),
            "name": text(call(project, "GetName")),
        },
        "timeline": {
            "id": text(call(timeline, "GetUniqueId")),
            "name": text(call(timeline, "GetName")),
            "start_frame": integer(call(timeline, "GetStartFrame"), 0),
            "end_frame": integer(call(timeline, "GetEndFrame"), 0),
            **shaped,
        },
        "items": rows,
        "missing": missing,
        "not_placed": short,
        "imported": imported,
        "audio_items": len(reader.items(timeline, "audio", 1)),
        "scaling": scaling,
        "tried": tried,
    }


# ---------------------------------------------------------------- markers
def markers(resolve, request):
    script = text(request.get("script"))
    if not script.isascii() or "SCRIPT_VERSION = " not in script:
        raise ReadError("usage", "not the application's fixed script")
    _, project = reader.open_project(resolve)
    scope = {"resolve": resolve, "project": project, "__name__": "vfe_markers"}
    exec(compile(script, "apply_payload_v1.py", "exec"), scope)  # noqa: S102 - the fixed script
    return {"result": scope.get("result")}


def read_request(raw, command):
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ReadError("usage", "the request is not JSON: " + str(exc)) from exc
    if not isinstance(request, dict):
        raise ReadError("usage", "the request is not an object")
    if command == "edit" and (not text(request.get("name")) or not request.get("items")):
        raise ReadError("usage", "the request needs a name and items")
    return request


def main(argv):
    if len(argv) < 3 or argv[2] not in ("edit", "markers"):
        raise ReadError("usage", "usage: timeline_editor.py <fusionscript> edit|markers < request")
    request = read_request(sys.stdin.read(), argv[2])
    resolve = reader.connect(reader.load_library(argv[1]))
    return edit(resolve, request) if argv[2] == "edit" else markers(resolve, request)


if __name__ == "__main__":
    reader.end_by_deadline()
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
    sys.stdout.write(json.dumps(answer, ensure_ascii=True, default=str))
    sys.stdout.flush()
