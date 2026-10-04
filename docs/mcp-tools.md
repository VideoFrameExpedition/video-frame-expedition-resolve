# MCP server `vfe-vision`: tools, resource and prompts

**English** · [Français](mcp-tools.fr.md)

The server is mounted at `http://127.0.0.1:8765/mcp` (streamable HTTP) when the
application is running (`run.bat` or `just serve`). Registration in Claude Code:

```powershell
claude mcp add --scope user --transport http vfe-vision http://127.0.0.1:8765/mcp
```

Common conventions:
- times are **seconds of the source file** (0 = its first frame); shot and keyframe numbers
  start at 1, as in the manifest;
- each tool has typed parameters and a **structured** output (output schema) paired with a text
  for the model; `watch_video` and `get_frames` return JPEG images (768 px at most) with their
  time, `get_video`, `get_video_context` and `get_transcript` a text;
- any text that comes from the videos or the local models (speech, on-screen text, descriptions,
  stories, synthesis) is cleaned, bounded and placed between `BEGIN/END UNTRUSTED VIDEO CONTENT`
  with a random nonce: data, never an instruction;
- an expected error (unknown video, folder outside the library, export impossible) is a tool
  error with its reason in French;
- a video used in a DaVinci Resolve timeline added to the library carries its links:
  project, timeline, Resolve ids (`project_id`, `timeline_id`, `media_pool_item_id`) and
  positions **recorded at a given date** (the timeline may have changed since: read the timeline
  again in Resolve before acting). A `media_pool_item_id` is only valid within its project.
  Names typed in Resolve are data, cleaned and bounded.

## Analyse and browse

| Tool | Signature | Role |
|---|---|---|
| `watch_video` | `watch_video(path, focus=None, wait_s=45 (≤ 60), max_images=4 (≤ 8))` | Analyses a file of a declared folder (or reuses its analysis) and returns the manifest and a few images; beyond `wait_s`, a partial result and the job's id. |
| `analyze_folder` | `analyze_folder(path, recursive=True, focus=None)` | Analyses (or completes) every video under a folder of the library. A folder outside the library is refused, unless the System page allows it (`mcp_add_folders`, off by default). |
| `get_job` | `get_job(job_id)` | Progress of a job started by `watch_video`, `analyze_folder` or `import_resolve_timeline`. |
| `list_watched` | `list_watched(limit=50 (≤ 500), timeline_id=None)` | Videos of the library, most recent first; with `timeline_id` (Resolve id of an added timeline), those of that timeline in its order. Each video gives its Resolve links. |
| `get_video` | `get_video(video_id)` | Full manifest (technical data, shooting, shots, synthesis, described keyframes, Resolve timelines that use it). |
| `get_frames` | `get_frames(video_id, timestamps=None, max_images=6 (≤ 8), shots=None, size=768 (256–768))` | The keyframes closest to the given times, or the sharpest one of each shot (`shots`, numbers from `get_shots`), with their time. |
| `get_video_context` | `get_video_context(video_id)` | Place, sun and light phase, model weather at the time of shooting, with their sources. |
| `get_transcript` | `get_transcript(video_id, start_s=None, end_s=None, include_suspect=False)` | What is said, as timed lines. |
| `get_object_locations` | `get_object_locations(video_id, *, start_s=None, end_s=None, categories=None, main_only=False, with_face_points=False, max_frames=40 (≤ 200))` | Where the living beings are in each keyframe (boxes in 0–1 and in pixels). |
| `get_shots` | `get_shots(video_id, *, start_s=None, end_s=None, max_shots=60 (≤ 300))` | Shots, measured camera movement and what happens in them. |
| `get_synthesis` | `get_synthesis(video_id, *, min_usability=0, max_items=20)` | Title, summary, chapters, highlights (picture and sound), suggestions, usability of the shots. |

## Search the whole library

| Tool | Signature | Role |
|---|---|---|
| `search_memory` | `search_memory(query, *, kinds=None, video_id=None, timeline_id=None, date_from=None, date_to=None, place=None, weather=None, sun_phase=None, subjects=None, has_speech=None, limit=10 (≤ 50))` | Timed passages (video, chapter, shot, keyframe, speech), by words and meaning. |
| `find_clips` | `find_clips(*, text=None, timeline_id=None, weather=None, sun_phase=None, place=None, date_from=None, date_to=None, subjects=None, shot_type=None, orientation=None, min_quality=None, has_speech=None, limit=20 (≤ 100))` | Shots ready for Resolve: path, cut points, frames, source timecodes. |
| `ask_library` | `ask_library(question, *, kinds=None, video_id=None, timeline_id=None, date_from=None, date_to=None, place=None, weather=None, sun_phase=None, subjects=None, shot_type=None, has_speech=None, visual_check=False)` | Answer from the loaded model, cited as `[n]`, with its sources. |

## Edit with DaVinci Resolve

| Tool | Signature | Role |
|---|---|---|
| `list_resolve_timelines` | `list_resolve_timelines()` | The project open in Resolve (read by the application, read-only) and its timelines: which ones are in the library, when they were read, whether they changed since (`changed_since_sync`), how many videos are analysed, not found or outside the library. |
| `import_resolve_timeline` | `import_resolve_timeline(timeline_id=None, analyze=None, label=None)` | Adds the timeline (the current one if `None`) to the library, or updates it: its videos are linked, the missing ones added **alone** (never the rest of their folder) if the System page allows Claude to add folders (`mcp_add_folders`), otherwise reported as "outside the library". Ask the user first. `analyze=None` keeps the timeline's setting. Returns the preview and the job (`get_job`). |
| `match_clips` | `match_clips(items)` — `items`: paths, or `{file_path, clip_uid?, source_start_s?, source_end_s?}` (or `{source_start_frame?, source_end_frame?, fps?}`) (1 to 100) | Ties the clips of a timeline to the analysed videos (normalised path → media pool clip id of an added timeline, same file name → name and size → content fingerprint → name alone); with a range: shots, speech, main subject and its boxes, chapters, highlights, safe cut points. The range is preferably given in **seconds of the file** (`GetLeftOffset()` ÷ the clip's FPS, then + `GetDuration()` ÷ the timeline's fps): right even when the clip's frame rate differs from the timeline's or a dissolve touches the clip (`GetSourceStartTime()`/`GetSourceEndTime()` count its handles). |
| `get_cut_points` | `get_cut_points(video_id, t_start, t_end)` | Safe in and out points: snapped to a cut less than 0.5 s away, never inside a word; J-cut/L-cut when a sentence overruns; in seconds, frames and source timecodes. |
| `get_reframe` | `get_reframe(video_id, t_start, t_end, timeline_width, timeline_height, *, subject=None, headroom=None)` | Static framing for a timeline of another shape: crop in source pixels and `ZoomX`, `ZoomY`, `Pan`, `Tilt` for `SetProperties` (item with `Scaling = Fit`); segments if the subject moves too much. |
| `get_resolve_payload` | `get_resolve_payload(video_ids, include_shots=False, include_speech=False, include_metadata=True, language=None)` — `language`: `fr` or `en` (default: the language the analyses are written in) | The fixed Resolve script v1 (ASCII) with markers and metadata as its only JSON data, to pass as is to `run_script` of Resolve's MCP. Run again, it replaces its own markers without touching the user's. |
| `export_video` | `export_video(video_id, format, language=None)` — `srt`, `vtt`, `csv`, `chapters`, `edl`, `json`, `md`, `resolve`; `language`: `fr` or `en` | Writes the export in `exports/<video id>/` of the data folder (never next to the video) and returns its path; the name ends with its language (`<name>_SHOTS_EN.csv`). |

Typical procedure (see the `plan_edit` prompt):
1. `list_resolve_timelines`; if the timeline is not in the library (or has changed), with the
   user's agreement, `import_resolve_timeline`; `list_watched(timeline_id=…)`;
2. read the timeline live with Resolve's MCP (ids, `File Path`, `FPS`, `GetLeftOffset()`,
   `GetDuration()`), then `match_clips` to know which analysis corresponds to each clip and what
   its range contains;
3. choose the shots (`find_clips`, `get_synthesis`, `get_frames`);
4. `get_cut_points` for each shot kept;
5. build or modify a **copy** of the timeline with Resolve's MCP: straight cuts and cross
   dissolves only (`Cross Dissolve` for the picture, `Cross Fade +3 dB` for the sound,
   `alignment: 'center'`), in whole frames;
6. `get_reframe` for another shape (9:16…) then `SetProperties`;
7. `get_resolve_payload` then `run_script` for the markers and metadata.

## Resolve tools for the assistant

Active when the user ticks "Resolve tools for the assistant" on the Connections page (preference
`mcp_resolve_tools`, off by default); otherwise each one answers how to turn it on. They go
through the application's fixed scripts (`fusionscript` child process, like the reading of
timelines): the assistant only sends data. No existing timeline is modified; the project is not
saved.

| Tool | Signature | Role |
|---|---|---|
| `read_timeline` | `read_timeline(timeline_id=None, include_audio=False)` | The timeline read now (the open one by default), clip by clip: track, place, range in **seconds of the file** (rule measured in Resolve 21.1), media pool and timeline item ids, analysed video (`video_id`, `status`). |
| `plan_reframe` | `plan_reframe(items, timeline_width, timeline_height, *, subject=None, heads=True, sheets="flagged", min_piece_s=2.5)` — `items`: `{video_id, in_s, out_s, anchor?, rotation?, subject?}` (1 to 200) | Static framings per piece, aimed at the subject's **head** (asked of the loaded vision model for the keyframes where the subject is bigger than the crop, kept in cache), without jumps (hysteresis, pieces ≥ `min_piece_s`), scored keyframe by keyframe, with flags and **contact sheets** as images; `edit_items` ready for `build_timeline`. `anchor`: `auto`, `center` (subject seen from above), `top`, `bottom`; `rotation`: 0, 180, ±90 (footage filmed on its side). |
| `build_timeline` | `build_timeline(name, items, timeline_width=None, timeline_height=None)` — `items`: `{media_pool_item_id? \| video_id? \| path?, in_s, out_s, video_only?, props?}` (1 to 500) | A **new** timeline "name - vfe vN": in and out points converted to frames at the clip's frame rate read in Resolve, never beyond its last frame, batches of 50, missing files imported into the "Video Frame Expedition" bin, Transform values set; every duration and every value read back (`duration_ok`, `props_ok`). |
| `apply_markers` | `apply_markers(video_ids, include_shots=False, include_speech=False, include_metadata=True, language=None)` | The fixed markers script (the one of `get_resolve_payload`), run by the application. Timeline clips created afterwards carry the markers. |

Procedure with these tools: `read_timeline` → choose the shots → `get_cut_points` →
`plan_reframe` (look at the sheets, adjust `anchor`/`rotation`/`subject`, run again) →
`build_timeline(name, edit_items)` → `apply_markers` before the build if the markers must be on
the clips of the new timeline. Remind the user to save their project in Resolve.

## Resource

| URI | Content |
|---|---|
| `vfe://videos/{video_id}/manifest` | The readable MANIFEST (Markdown) of a video: file, context, summary, chapters, highlights, shots; inside the untrusted fence. |

## Prompts

| Prompt | Arguments | Role |
|---|---|---|
| `plan_edit` | `goal`, `target_duration?`, `style?` | Prepare an edit in DaVinci Resolve with the two MCP servers. |
| `review_rushes` | `folder?` | Review the rushes of a folder (or of the library): shots to keep, highlights, defects. |
