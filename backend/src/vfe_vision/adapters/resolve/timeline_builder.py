"""Build a timeline in the project open in DaVinci Resolve, in a short-lived child process:
the one change the application makes in Resolve, when the user asks for it.

Run by ``adapters/resolve/builder.py`` with the application's Python, in isolated mode::

    python -I timeline_builder.py <fusionscript library> build  < request (JSON)

The request: ``name``, ``rate`` (as Resolve writes it: "29.97"), ``width``, ``height``, ``folder``
(the media pool bin that receives the new clips and the timeline), ``clips`` in order
(``[{"path", "video_id", "markers": [{"t_s", "duration_s", "name", "note", "color",
"custom_data"}]}]``), ``marker_kinds`` (the kinds an earlier build's markers are replaced of),
``subtitle_tracks`` (``[{"name", "path"}]``: a subtitle file per track, in the timeline's time,
laid on a track of that name) and ``subtitle_files`` (paths only imported into the
bin: the videos' own, when the timeline's cannot reach Resolve). The child imports the
files the media pool does not have yet (clips already there are used as they are), creates the
timeline (« name (2) » when the name is taken), makes it the current one, gives it the rate and
the size asked (Resolve keeps the project's rate when it refuses another: what the timeline has
is read back), puts the markers on the media pool clips, lays the subtitle tracks, appends the
clips whole, picture and sound (their copies on the timeline take the clips' markers), and gives
the media pool its current bin back. It prints ONE JSON object (ASCII only) on stdout, as the
reader does.

Standard library only. The helpers are the reader's, loaded from its file (isolated mode keeps
this folder out of the import path).
"""

import importlib.util
import json
import sys
from pathlib import Path

BATCH = 50  # clips per ImportMedia / AppendToTimeline call
TRIES = 3  # names tried when Resolve refuses to create a timeline under a free one
MAX_SUFFIX = 99  # « name (2) » … « name (99) »
VIDEO_ID_KEY = "vfe_video_id"  # read back by the reader as a link hint
MAX_SHIFT = 12  # frames tried after a marker's own when another marker is there


def _reader():
    spec = importlib.util.spec_from_file_location(
        "vfe_timeline_reader", Path(__file__).with_name("timeline_reader.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = _reader()
ReadError, call, integer, number, text = (
    reader.ReadError, reader.call, reader.integer, reader.number, reader.text,
)  # fmt: skip


def key(path):
    """A file path compared as Resolve and Windows do: separators and letter case aside."""
    return text(path).replace("\\", "/").casefold()


def file_path(clip):
    props = call(clip, "GetClipProperty")
    return text(props.get("File Path")) if isinstance(props, dict) else ""


def pool_clips(folder, found):
    found.extend(call(folder, "GetClipList") or [])
    for sub in call(folder, "GetSubFolderList") or []:
        pool_clips(sub, found)
    return found


def pool_index(root):
    """The media pool's files by path (the first clip of a file imported twice)."""
    index = {}
    for clip in pool_clips(root, []):
        path = file_path(clip)
        if path:
            index.setdefault(key(path), clip)
    return index


def bin_named(pool, root, name):
    """The bin ``name`` at the top of the media pool, made when missing; and whether it was."""
    for sub in call(root, "GetSubFolderList") or []:
        if text(call(sub, "GetName")) == name:
            return sub, False
    return call(pool, "AddSubFolder", root, name), True


def bin_clips(folder):
    """The clips of a bin by path."""
    found = {}
    for clip in call(folder, "GetClipList") or []:
        path = key(file_path(clip))
        if path:
            found.setdefault(path, clip)
    return found


def import_files(pool, paths, tried):
    """Import files into the current bin: the clips made there. The canonical form of 21.1
    first, then the plain list of paths when it made nothing (a build on Resolve 21.1.0 ended with
    no clip imported by the first). What a form made is read back from the bin, not
    only from its answer, so that no file is imported twice. ``tried``: what each form gave."""
    where = call(pool, "GetCurrentFolder")
    before = set(bin_clips(where))
    made = {}
    for form in ("dict", "list"):
        returned = 0
        for start in range(0, len(paths), BATCH):
            chunk = paths[start : start + BATCH]
            infos = [{"FilePath": path} for path in chunk] if form == "dict" else chunk
            items = call(pool, "ImportMedia", infos) or []
            returned += len(items)
            for item in items:
                if path := key(file_path(item)):
                    made.setdefault(path, item)
        for path, clip in bin_clips(where).items():
            if path not in before:
                made.setdefault(path, clip)
        tried.append(f"ImportMedia {form}: {returned} returned, {len(made)} in the bin")
        if made:
            break
    return list(made.values())


def free_names(project, wanted):
    taken = {text(call(timeline, "GetName")) for timeline in reader.timelines(project)}
    names = [wanted] + [f"{wanted} ({count})" for count in range(2, MAX_SUFFIX + 1)]
    return [name for name in names if name not in taken]


def create_timeline(pool, project, wanted):
    for name in free_names(project, wanted)[:TRIES]:
        timeline = call(pool, "CreateEmptyTimeline", name)
        if timeline is not None:
            return timeline
    raise ReadError("timeline_refused", "Resolve created no timeline named " + ascii(wanted))


def shape(timeline, rate, width, height):
    """The rate first (Resolve takes it only while the timeline is empty, and may keep the
    project's), then the picture size; what the timeline has in the end."""
    settings = call(timeline, "GetSettings") or {}
    have_rate = number(settings.get("timelineFrameRate"))
    if have_rate is None or abs(have_rate - float(rate)) > 0.001:
        call(timeline, "SetSettings", {"useCustomSettings": "1", "timelineFrameRate": rate})
    have_size = (
        integer(settings.get("timelineResolutionWidth")),
        integer(settings.get("timelineResolutionHeight")),
    )
    if have_size != (width, height):
        call(timeline, "SetSettings", {
            "useCustomSettings": "1",
            "timelineResolutionWidth": str(width),
            "timelineResolutionHeight": str(height),
        })  # fmt: skip
    after = call(timeline, "GetSettings") or {}
    return {
        "fps": number(after.get("timelineFrameRate")),
        "width": integer(after.get("timelineResolutionWidth")),
        "height": integer(after.get("timelineResolutionHeight")),
    }


def placed(timeline):
    return len(reader.items(timeline, "video", 1)) + len(reader.items(timeline, "audio", 1))


def append(pool, timeline, clips, tried):
    """Append whole clips, picture and linked sound, end to end on the current timeline (the new,
    empty one): the canonical form of 21.1, then the plain list when the timeline is still empty
    (as for the import, the tracks are read back rather than the answer trusted)."""
    for form in ("dict", "list"):
        for start in range(0, len(clips), BATCH):
            chunk = clips[start : start + BATCH]
            infos = [{"mediaPoolItem": clip} for clip in chunk] if form == "dict" else chunk
            call(pool, "AppendToTimeline", infos)
        count = placed(timeline)
        tried.append(f"AppendToTimeline {form}: {count} on the tracks")
        if count:
            return


def same(a, b):
    """Two answers of Resolve for the same timeline: their unique ids, else their names."""
    ids = text(call(a, "GetUniqueId")), text(call(b, "GetUniqueId"))
    if all(ids):
        return ids[0] == ids[1]
    return text(call(a, "GetName")) == text(call(b, "GetName"))


def open_new(pool, project, timeline):
    """Make the new timeline the current one. Clips are appended to the current timeline: one
    Resolve would not open is deleted, and nothing is appended anywhere."""
    call(project, "SetCurrentTimeline", timeline)
    current = call(project, "GetCurrentTimeline")
    if current is None or not same(current, timeline):
        call(pool, "DeleteTimelines", [timeline])
        raise ReadError("timeline_not_current", "Resolve did not open the new timeline")


def file_name(path_key):
    return path_key.rsplit("/", 1)[-1]


def gather(pool, index, wanted, tried):
    """The media pool clips of the wanted files, in order, and the files imported now (keys).
    A file imported now is found by its path, else by its name among the clips just imported
    (Resolve may write a path its own way)."""
    keys = {key(clip["path"]) for clip in wanted}
    to_import = list(dict.fromkeys(
        clip["path"] for clip in wanted if key(clip["path"]) not in index
    ))  # fmt: skip
    new, unmatched = set(), {}
    for item in import_files(pool, to_import, tried) if to_import else []:
        path = key(file_path(item))
        if path in keys and path not in index:
            index[path] = item
            new.add(path)
        elif path:
            unmatched.setdefault(file_name(path), item)
    for path in (key(p) for p in to_import):
        if path not in index and file_name(path) in unmatched:
            index[path] = unmatched.pop(file_name(path))
            new.add(path)
    placed_clips, missing = [], []
    for clip in wanted:
        item = index.get(key(clip["path"]))
        if item is None:
            missing.append(clip["path"])
            continue
        placed_clips.append((clip, item))
        if key(clip["path"]) in new and clip.get("video_id"):
            call(item, "SetThirdPartyMetadata", {VIDEO_ID_KEY: text(clip["video_id"])})
    return placed_clips, list(dict.fromkeys(missing)), new


def prefixes_of(kinds):
    """The ``customData`` beginnings of the application's markers of these kinds."""
    return tuple(f"vfe:{text(kind)}:" for kind in kinds or [] if text(kind))


def ours(markers, prefixes):
    """Among the markers ``GetMarkers()`` gave, those of these kinds: {frame: info}."""
    return {
        frame: info
        for frame, info in (markers if isinstance(markers, dict) else {}).items()
        if isinstance(info, dict) and text(info.get("customData")).startswith(prefixes)
    }


def mark(clip, markers, prefixes):
    """Our markers on a media pool clip, in its frames at its own rate (as Resolve reads the
    file): one frame, or the range of a suggestion (cut at the clip's end). Those an earlier
    build wrote (same kinds) are replaced, the user's left as they are: a marker falling on a
    frame one holds goes to the next free frame. (added, missed)."""
    if not markers:
        return 0, 0
    for frame, info in ours(call(clip, "GetMarkers"), prefixes).items():
        if not call(clip, "DeleteMarkerAtFrame", frame):
            call(clip, "DeleteMarkerByCustomData", text(info.get("customData")))
    props = call(clip, "GetClipProperty")
    props = props if isinstance(props, dict) else {}
    fps = number(props.get("FPS")) or 0.0
    frames = integer(props.get("Frames"), 0) or 0
    if fps <= 0:
        return 0, len(markers)
    taken = {integer(frame) for frame in (call(clip, "GetMarkers") or {})}
    added = 0
    for marker in markers:
        start = int((number(marker.get("t_s")) or 0.0) * fps + 1e-6)
        length = max(1, round((number(marker.get("duration_s")) or 0.0) * fps))
        for frame in range(start, start + MAX_SHIFT + 1):
            if frames and frame >= frames:
                break
            if frame in taken:
                continue
            taken.add(frame)  # added, or refused as held already
            if call(clip, "AddMarker", frame, text(marker.get("color")) or "Blue",
                    text(marker.get("name")), text(marker.get("note")),
                    max(1, min(length, frames - frame) if frames else length),
                    text(marker.get("custom_data"))):  # fmt: skip
                added += 1
                break
    return added, len(markers) - added


def mark_clips(placed_clips, prefixes):
    """The markers on each media pool clip placed (once per clip). (added, missed)."""
    added = missed = 0
    done = set()
    for clip, item in placed_clips:
        if key(clip["path"]) in done:
            continue
        done.add(key(clip["path"]))
        markers = [m for m in clip.get("markers") or [] if isinstance(m, dict)]
        counts = mark(item, markers, prefixes)
        added, missed = added + counts[0], missed + counts[1]
    return added, missed


def carry(timeline, prefixes, timeline_fps):
    """The timeline's clips with our markers: Resolve copies a media pool clip's markers onto
    the clip it appends (in the timeline's frames); one that did not get them is given them,
    turned from the clip's frames into the timeline's. (clips that took them, given them)."""
    took = given = 0
    for kind in ("video", "audio"):
        for item in reader.items(timeline, kind, 1):
            if ours(call(item, "GetMarkers"), prefixes):
                took += 1
                continue
            source = call(item, "GetMediaPoolItem")
            wanted = ours(call(source, "GetMarkers"), prefixes) if source is not None else {}
            props = call(source, "GetClipProperty") if wanted else {}
            clip_fps = number((props if isinstance(props, dict) else {}).get("FPS"))
            ratio = timeline_fps / clip_fps if timeline_fps and clip_fps else 1.0
            done = [
                call(item, "AddMarker", round((number(frame) or 0.0) * ratio),
                     text(info.get("color")) or "Blue", text(info.get("name")),
                     text(info.get("note")),
                     max(1, round((number(info.get("duration")) or 1.0) * ratio)),
                     text(info.get("customData")))
                for frame, info in wanted.items()
            ]  # fmt: skip
            given += 1 if any(done) else 0
    return took, given


def subtitle_tracks(request):
    """The subtitle tracks asked for: [(name, path)], in order."""
    return [
        (text(track.get("name")), text(track.get("path")))
        for track in request.get("subtitle_tracks") or []
        if isinstance(track, dict) and text(track.get("path"))
    ]


def import_subtitles(pool, root, paths, tried):
    """The subtitle files as clips of the current bin, by path. The copies an earlier build
    imported are deleted first: Resolve hands such a copy back as it was, not as the file is now,
    and the timelines that use it keep their captions (measured)."""
    wanted = {key(path) for path in paths}
    stale = [clip for clip in pool_clips(root, []) if key(file_path(clip)) in wanted]
    if stale:
        call(pool, "DeleteClips", stale)
    where = call(pool, "GetCurrentFolder")
    found = {}
    for item in call(pool, "ImportMedia", list(paths)) or []:  # the plain list: see import_files
        found.setdefault(key(file_path(item)), item)
    for path, clip in bin_clips(where).items():  # Resolve's answer aside, what the bin holds
        if path in wanted:
            found.setdefault(path, clip)
    tried.append(
        f"subtitles: {len(found)} of {len(paths)} imported, {len(stale)} earlier copies replaced"
    )
    return found


def enable(timeline, track, *, on):
    call(timeline, "SetTrackEnable", "subtitle", track, on)


def only_enabled(timeline, track, count):
    """Only this subtitle track enabled: Resolve lays a subtitle clip on the enabled one (with
    several enabled, not on the one last enabled every time)."""
    for other in range(1, count + 1):
        if other != track:
            enable(timeline, other, on=False)
    enable(timeline, track, on=True)


def lay(pool, timeline, clips, tracks, *, tried):
    """The subtitle tracks (``[(name, clip)]``, each a subtitle clip in the timeline's time), then
    the clips end to end; the names of the tracks laid. From a script, Resolve lays a
    subtitle clip on the enabled subtitle track, at the end of that track (and of the picture and
    sound only when a video was appended just before), whatever frame or track is asked, and dies
    on one given source frames: so each file goes first, alone enabled, on the empty timeline,
    and the clips then at their frames (``recordFrame``, which Resolve honours for a video)."""
    for number, (name, _) in enumerate(tracks, 1):
        call(timeline, "AddTrack", "subtitle")
        call(timeline, "SetTrackName", "subtitle", number, name)
    count = integer(call(timeline, "GetTrackCount", "subtitle"), 0) or 0
    laid = []
    for number, (name, srt) in enumerate(tracks, 1):
        only_enabled(timeline, number, count)
        before = len(reader.items(timeline, "subtitle", number))
        call(pool, "AppendToTimeline", [{"mediaPoolItem": srt}])  # NEVER source frames
        if len(reader.items(timeline, "subtitle", number)) > before:
            laid.append(name)
    for number in range(1, count + 1):
        enable(timeline, number, on=True)
    at = integer(call(timeline, "GetStartFrame"), 0) or 0
    at_end = 0
    for item in clips:
        got = call(pool, "AppendToTimeline", [{"mediaPoolItem": item, "recordFrame": at}]) or []
        if not got:  # the plain form, at the end
            got = call(pool, "AppendToTimeline", [item]) or []
            at_end += 1
        at = max((integer(call(i, "GetEnd"), 0) or 0 for i in got), default=at)
    tried.append(
        f"laid: {len(laid)} subtitle tracks, {len(clips)} clips at their frames ({at_end} at the "
        f"end)"
    )
    return laid


def build(resolve, request):
    _, project = reader.open_project(resolve)
    pool = call(project, "GetMediaPool")
    root = call(pool, "GetRootFolder") if pool is not None else None
    if root is None:
        raise ReadError("starting", "the media pool is not ready")
    previous = call(pool, "GetCurrentFolder")
    index = pool_index(root)
    wanted = [clip for clip in request["clips"] if text(clip.get("path"))]
    folder, made = bin_named(pool, root, request["folder"])
    if folder is not None:
        call(pool, "SetCurrentFolder", folder)
    tried = []
    try:
        placed_clips, missing, new = gather(pool, index, wanted, tried)
        ordered = [item for _, item in placed_clips]
        if not ordered:
            if made and folder is not None:
                call(pool, "DeleteFolders", [folder])  # nothing was put in it
            raise ReadError(
                "no_media",
                f"Resolve could not open any of the {len(wanted)} files ({'; '.join(tried)})",
            )
        timeline = create_timeline(pool, project, request["name"])
        open_new(pool, project, timeline)
        shaped = shape(timeline, request["rate"], request["width"], request["height"])
        prefixes = prefixes_of(request.get("marker_kinds"))
        marked, missed = mark_clips(placed_clips, prefixes)  # before the append: it copies them
        tracks = subtitle_tracks(request)
        loose = [text(path) for path in request.get("subtitle_files") or [] if text(path)]
        paths = list(dict.fromkeys([path for _, path in tracks] + loose))
        srts = import_subtitles(pool, root, paths, tried) if paths else {}
        laying = [(name, srts[key(path)]) for name, path in tracks if key(path) in srts]
        if laying:
            laid = lay(pool, timeline, ordered, laying, tried=tried)
        else:
            laid = []
            append(pool, timeline, ordered, tried)
        if not placed(timeline):  # the empty timeline made is taken back; the clips stay
            call(pool, "DeleteTimelines", [timeline])
            raise ReadError(
                "nothing_placed",
                f"Resolve placed none of the {len(ordered)} clips ({'; '.join(tried)})",
            )
        clips = len(reader.items(timeline, "video", 1))
        if marked:
            took, given = carry(timeline, prefixes, shaped["fps"] or number(request["rate"]))
            tried.append(
                f"markers: {marked} on the clips, {missed} missed; timeline clips: "
                f"{took} took them, {given} given them"
            )
        subtitles = [text(call(srts[key(p)], "GetName")) for p in paths if key(p) in srts]
    finally:
        if previous is not None:
            call(pool, "SetCurrentFolder", previous)
    used = {key(clip["path"]) for clip in wanted} - {key(path) for path in missing}
    return {
        "project": {
            "id": text(call(project, "GetUniqueId")),
            "name": text(call(project, "GetName")),
        },
        "timeline": {
            "id": text(call(timeline, "GetUniqueId")),
            "name": text(call(timeline, "GetName")),
            **shaped,
        },
        "folder": text(call(folder, "GetName")) if folder is not None else "",
        "clips": clips,
        "imported": len(new),
        "reused": len(used - new),
        "missing": missing,
        "markers": marked,
        "markers_missed": missed,
        "subtitles": subtitles,
        "subtitles_laid": laid,
        "tried": tried,
    }


def read_request(raw):
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ReadError("usage", "the request is not JSON: " + str(exc)) from exc
    clips = request.get("clips") if isinstance(request, dict) else None
    if not isinstance(clips, list) or not clips or not text(request.get("name")):
        raise ReadError("usage", "the request needs a name and clips")
    return request


def main(argv):
    if len(argv) < 3 or argv[2] != "build":
        raise ReadError("usage", "usage: timeline_builder.py <fusionscript> build < request")
    request = read_request(sys.stdin.read())
    return build(reader.connect(reader.load_library(argv[1])), request)


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
    sys.stdout.write(json.dumps(answer, ensure_ascii=True))
    sys.stdout.flush()
