<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo/lockup.svg">
  <img src="docs/brand/logo/lockup-light.svg" alt="Video Frame Expedition for DaVinci Resolve" height="96">
</picture>

**English** · [Français](README.fr.md)

[![Checks](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve/actions/workflows/checks.yml/badge.svg)](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve/actions/workflows/checks.yml)

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

**Windows and macOS.** This repository holds the application for Windows 11 and for Macs with
Apple Silicon: one code base, one installation per system. It was developed on Windows, with an
NVIDIA graphics card; on a Mac, the analyses, HDR videos included, and the link with DaVinci
Resolve were tried on an M1 Mac.

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
  option (System page), on Windows with an NVIDIA card, Whisper can borrow the GPU when the
  vision model leaves enough memory (`vfe models cuda-runtime`).
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

- **The application**: Windows 11, or a Mac with Apple Silicon (M1 or later) on macOS 15 or
  later, which DaVinci Resolve 21 requires. The same code serves both.
  [uv](https://docs.astral.sh/uv/), Node.js 24 LTS (the web interface is built at the first
  start), FFmpeg and ExifTool: `scripts/bootstrap.ps1` (Windows) or `scripts/bootstrap.sh` (Mac)
  installs them. On a Mac, FFmpeg comes in its full build, `ffmpeg-full`, whose zscale filter
  turns HDR videos into frames for the vision model.
- **LM Studio**, with the local server enabled and a vision model loaded (e.g.
  `qwen/qwen3-vl-8b`): on the same computer or on another one, under Windows, macOS or Linux.
- **DaVinci Resolve Studio 21.1 or later**, for the link with Resolve: on the same computer or
  on another one, under Windows, macOS or Linux.

**On Windows**, the application was developed and tested with an NVIDIA graphics card, which it
uses when the vision model leaves enough memory (video decoding and, as an option, speech).
Without one, that work runs on the processor. **On a Mac**, the vision model runs in LM Studio
on the chip's graphics cores and shares the unified memory with Resolve and the application;
video decoding, speech, on-screen text and the other models run on the processor. 32 GB of
memory are comfortable for an 8-billion-parameter vision model next to Resolve; with 16 GB,
prefer a 4-billion one and a short context.

## Installation

### On Windows

First get the application:
`git clone https://github.com/VideoFrameExpedition/video-frame-expedition-resolve.git`,
or GitHub's "Code › Download ZIP" button, then unzip it.

1. Double-click `install.bat`, in the application's folder. Windows may warn that it comes
   from the Internet: "More info", then "Run anyway". It removes that mark from every file of
   the folder, then runs `scripts\bootstrap.ps1` (in PowerShell, from the folder:
   `powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1`, which takes the same
   options). At the end, it offers to start the application in the same window.

   The script uses `winget` to install what is missing (uv, Node.js, FFmpeg, ExifTool; Node.js
   and ExifTool from their official ZIP when their installer is refused), then
   the application's Python packages and its models (about 2 GB, downloaded once;
   `-NoModels` skips them), and adds "Video Frame Expedition" to the Start menu. When Smart
   App Control (Windows 11) is on or in evaluation, it also installs Python 3.12 from
   python.org, signed, on which the application runs; then it asks Windows whether it refuses
   any of the application's compiled files, and names those. It asks
   whether to install LM Studio on this PC: the vision model
   can also run in the LM Studio of another computer. `-WithLMStudio` or `-NoLMStudio` gives
   the answer in advance. Accept the Windows (UAC) prompts. Its messages are in English, or in
   French on a Windows set to French; `VFE_LANG=en` or `VFE_LANG=fr` in the `.env` file decides,
   for `run.bat` and the `vfe` commands too.
2. In LM Studio, download a vision model (for example `qwen/qwen3-vl-8b`), load it and start the
   local server (see below when it runs on another computer).
3. Open "Video Frame Expedition" from the Start menu, or double-click `run.bat`. The first time,
   it builds the web interface (one or two minutes), then opens the browser. Started before the
   installation, `run.bat` offers to do it.

### On a Mac

1. Open the Terminal (⌘ Space, then type "Terminal"), paste this line and press Return:

   ```sh
   /bin/sh -c "$(curl -fsSL https://raw.githubusercontent.com/VideoFrameExpedition/video-frame-expedition-resolve/main/install.sh)"
   ```

   It downloads the application into `~/video-frame-expedition-resolve`, uses
   [Homebrew](https://brew.sh) to install what is missing (Homebrew itself first, when the Mac
   does not have it: it asks for your password; then uv, Node.js, the full build of FFmpeg,
   ExifTool), the application's Python packages and its models (about 2 GB, downloaded once),
   and adds "Video Frame Expedition" to your Applications folder. It asks whether to install
   LM Studio on this Mac and, at the end, offers to start the application in the same window.
   Run again, the same line updates the application. Its messages follow the Mac's language
   (English or French).

   Already have the application's folder (`git clone` or ZIP)? Double-click `install.command`:
   the same installation, from that folder, with nothing downloaded again. Coming from a ZIP,
   macOS refuses to open it the first time: System Settings › Privacy & Security › "Open
   Anyway". It then removes the quarantine mark from the whole folder. Its options
   (`--no-models`, `--with-lm-studio`, `--without-lm-studio`) are given in the Terminal:
   `sh install.sh --without-lm-studio`. `VFE_LANG` in the `.env` file can decide the language
   of the messages.
2. In LM Studio, download a vision model (for example `qwen/qwen3-vl-4b`, or `qwen/qwen3-vl-8b`
   with 32 GB of memory), load it and start the local server.
3. Open "Video Frame Expedition" from Launchpad, Spotlight or the Applications folder (or
   double-click `run.command` in the application's folder): it starts the application in a
   Terminal window. The first time, it builds the web interface (one or two minutes), then
   opens the browser; started before the installation, it offers to do it. macOS may ask whether the Terminal may access your Movies, your Documents
   or an external disk: accept, the application reads your videos there.

   If macOS refuses to open `run.command` the first time (a file downloaded as a ZIP carries a
   quarantine mark; a folder obtained with `git clone` does not), allow it in System Settings ›
   Privacy & Security, or remove the mark in the Terminal, from the application's folder:
   `xattr -dr com.apple.quarantine .`

### Updating

**Open "Video Frame Expedition - update" (Start menu or Applications folder), or double-click
`update.bat` (Windows) or `update.command` (Mac) in the application's folder.** It asks you to
close the application if it is running, looks on GitHub for the latest version and, when there
is one, puts it in the same folder and installs what it needs; then it offers to start the
application. Your library, your settings and the models are kept: they live in the data folder
(`%LOCALAPPDATA%\vfe-vision`, or `~/Library/Application Support/vfe-vision` on a Mac), which the
update does not touch, and the `.env` file stays as it is. Before changing the structure of its
database, the new version backs it up (`backups` folder, next to it).

A folder obtained with `git clone` (on a Mac, the installation line) is updated with `git pull`.
Otherwise the update downloads the latest release and replaces the application's files: what the
new version no longer has goes from the application's own folders (`backend`, `frontend`,
`scripts`, `docs`), and anything else you put in the folder stays. `update.bat -From <ZIP or
folder>` (`update.command --from …`) installs that version instead.

Up to version 1.2.1, which did not have it, update by hand once: download the new version
(Code › Download ZIP), unzip it and double-click `install.bat` in the new folder (copy your
`.env` file over if you made one); on a Mac, paste the installation line again.

### LM Studio on another computer

When LM Studio runs on another computer, download and load the model over there, and let its
server accept the local network (Developer › Server Settings › "Serve on Local Network"); once
the application is open, give its address on the System page, "LM Studio" card.

**Command line.** In this page, `vfe <command>` stands for the following command, typed in
PowerShell (on a Mac, in the Terminal) from the application's folder:

```powershell
uv run --frozen --no-dev --project backend python -m vfe_vision <command>
```

For example, `vfe doctor` checks FFmpeg, ExifTool, LM Studio and the GPU (on a Mac, the chip
and its memory); `vfe doctor --binaries` only what Windows (Smart App Control) thinks of the
application's compiled files.

## Quick start

**Day to day: open "Video Frame Expedition" (Start menu or Applications folder), or double-click
`run.bat` (Windows) or `run.command` (Mac).** It starts the
application (interface, MCP and analyses) and opens the browser on http://127.0.0.1:8765. If the
application is already running, it simply opens the interface. After an update, it first
rebuilds the web interface; `run.bat build` (or `run.command build`) rebuilds it even when
nothing has changed. To stop the application, close its window (on a Mac, the Terminal's, or
press Ctrl+C).

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
[`docs/env.example`](docs/env.example) to a `.env` file next to `run.bat` (or `run.command`).
Everything else is set in the interface.

## Development

The development tasks go through [just](https://just.systems) (`winget install Casey.Just` on
Windows, `brew install just` on a Mac); every recipe of the `justfile` can also be run by hand,
for instance if Smart App Control blocks `just.exe` on Windows. Typing is checked for the three
systems: `mypy --platform win32`, `darwin` and `linux`.

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
   metadata. No existing timeline is modified and **the project is not saved**: press Ctrl+S
   (Cmd+S on a Mac) in Resolve if you keep the edit.

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
documents above exist in both languages, and so do the messages of the launchers, of the
installation scripts and of the command line, which follow the system's language.

## Licence

Free and open source, under the [Apache License 2.0](LICENSE). You may use, modify, integrate
and share it, including for paid work, as long as you keep the copyright notice and the
[NOTICE](NOTICE) file. Provided as is, without warranty or support.

Not affiliated with or endorsed by Blackmagic Design or Anthropic; DaVinci Resolve, Claude and
the other products named here are trademarks of their respective owners.

## Contributions

This repository is published so that the application can be installed and its code read. It does
not take code contributions: pull requests are not merged. To report a bug, [open an
issue](https://github.com/VideoFrameExpedition/video-frame-expedition-resolve/issues/new/choose): the form asks for the system (Windows or macOS, and its version), the graphics card or the Mac, the model loaded in
LM Studio and the error message. To report a security flaw, see [SECURITY.md](SECURITY.md).
