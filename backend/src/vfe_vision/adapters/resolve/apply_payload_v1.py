"""Video Frame Expedition -> DaVinci Resolve: markers and metadata on media pool clips (fixed
script, v1).

Shipped with Video Frame Expedition for DaVinci Resolve, never generated: the
application only replaces the ``PAYLOAD`` line below with the same line holding a JSON
string literal, so no text from the footage or from a model can ever run as code. Run it
with the Resolve MCP's ``run_script`` (its sandbox has no ``os``, ``sys`` or ``pathlib``: only
``json`` and plain Python here) or from Workspace > Scripts; ``resolve`` (and ``project``) are
the globals Resolve defines.

The whole file is ASCII (French messages use \\u escapes): ``run_script`` loses the non-ASCII
characters of a script's text (Resolve 21.1, Windows). The payload is ASCII too (JSON escapes).

For each clip of the payload, found in the media pool (every bin) by its normalised File Path:

1. the markers this script wrote before (customData starting with ``vfe:``) are removed, so a
   new run replaces them; the user's own markers are never touched;
2. the markers are added, their times converted from seconds to frames with the clip's own
   ``FPS`` property (a variable frame rate phone clip has the rate Resolve gave it); a frame
   already holding a marker (Resolve keeps one per frame) moves ours to the next free one;
3. the metadata are written: a Description or Comments the user changed since the last run is
   kept, and the user's own keywords stay next to the application's; what the last run wrote and
   the video's id in the application go to the third-party metadata.

Only the canonical 21.1 calling forms are used (``GetClipProperty()[key]``, ``GetMetadata()``,
``SetMetadata({...})``), frames are integers, and every return value is checked (the API answers
False or None rather than raising). ``result`` reports what was applied, the clips not found in
the media pool and the errors.
"""

import json

SCRIPT = "vfe-vision-resolve"
SCRIPT_VERSION = 1
PAYLOAD = json.loads("{}")
# What the last run wrote, to tell the user's own edits apart. Stored in the users' Resolve
# projects under the application's former name: changing it would lose that record.
THIRD_PARTY_KEY = "VFE Vision"
VIDEO_ID_KEY = "vfe_video_id"
MAX_SHIFT = 12  # frames tried after one already holding a marker
LENGTH_TOLERANCE = 0.01  # Resolve's frame count this far from the analysed duration: warned

AFTER_END = "apr\u00e8s la fin du clip"
FRAMES_TAKEN = "images d\u00e9j\u00e0 occup\u00e9es par d'autres marqueurs"
NO_FPS = "fr\u00e9quence d'images inconnue"
OTHER_LENGTH = (
    "longueur du clip dans Resolve diff\u00e9rente de l'analyse (fr\u00e9quence variable ?) : "
    "marqueurs peut-\u00eatre d\u00e9cal\u00e9s"
)
BAD_PAYLOAD = "donn\u00e9es absentes ou d'un autre format"
BAD_VERSION = "version de donn\u00e9es non prise en charge par ce script"
NO_PROJECT = "aucun projet ouvert dans DaVinci Resolve"


def match_key(path, case_insensitive=True):
    """The comparison form of a path (same rule as ``vfe_vision.domain.clip_paths``)."""
    text = str(path).strip().strip('"').replace("\\", "/")
    if text[:8].lower() == "//?/unc/":
        text = "//" + text[8:]
    elif text[:4] in ("//?/", "//./"):
        text = text[4:]
    lead = "//" if text.startswith("//") else ("/" if text.startswith("/") else "")
    parts = []
    for part in text[len(lead) :].split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(part)
    key = lead + "/".join(parts)
    return key.casefold() if case_insensitive else key


def media_pool_clips(folder, found):
    """Every clip of a media pool folder and of its sub-folders (bins)."""
    found.extend(folder.GetClipList() or [])
    for sub in folder.GetSubFolderList() or []:
        media_pool_clips(sub, found)
    return found


def number(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if abs(result) < float("inf") else default  # neither NaN nor infinite


def as_dict(value):
    return value if isinstance(value, dict) else {}


def remove_ours(clip, prefix):
    """Remove the markers a previous run wrote; return (removed, failed)."""
    removed, failed = 0, 0
    for frame, info in list(as_dict(clip.GetMarkers()).items()):
        data = str(as_dict(info).get("customData") or "")
        if not data.startswith(prefix):
            continue
        if clip.DeleteMarkerByCustomData(data) or clip.DeleteMarkerAtFrame(frame):
            removed += 1
        else:
            failed += 1
    return removed, failed


def add_marker(clip, marker, fps, frames, taken):
    """Add one marker at its frame, else at the next free one (Resolve holds one marker per
    frame); return (frame used or None, why it could not be added)."""
    start = int(number(marker.get("t_s")) * fps + 1e-6)
    wanted = max(1, round(number(marker.get("duration_s")) * fps))
    for frame in range(start, start + MAX_SHIFT + 1):
        if frames and frame >= frames:
            return None, AFTER_END
        if frame in taken:
            continue
        duration = min(wanted, frames - frame) if frames else wanted
        added = clip.AddMarker(
            frame,
            str(marker.get("color") or "Blue"),
            str(marker.get("name") or ""),
            str(marker.get("note") or ""),
            max(1, duration),
            str(marker.get("custom_data") or ""),
        )
        taken.add(frame)  # added, or refused because a marker is there already
        if added:
            return frame, None
    return None, FRAMES_TAKEN


def keywords_of(text):
    return [k.strip() for k in str(text or "").split(",") if k.strip()]


def write_metadata(clip, wanted, video_id):
    """Write the metadata; return (written fields, fields kept as the user wrote them, fields
    Resolve refused)."""
    raw = as_dict(clip.GetThirdPartyMetadata()).get(THIRD_PARTY_KEY)
    try:
        before = json.loads(raw) if raw else {}
    except ValueError:
        before = {}
    before = as_dict(before)
    present = as_dict(clip.GetMetadata())
    record, update, kept = dict(before), {}, []
    for field, value in wanted.items():
        current = str(present.get(field) or "")
        if field == "Keywords":
            ours_before = {str(k).casefold() for k in before.get("Keywords") or []}
            users = [k for k in keywords_of(current) if k.casefold() not in ours_before]
            seen = {k.casefold() for k in users}
            ours = [str(k) for k in value if str(k).casefold() not in seen]
            text = ", ".join(users + ours)
            record["Keywords"] = ours
        else:
            text = str(value)
            if current and current != text and current != before.get(field):
                kept.append(field)  # written by the user: theirs
                continue
            record[field] = text
        if text != current:
            update[field] = text
    refused = []
    if update and not clip.SetMetadata(update):  # all or nothing: find the key Resolve refuses
        refused = [key for key in sorted(update) if not clip.SetMetadata({key: update[key]})]
        for key in refused:
            record[key] = before.get(key)
    saved = {THIRD_PARTY_KEY: json.dumps(record), VIDEO_ID_KEY: video_id}
    if not clip.SetThirdPartyMetadata(saved):
        refused.append("third-party")
    return [key for key in sorted(update) if key not in refused], kept, refused


def apply_clip(clip, entry, prefix):
    properties = as_dict(clip.GetClipProperty())
    fps = number(properties.get("FPS")) or number(entry.get("fps"))
    if fps <= 0:
        raise RuntimeError(NO_FPS)
    frames = int(number(properties.get("Frames")))
    warnings = []
    expected = number(entry.get("duration_s")) * fps
    if frames and expected and abs(frames - expected) > max(2.0, LENGTH_TOLERANCE * expected):
        warnings.append(OTHER_LENGTH)
    removed, not_removed = remove_ours(clip, prefix)
    taken = {int(frame) for frame in as_dict(clip.GetMarkers())}
    added, shifted, failed = 0, [], []
    for marker in entry.get("markers") or []:
        name = str(marker.get("name") or "")
        frame, why = add_marker(clip, marker, fps, frames, taken)
        if frame is None:
            failed.append(name + " : " + str(why))
            continue
        added += 1
        start = int(number(marker.get("t_s")) * fps + 1e-6)
        if frame != start:
            shifted.append(name + " : +" + str(frame - start))
    metadata = as_dict(entry.get("metadata"))
    written, kept, refused = [], [], []
    if metadata:
        written, kept, refused = write_metadata(clip, metadata, str(entry.get("video_id") or ""))
    return {
        "file": entry.get("file_name"),
        "clip": clip.GetName(),
        "fps": fps,
        "frames": frames,
        "markers_added": added,
        "markers_shifted": shifted,
        "markers_removed": removed,
        "markers_not_removed": not_removed,
        "markers_failed": failed,
        "metadata_written": written,
        "metadata_kept": kept,
        "metadata_refused": refused,
        "warnings": warnings,
    }


def run(resolve, project, payload):
    report = {"script": SCRIPT, "version": SCRIPT_VERSION, "applied": [], "not_found": [],
              "errors": []}  # fmt: skip
    if not isinstance(payload, dict) or payload.get("format") != SCRIPT:
        report["errors"].append(BAD_PAYLOAD)
        return report
    if payload.get("version") != SCRIPT_VERSION:
        report["errors"].append(BAD_VERSION)
        return report
    if project is None and resolve is not None:
        project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        report["errors"].append(NO_PROJECT)
        return report
    case_insensitive = bool(payload.get("case_insensitive", True))
    prefix = str(payload.get("prefix") or "vfe:")
    by_path = {}
    for clip in media_pool_clips(project.GetMediaPool().GetRootFolder(), []):
        path = as_dict(clip.GetClipProperty()).get("File Path")
        if path:
            by_path.setdefault(match_key(path, case_insensitive), []).append(clip)
    for entry in payload.get("clips") or []:
        clips = by_path.get(match_key(entry.get("path") or "", case_insensitive)) or []
        if not clips:
            report["not_found"].append({"file": entry.get("file_name"), "path": entry.get("path")})
        for clip in clips:
            try:
                report["applied"].append(apply_clip(clip, entry, prefix))
            except Exception as exc:  # noqa: BLE001 - one clip's failure never stops the others
                report["errors"].append(str(entry.get("file_name")) + " : " + str(exc))
    return report


# The run_script sandbox has no globals(): read the names Resolve defines, if it does.
try:
    RESOLVE = resolve  # type: ignore[name-defined]
except NameError:
    RESOLVE = None
try:
    PROJECT = project  # type: ignore[name-defined]
except NameError:
    PROJECT = None
result = run(RESOLVE, PROJECT, PAYLOAD)
