# Security

**English** · [Français](SECURITY.fr.md)

Video Frame Expedition for DaVinci Resolve is a **local** application: the server listens on
`127.0.0.1` by default. It can also listen on the computer's Tailscale address
(`VFE_TAILSCALE=true`); it never listens on `0.0.0.0` of its own accord.

## Threat model

- **Malicious web pages** that would try to call the local API (CSRF, DNS rebinding): `Origin`
  header check, custom `X-VFE-Client` header mandatory on every write, host allowlist
  (`TrustedHost`). No page of the application can be shown inside another site's frame
  (`X-Frame-Options`, `frame-ancestors`). A file path taken from an address is checked on its
  text before any disk access: a network path (`\\host\share`) is never opened, so Windows
  never sends the user's credentials to a host named by a web page.
- **Untrusted content in the videos** (transcriptions, on-screen text) that could contain
  instructions aimed at an LLM: this content is always framed as "untrusted" in the prompts and
  the MCP responses, and by default the MCP server cannot add a folder to the library.
- **File access**: the API only reads files located under the declared library roots. There it
  only writes the analysis file of each video, one per language (`<name>_FR.txt`,
  `<name>_EN.txt`; can be turned off) and, when you create a timeline with subtitles,
  its `.srt` files: atomic write, never in place of a file it did not write, never a
  folder created. When read back, an analysis file is only taken over if it carries the
  application's format and the video's fingerprint and size.
- **Code execution**: no `shell=True` call; no code is generated from AI output. The DaVinci
  Resolve markers script is fixed and only reads a JSON file; the child processes that talk to
  Resolve only run their fixed commands: an assistant only sends them data.
- **Writing to DaVinci Resolve**: the application adds to the open project; it replaces nothing
  and never saves it. "Create a timeline" acts at the user's request. The assistant's
  Resolve tools are **off by default** (Connections page): they create new timelines
  "… - vfe vN" and put markers and metadata on the media pool clips, replacing only those they
  put there. `build_timeline` also accepts a file path as Resolve sees it: Resolve then imports
  that file into the "Video Frame Expedition" bin, without the application reading it or
  checking that it lies under a library root.
- **Loading models in LM Studio**: the "Model bench" page asks LM Studio, through its
  local API, to load and unload the vision models the user has ticked, among those LM Studio
  already has; nothing is downloaded. This request is a write like any other (`Origin` checked,
  `X-VFE-Client` header, token when remote). No analysis and no MCP tool loads a model. On a
  server compatible with OpenAI's API, the application loads and unloads nothing, and the model
  bench does not start.
- **A model server on another computer**: by default, the frames of the videos do not
  leave the computer (`http://127.0.0.1:1234`). The user can designate another model server on
  the System page, an LM Studio or a server compatible with OpenAI's API (vLLM…): the frames and
  the texts of the analyses are then sent to it, unencrypted on a local network (http),
  encrypted on Tailscale or over https. The card says so before saving and the diagnostics show
  it as a warning. The accepted address is a host and a port, with no credentials; only the
  address of an OpenAI-compatible server keeps a path (`/v1` by default), made of plain
  segments. The connection test only lists the models (LM Studio's `GET /api/v1/models`, or the
  `GET /v1/models` of an OpenAI-compatible server) and returns only counts, the kind of server
  and the names of the vision models loaded; on an OpenAI-compatible server, it also sends its
  first vision model one tiny plain grey image, to tell whether it sees images. The API token of
  a model server is sent only to its address, is never returned by the API, and is stored in
  clear text in the local database. No MCP tool changes this address.
- **Compiled extension refused by Windows**: when Smart App Control refuses an
  extension of a Python package that also exists in pure Python, the application renames that
  file in its own environment (`….pyd.refused`) and restarts. It never runs a refused file and
  changes no Windows setting.
- **Compiled files checked**: at the end of the installation, and on the System page, the
  application asks Windows whether it refuses each of the compiled files (`.pyd`, `.dll`) of
  the packages it uses and of Python itself. To do so, Windows maps the file as it would to run
  it, without running its code or loading what it depends on (`DONT_RESOLVE_DLL_REFERENCES`).
  A refusal is named (file and package); the application does not work around it.
- **Network calls**: once installed, the analyses make only two. Nominatim (OpenStreetMap)
  receives a position rounded to three decimal places (within about 70 metres), to name the
  place; Open-Meteo receives a position rounded to two decimal places (within about
  700 metres) and a date, for the weather. One switch on the System page turns both off, and
  also hides the map of the Context tab, whose tiles come from OpenStreetMap. The help page
  loads its fonts from Google Fonts and its presentation videos from youtube-nocookie.com, only
  when the reader reaches them. The models are downloaded at installation, or when you ask for
  one (Hugging Face, PyPI, GitHub, GeoNames). The application sends no telemetry.
- **Child processes**: the analysis worker, the transcription engine and the scripts that talk
  to DaVinci Resolve are tied to the application (a Job Object on Windows; a process group and a
  lifeline on a Mac): if it goes away, they stop with it, and nothing of them goes on in the
  background. On a Mac, the application also runs `osascript` (the window to choose a folder),
  `diskutil`, `sysctl` and `pgrep`, to read only.
- **Installation**: `install.bat`, `install.command` and the Mac's installation line
  (`install.sh`) install what is missing through winget or Homebrew, which ask for the
  administrator's consent themselves when needed, then download the models. On Windows, when
  Smart App Control is on, Node.js comes from its official ZIP (nodejs.org), and ExifTool from
  its own (exiftool.org) when winget could not install it: each is checked against its
  published SHA-256 checksum and installed for the user only. When Smart App Control is on or
  in evaluation, Python 3.12 comes from python.org (signed, through winget, for the user)
  rather than from uv's download, which is not signed.
  `install.bat`
  removes the mark of the Web from the files of the application's folder, `install.command`
  their quarantine mark, and nothing outside that folder. On a Mac, the installation also adds
  "Video Frame Expedition" to the user's Applications folder: a small application made on the
  spot, signed on the spot (ad hoc signature), that opens `run.command`; on Windows, a "Video
  Frame Expedition" shortcut to `run.bat`, in the user's Start menu. The Mac's line runs a
  script downloaded from GitHub: it can be read first (`install.sh`, at the root of the
  repository).
- **Update**: `update.bat` and `update.command` download from the application's repository on
  GitHub only, over HTTPS: the address of its latest release, then that release's ZIP (a
  folder that comes from Git is updated with `git pull`). They replace the files of the
  application's folder and remove, from its own folders (`backend`, `frontend`, `scripts`,
  `docs`), those the new version no longer has; never the data folder, the `.env` file, the
  application's environment or anything else in the folder. Then they run the new version's
  installation. The installation puts "Video Frame Expedition - update" next to the
  application (Start menu, Applications folder): it opens them.
- **Export, import and reset of the data** (System page): an export is prepared by a write
  request (the `X-VFE-Client` header, see above), then downloaded once through an unguessable
  link; it holds the settings, saved tokens included. An import keeps only the database and the
  frames from an archive, refuses any path leading out of the data folder, and accepts only a
  sound database of the application, of a version it knows. The import and the reset are
  carried out at the next start, before anything opens the database, once a copy of the current
  one is in the `backups` folder; the restart asked from the interface stops the application as
  Ctrl+C does, and its launcher (`run.bat`, `run.command`) starts it again.

## Access from other devices

- **Local** means: arriving from a loopback address **and** with a loopback host (`127.0.0.1`,
  `localhost`, `::1`). These requests never need a token. A request relayed by a proxy on the
  computer (different host) is treated as remote.
- **Every remote request** presents the token: `Authorization: Bearer` header, `access_token`
  parameter (media, SSE), or the session cookie of the sign-in page. `/mcp` only accepts the
  `Authorization` header.
- **Token**: `VFE_API_TOKEN`, otherwise generated (256 bits) at the first start with remote
  access, in `api-token.txt` of the data folder, readable by the user only; `vfe token` shows
  it, `vfe token --rotate` replaces it (`vfe` as defined under "Command line" in the
  [README](README.md#installation)). The "Connections" page only shows it to a browser open
  on the application's computer. If `VFE_HOST` is not a loopback address, `VFE_API_TOKEN`
  remains mandatory.
- **Wrong tokens**: constant-time comparison, a 0.5 s wait per failure, address blocked for one
  minute after 10 failures in 5 minutes.
- **Session**: `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS), 30 days, signed with
  the token (changing the token closes every session). With this cookie, every write requires
  an allowed `Origin` in addition to `X-VFE-Client`.
- **Allowlists** of hosts and origins extended only to the Tailscale address listened on and to
  the computer's MagicDNS names.
- Tailnet traffic is encrypted (WireGuard) and stays between the user's devices; never use
  `tailscale funnel` (publication on the internet).

## Reporting a problem

Report it privately, through the "Security" tab of the GitHub repository ("Report a
vulnerability"): do not open a public issue.
