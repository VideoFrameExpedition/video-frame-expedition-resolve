<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo/lockup.svg">
  <img src="docs/brand/logo/lockup-light.svg" alt="Video Frame Expedition for DaVinci Resolve" height="96">
</picture>

**English** · [Français](README.fr.md)

[![Checks](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve-windows/actions/workflows/checks.yml/badge.svg)](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve-windows/actions/workflows/checks.yml)

**Video Frame Expedition for DaVinci Resolve analyses your rushes with a local vision model, so
that an AI assistant can edit knowing what each shot contains.**

**Analysis:** metadata, place, time, sun and weather of the shoot; shots, keyframes and subjects;
sounds, speech and on-screen text. In English and in French.

**Semantic search:** shots, keyframes, speech and chapters become timed passages, found by
keywords and by meaning (hybrid search: full text and vectors computed locally). Filters by
weather, light, place, dates, subjects, framing or quality; the local model answers questions
about the whole library and cites its sources.

**Resilient database:** each video is recognised by its content, not its path: moved or renamed,
it keeps its analyses without being analysed again. Analysis files next to the videos bring them
back on another computer or after the database is lost.

**For AI assistants** (Claude, Cursor, VS Code, Codex): an MCP server with 25 tools. The AI
assistant reads the timeline open in Resolve, knows what each clip contains, searches the whole
library for shots, gets safe cut points and reframing, and builds the edit in a new timeline.
Simple tasks (describing frames, answering about the library, locating a subject) go to the local
model: the AI assistant only receives the results and saves its tokens.

**Beyond Resolve's MCP:** four tools drive Resolve Studio 21.1 through fixed, tested scripts
rather than code rewritten for each request: reading the timeline with exact source ranges,
reframing (9:16…) centred on the subjects, a new timeline built then checked value by value,
markers. They work around known pitfalls of Resolve 21.1's API, never modify an existing
timeline, and also drive a remote Resolve, which Blackmagic's MCP server does not do yet. The AI
assistant can still work on the open timeline through Resolve's MCP.

**With DaVinci Resolve, both ways:** selected videos become a timeline with subtitles and
markers; a Resolve timeline enters the library and its videos are analysed.

**Stand-alone:** a web interface to browse the analyses, search and query the whole library.

**Windows version.** This repository holds the application for Windows 11. It was developed
and tested with an NVIDIA graphics card, which it uses when the vision model leaves enough
memory (video decoding, and optionally speech recognition). Without one, that work runs on the
processor, and the vision model runs on whatever LM Studio supports on your computer; other
graphics cards have not been tested. A macOS version is planned as a separate repository.

> Video presentation, nine minutes: [youtu.be/G0WT96QsGsU](https://youtu.be/G0WT96QsGsU);
> in French: [youtu.be/1EI36bRbdWo](https://youtu.be/1EI36bRbdWo).
>
> This is the new version of *Video Frame Expedition*; it replaces the earlier one.

Your frames and sounds never leave your machine, except, if you choose, to go to LM Studio on
another of your computers; the vision model runs on your graphics card. The analyses make only
two network calls (an approximate position and a date, to find the place and the weather), and
one switch on the System page turns them off; the same switch hides the OpenStreetMap map of the
Context tab, which loads its tiles from the internet. The help page loads its fonts from Google
Fonts, and its presentation videos from YouTube (youtube-nocookie.com, only when you scroll to
them).

## Principles

- **Local first**: your frames and sounds never leave your machine. The only data sent out (GPS
  coordinates and a date, for the place and the weather) can be switched off on the System page,
  together with the map of the Context tab.
- **Escalation between models**: the simple tasks go to the local model, on your machine
  (analysing the videos, answering a question about the library with `ask_library`, locating a
  subject in a frame with `plan_reframe`), and the assistant only receives the result. The
  complex level (understanding a request, choosing the shots, editing) belongs to the frontier
  model (Claude…), which costs more. It no longer has to look at hundreds of frames or write
  scripts for Resolve, so it uses far fewer tokens.
- **Your rushes stay intact**: the only thing written in your video folders is a small analysis
  file per video and per language (`<name>_FR.txt`, `<name>_EN.txt`; JSON, without images),
  which can be switched off on the System page. It brings the analyses back without redoing them
  (lost database, another computer); it also holds the GPS position and what is said, so sharing
  the folder shares them too. When you create a timeline in DaVinci Resolve with subtitles, each
  video also receives its own: `<name>_EN.srt` (what is said, in the spoken language) and
  `<name>_SHOTS_EN.srt` (the shots, in the interface language). A file the application did not
  write, or that you have changed, is never replaced.
- **The vision model stays on the GPU**: the application uses the model you loaded in LM Studio,
  and never reloads it or loads another one during the analyses. It decodes videos on the GPU
  only when that model leaves enough free memory (with `qwen/qwen3-vl-4b`, for example).
- **Choosing your vision model**: the "Model bench" page compares the LM Studio models you tick,
  on frames from your library. Each one is loaded alone, queried the way the analyses query it,
  then unloaded; a table gives the graphics memory used, the time per frame, the valid answers,
  whether the answers are in the requested language, the text read and the positions, and you
  grade the descriptions blind. A ranking, profiles and charts sum these measures up, and a
  history keeps every test: the overall ranking compares the latest result of each model across
  all tests. This is the only place where the application loads a model, at your request; it
  then reloads the one that was there before.
- **LM Studio here or elsewhere**: by default the application talks to LM Studio on this
  computer. The "LM Studio" card of the System page can point it to another computer on your
  local network or on Tailscale, one with a more powerful graphics card. The application tests
  that computer before switching to it and keeps the past connections one click away. The frames
  of your videos then go to that computer, and to that computer only.
- **Two languages**: every analysis exists in French and in English. The models write in one
  language (System page), then the "Translation" step translates their texts into the other,
  without redoing anything; the interface shows them, and exports them (files, timelines,
  subtitles), in its own language. The file names end with their language: `_FR`, `_EN`.
- **A finished analysis is kept**: running the analysis again only does what is missing.
  "Update" and "Redo everything" are explicit choices.
- **Sounds, speech and text on the CPU**: YAMNet (sounds and instruments) with a second opinion
  from CED-small ("sounds heard": birds, frogs, insects, rain, footsteps… with their timestamps),
  Whisper large-v3-turbo (transcription, in a separate process) and PP-OCRv6 (on-screen text)
  run on the processor. Their models are downloaded once by the installation script. As an
  option (System page), Whisper can borrow the GPU when the vision model leaves enough memory
  (`vfe models cuda-runtime`).
- **Where the subjects are**: a box around every living being (people, animals, insects) on the
  keyframes, positions that can be reused to reframe (MCP `get_object_locations`). The vision
  model already loaded finds them all, then D-FINE and YuNet (on the CPU) tighten the boxes and
  complete the crowds. Positions only: nobody is identified.
- **What happens in each shot**: the vision model describes what happens in each shot from
  several of its frames, in order (an insect taking off, a hand adding an ingredient), with the
  time of each frame. Shots tab, timeline strip and MCP `get_shots`.
- **Finding everything again**: the Search page looks through the whole library for what is
  seen, said, heard or read, by keywords and by meaning (EmbeddingGemma, on the CPU), with
  filters (weather, light, place, dates, subjects, framing…); one click opens the video at the
  right moment. MCP `search_memory` and `find_clips` (shots ready for Resolve).
- **Asking questions**: the Questions page answers a question about the whole library with the
  loaded model, quoting its sources (one click opens the video at the right moment), with an
  optional check against the frames. MCP `ask_library`.
- **Exporting**: Exports tab of each video (SRT/VTT subtitles, shots as CSV for Excel, YouTube
  chapters, marker EDL, JSON analysis, MANIFEST sheet, Resolve script) and a CSV table of the
  selected videos. Nothing is written next to the videos.
- **Creating a timeline**: the ticked videos, end to end, in shooting order (or another order),
  as a file to import into DaVinci Resolve (File › Import › Timeline) or, when Resolve is open,
  directly in the current project. As you choose, the transcript and the shot descriptions
  become subtitles (one track each), the suggestions become duration markers and the chapters
  become markers. The download is a ZIP: OTIO for Resolve, FCPXML for Final Cut Pro, SRT files
  and a README.txt. When the timeline is created directly, the subtitles are placed on it (a
  "Transcript" track, a "Shots" track) and also written next to each video.

## Requirements

- Windows 11.
- [uv](https://docs.astral.sh/uv/), Node.js 24 LTS (the web interface is built at the first
  start), FFmpeg and ExifTool: `scripts/bootstrap.ps1` installs them.
- LM Studio with the local server enabled and a vision model loaded (e.g. `qwen/qwen3-vl-8b`),
  on this computer or on another computer of your network (System page, "LM Studio" card).
- DaVinci Resolve Studio 21.1 or later, for the link with Resolve.

## Installation

1. Get the application:
   `git clone https://github.com/VideoFrameExpedition/video-frame-expedition-resolve-windows.git`,
   or GitHub's "Code › Download ZIP" button, then unzip it.
2. In PowerShell, from the application's folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
   ```

   The script uses `winget` to install what is missing (uv, Node.js, FFmpeg, ExifTool,
   LM Studio), then the application's Python packages and its models (about 2 GB, downloaded
   once; `-SansModeles` skips them). Accept the Windows (UAC) prompts. Its messages are in
   French.
3. In LM Studio, download a vision model (for example `qwen/qwen3-vl-8b`), load it and start the
   local server.
4. Double-click `run.bat`. The first time, it builds the web interface (one or two minutes),
   then opens the browser.

**Command line.** In this page, `vfe <command>` stands for the following command, typed in
PowerShell from the application's folder:

```powershell
uv run --frozen --no-dev --project backend python -m vfe_vision <command>
```

For example, `vfe doctor` checks FFmpeg, ExifTool, LM Studio and the GPU.

## Quick start

**Day to day: double-click `run.bat`.** It starts the application (interface, MCP and analyses)
and opens the browser on http://127.0.0.1:8765. If the application is already running, it
simply opens the interface. `run.bat build` first rebuilds the web interface after an update.
To stop the application, close its window.

**The "Help" page** in the sidebar is the complete guide: twelve parts, the seven tabs of a video
one by one, some fifty screenshots of the French interface, with the text in French and in
English. It is the file
[`frontend/public/help/index.html`](frontend/public/help/index.html), served at
http://127.0.0.1:8765/help/index.html; the folder can also be hosted elsewhere as it is.

**The presentation video** (nine minutes, ten chapters) is on
[YouTube](https://youtu.be/G0WT96QsGsU) and in the help page; so is its French version (eight
minutes), on [YouTube](https://youtu.be/1EI36bRbdWo) and in the French help page.

Connecting assistants (Claude Code, Claude Desktop, Cursor, VS Code, Codex) and using the
application from your other devices through Tailscale: "Connections" page of the interface and
[guide](docs/guide/connect-mcp.md).

Settings read at start-up (address and port, paths of the tools, address of LM Studio): copy
[`docs/env.example`](docs/env.example) to a `.env` file next to `run.bat`. Everything else is
set in the interface.

## Development

The development tasks go through [just](https://just.systems) (`winget install Casey.Just`);
every recipe of the `justfile` can also be run by hand if Smart App Control blocks `just.exe`.

```powershell
just setup      # backend + frontend dependencies, pre-commit hook
just build      # builds the web interface
just serve      # starts the application on http://127.0.0.1:8765
```

| Command | Role |
|---|---|
| `just dev-backend` / `just dev-frontend` | development servers (API :8765, Vite :5173) |
| `just check` | lint, strict typing, architecture contracts and tests (backend + frontend) |
| `just test-live` | tests that use LM Studio, the models, the GPU or the internet |
| `just gen-client` | regenerates the OpenAPI schema and the TypeScript client |

## Editing with Claude Code and DaVinci Resolve

The `vfe-vision` server has 25 tools. Four of them drive DaVinci Resolve Studio 21.1 on the
assistant's behalf when the **"Resolve tools for the assistant"** box of the Connections page is
ticked (off by default). The approach:

1. `read_timeline` reads the open timeline and links each clip to its analysed video, with
   correct ranges in seconds; `match_clips` says what each range contains;
2. Claude chooses the shots (`find_clips`, `get_synthesis`, `get_frames`) and asks
   `get_cut_points` for safe in and out points (never in the middle of a word, with J-cuts and
   L-cuts);
3. `plan_reframe` prepares the reframing for another aspect ratio (9:16…): the image work is
   done locally, by the vision model loaded in LM Studio (answers kept in a cache), and Claude
   only looks at the contact sheets of the shots that were flagged;
4. `build_timeline` builds a **new** timeline "… - vfe vN" with these shots and these reframes,
   reads every duration and every value back, and `apply_markers` places chapters, highlights and
   metadata. No existing timeline is modified and **the project is not saved**: press Ctrl+S in
   Resolve if you keep the edit.

Without the box, Claude writes the same steps as scripts for the MCP server of DaVinci Resolve
Studio (`match_clips`, `get_reframe` and `get_resolve_payload` provide the data; the `plan_edit`
prompt describes this approach and has it work in a copy of the timeline), with straight cuts
and cross-dissolves only.

A Claude Code plugin connects Claude Code to this server and adds a skill with the editing
method:
[video-frame-expedition-claude-plugin](https://github.com/VideoFrameExpedition/video-frame-expedition-claude-plugin).
Install it with `/plugin marketplace add VideoFrameExpedition/video-frame-expedition-claude-plugin`,
then `/plugin install video-frame-expedition@video-frame-expedition`.

Claude adds a new folder to the library (`analyze_folder`) only if the System page allows it.
All the tools: [docs/mcp-tools.md](docs/mcp-tools.md).

## Documentation

- [Architecture](docs/architecture.md)
- [Tools, resource and prompts of the MCP server](docs/mcp-tools.md)
- [User guides](docs/guide/)
- [Security](SECURITY.md) · [Third-party licences](THIRD_PARTY_NOTICES.md)
- [The logo](docs/brand/README.md)

The application was developed in French. The interface, its help page, this page and the
documents above exist in both languages; the messages of the launcher and of the command line
are in French.

## Licence

Free and open source, under the [Apache License 2.0](LICENSE). You may use, modify, integrate
and share it, including for paid work, as long as you keep the copyright notice and the
[NOTICE](NOTICE) file. Provided as is, without warranty or support.

Not affiliated with or endorsed by Blackmagic Design or Anthropic; DaVinci Resolve, Claude and
the other products named here are trademarks of their respective owners.

## Contributions

This repository is published so that the application can be installed and its code read. It does
not take code contributions: pull requests are not merged. To report a bug, [open an
issue](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve-windows/issues/new/choose): the form asks for the Windows version, the graphics card, the model loaded in
LM Studio and the error message. To report a security flaw, see [SECURITY.md](SECURITY.md).
