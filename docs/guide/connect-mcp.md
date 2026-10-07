# Connect the MCP and reach Video Frame Expedition from your other devices

**English** · [Français](connect-mcp.fr.md)

Video Frame Expedition for DaVinci Resolve contains an **MCP server**: an assistant (Claude
Code, Claude Desktop, Cursor, VS Code, Codex…) can consult your analysed videos there. It looks
for shots (place, light, weather, subjects), reads what is said, looks at frames and prepares an
edit in DaVinci Resolve. The server answers as long as the application is running (`run.bat` on
Windows, `run.command` on a Mac); it never starts it.

The **Connections** page of the interface (left-hand menu) gives everything below **for your
computer**: addresses, token, and configurations ready to copy with the right paths.

## On the app's computer

MCP server address: `http://127.0.0.1:8765/mcp` (Streamable HTTP transport). No token is asked
for on this computer.

### Claude Code

```powershell
claude mcp add --scope user --transport http vfe-vision http://127.0.0.1:8765/mcp
```

Or the Claude Code plugin, below. Use one or the other: with both, Claude Code keeps the entry
added by `claude mcp add` and leaves the plugin's aside.

### The Claude Code plugin

A **plugin** is a package you install into Claude Code. Video Frame Expedition's,
`video-frame-expedition`, has its own repository
([video-frame-expedition-claude-plugin](https://github.com/VideoFrameExpedition/video-frame-expedition-claude-plugin))
and holds two things:

- the **connection to the application's MCP server** (`vfe-vision`), the same as
  `claude mcp add`;
- a **skill**, `resolve-editing`: the editing method Claude reads before working in Resolve
  (tie the open timeline to the analyses, pick the shots, write an edit list, cut at safe
  points, edit into a new timeline, reframe for another aspect ratio, lay markers), with the
  known pitfalls of the DaVinci Resolve Studio 21.1 scripting API and tested script templates.

It needs Video Frame Expedition 1.1.1 or later, running. To edit: DaVinci Resolve Studio 21.1 or
later, with the "Resolve tools for the assistant" box (Connections page) or Resolve's own server
plugged into Claude Code (below). The skill's small scripts need Python 3, with Windows
PowerShell on Windows or `osascript` (part of macOS) on a Mac.

In Claude Code, install it, then confirm its settings:

```text
/plugin marketplace add VideoFrameExpedition/video-frame-expedition-claude-plugin
/plugin install video-frame-expedition@video-frame-expedition
/plugin configure video-frame-expedition@video-frame-expedition
```

The name appears twice in `video-frame-expedition@video-frame-expedition`: the plugin, then the
catalogue that offers it.

| Setting | Value |
|---|---|
| Application address | `http://127.0.0.1:8765/mcp` (default) when the application runs on this computer; otherwise its address on your network or Tailscale, for instance `http://100.64.12.34:8765/mcp` |
| Access token | Empty on the application's own computer. From another device: the token on the Connections page (kept in the system's password store) |

If you had already plugged the server in with `claude mcp add`, remove that entry so the
plugin's settings apply:

```powershell
claude mcp remove --scope user vfe-vision
```

Then ask in your own words: "Look at the timeline open in Resolve and tell me what each shot
holds", "Edit 45 seconds from the lake videos, with safe cuts", "Make a 9:16 version of this
timeline, framed on the people". Claude picks up the skill by itself when a request is about an
edit in Resolve; you can also call it with `/video-frame-expedition:resolve-editing`.

To update (then restart Claude Code) or remove the plugin, in a terminal:

```powershell
claude plugin marketplace update video-frame-expedition
claude plugin update video-frame-expedition@video-frame-expedition

claude plugin uninstall video-frame-expedition@video-frame-expedition
```

The plugin only contacts the application, at the address you set, and has no hook: nothing
starts on its own. The skill's scripts only run when Claude calls them for a task you asked for,
and download nothing. Like the application's tools, the skill never changes a timeline in place
(it duplicates it first) and does not save the project. The other assistants (Claude Desktop,
Cursor, VS Code, Codex) plug in as below, without the skill.

### Claude Desktop

Claude Desktop's configuration file only launches **local commands** ("stdio" servers). Video
Frame Expedition therefore provides a bridge, `vfe mcp-stdio`, which relays the messages to the
application already running, without ever starting it: if it is not running, Claude Desktop
receives a clear error ("Video Frame Expedition ne répond pas… lancez l'application (run.command
sur Mac, run.bat sous Windows)", that is, "Video Frame Expedition is not responding… start the
application").

File to edit:

- Windows, Microsoft Store installation (MSIX):
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
- Windows, installation from claude.ai: `%APPDATA%\Claude\claude_desktop_config.json`
- Mac: `~/Library/Application Support/Claude/claude_desktop_config.json` (in the Finder: Go ›
  Go to Folder, then paste this path without the file name)

Add the `vfe-vision` entry under `mcpServers`, **keeping the rest of the file** (it already
contains your preferences). The program is the application's Python, as an absolute path: Claude
Desktop then needs neither the `PATH` nor a particular working directory.

```json
{
  "mcpServers": {
    "vfe-vision": {
      "command": "C:\\…\\video-frame-expedition-resolve\\backend\\.venv\\Scripts\\python.exe",
      "args": ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"]
    }
  }
}
```

On a Mac, the application's Python is `…/backend/.venv/bin/python`. The Connections page shows
this block with the exact path. Then quit Claude Desktop **completely** (on Windows, icon near
the clock, "Quit"; on a Mac, Cmd+Q) and start it again. Its server log is in the `logs`
folder next to the configuration file (`mcp-server-vfe-vision.log`).

Claude Desktop's "custom connectors" (remote URL) go through Anthropic's servers: they cannot
reach a local address or a Tailscale address. Use the bridge above.

### Cursor

`%USERPROFILE%\.cursor\mcp.json` on Windows, `~/.cursor/mcp.json` on a Mac (or
`.cursor/mcp.json` in a project):

```json
{ "mcpServers": { "vfe-vision": { "url": "http://127.0.0.1:8765/mcp" } } }
```

### VS Code

Command "MCP: Open User Configuration" (or `.vscode/mcp.json` in a project):

```json
{ "servers": { "vfe-vision": { "type": "http", "url": "http://127.0.0.1:8765/mcp" } } }
```

### OpenAI Codex CLI

`%USERPROFILE%\.codex\config.toml` on Windows, `~/.codex/config.toml` on a Mac:

```toml
[mcp_servers.vfe-vision]
url = 'http://127.0.0.1:8765/mcp'
```

A version of Codex that does not take HTTP servers goes through the stdio bridge:

```toml
[mcp_servers.vfe-vision]
command = 'C:\…\backend\.venv\Scripts\python.exe'  # on a Mac: '/Users/me/…/backend/.venv/bin/python'
args = ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"]
```

### Other MCP client

- Streamable HTTP: `http://127.0.0.1:8765/mcp`.
- stdio: `"<the application's Python>" -m vfe_vision mcp-stdio --url http://127.0.0.1:8765/mcp`
  (on a Mac, in single quotes)
  (options: `--token`, or the `VFE_MCP_TOKEN` variable, for an application on another
  device). Standard output carries only the protocol; the log goes to standard error.

### DaVinci Resolve

Two ways to let the assistant act in Resolve; they can be combined.

**The application's Resolve tools.** On the **Connections › Resolve tools for the assistant**
page, tick "Enhanced DaVinci Resolve tools for the assistant" (off by default). Four MCP tools of
Video Frame Expedition then drive Resolve through the application's fixed scripts:
`read_timeline` reads the open timeline, `plan_reframe` prepares a reframing,
`build_timeline` builds a new timeline "… - vfe vN", `apply_markers` places the
markers. The assistant sends only data, never code, and receives short results: it uses far
fewer tokens than when it writes and rereads its own scripts.
No existing timeline is modified and the project is not saved (Ctrl+S, or Cmd+S on a Mac, in
Resolve if you keep the edit).
No other server needs to be plugged in.

**The MCP of DaVinci Resolve Studio 21** (`ResolveMCP`, over stdio: on Windows,
`C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe`; on a Mac, in
`/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/`; the Connections page
shows where it is), for everything these four tools do not cover
(cross dissolves, effects): the assistant writes its scripts there, and Video Frame Expedition
gives it the shots (`find_clips`: paths, in and out points, timecodes). Plug both into the same
assistant:

```powershell
claude mcp add --scope user davinci-resolve -- "C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe"
```

On a Mac:

```sh
claude mcp add --scope user davinci-resolve -- '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP'
```

For Claude Desktop, add under `mcpServers` (on a Mac, Resolve also provides the
`DaVinciResolve.mcpb` extension, which a double-click installs in Claude Desktop):

```json
"davinci-resolve": {
  "command": "C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\ResolveMCP.exe"
}
```

### DaVinci Resolve on another computer (a Mac, for example)

The **application's Resolve tools** work as they are: they go through this PC, which reads and
drives the Mac's Resolve (settings below). Claude can then stay on this PC.

The **Resolve MCP**, for its part, only drives the Resolve of its own machine: to use it,
Claude (Claude Code or Claude Desktop) is installed **on Resolve's computer**, and both MCPs are
plugged in there:

- Resolve's, locally (on a Mac, the `DaVinciResolve.mcpb` extension supplied with Resolve, or
  its `ResolveMCP` program);
- Video Frame Expedition's, through Tailscale with the token (see "Assistants on your other
  devices" below).

In Video Frame Expedition, on the **Connections › Where DaVinci Resolve runs** page: choose "on
another computer", give its name or its Tailscale address, then **the same folders seen from
both sides** (for example `D:\cats 2026` here and `/Volumes/cats 2026` on the Mac, if the Mac
reads the rushes through a network share of this PC). "Test the connection" reads the project
open in Resolve.

On the Mac, in Resolve Studio: **Preferences › System › General › External scripting using:
Network**, and the Mac's firewall lets this PC in: Resolve's scripting server (port 1144) **and**
DaVinci Resolve itself, which picks another port each time it starts. If Resolve's computer is a
Windows PC, the firewall rules Resolve installs only hold for a "private" network: on a network
classed as "public", allow `fuscript.exe` and `Resolve.exe` (DaVinci Resolve's folder) for that
network. This PC keeps DaVinci Resolve installed (without starting it): the application uses its
scripting library to read the Mac's Resolve.

The reverse works the same way: the application on a Mac reads and drives the Resolve of a
Windows PC or of another Mac. The folders are then given from the Mac (for example
`/Volumes/cats 2026` here and `D:\cats 2026` on the PC), and the Mac keeps DaVinci Resolve
installed for its scripting library (`fusionscript.so`).

For the subtitles, the share of the rushes must be **read and write**: the application writes
the timeline's tracks next to its first video (`<timeline>_TIMELINE_EN.srt`), where Resolve reads
them to lay them, and each video's subtitles next to it. On a read-only share, the timeline
arrives without its subtitle tracks, and the application says so.

## Access through Tailscale

[Tailscale](https://tailscale.com) links your devices in an encrypted private network (the
"tailnet"). Video Frame Expedition can listen on it so that your laptop, your tablet or your
phone open the interface and their assistants use the MCP.

### Turn it on

1. Create (or add to) the `.env` file at the root of the repository, next to `run.bat` (or
   `run.command`):

   ```ini
   VFE_TAILSCALE=true
   ```

   For a single launch: `run.bat tailscale` (or `run.command tailscale`). You can also give
   specific addresses:
   `VFE_EXTRA_HOSTS=100.64.12.34` (IP addresses only, separated by commas).

2. **On Windows**, allow the port in the firewall, **once**, in a PowerShell opened **as
   administrator** (Windows classifies the tailnet as "Private"):

   ```powershell
   New-NetFirewallRule -DisplayName "Video Frame Expedition (Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Private -Action Allow
   ```

   Check the profile with `Get-NetConnectionProfile -InterfaceAlias Tailscale`: if it is
   "Public", replace `-Profile Private` with `-Profile Any`. If Windows once offered to allow
   Python and you refused, a **block** rule for `python.exe` may exist (it wins over any
   allow rule): delete it in "Windows Defender Firewall with Advanced Security" → "Inbound
   Rules".

   **On a Mac**, if macOS's firewall is on (System Settings › Network › Firewall), it asks
   **once**, when the application starts listening, whether Python may accept incoming
   connections: click "Allow". With "Block all incoming connections", your other devices do not
   reach the application. If you once refused, change the answer in the firewall's Options, in
   the list of applications.

3. Restart the application. The console shows the addresses reachable from your devices, for
   example `http://100.64.12.34:8765` and `http://<machine>.<tailnet>.ts.net:8765` (MagicDNS name).

The application then listens on `127.0.0.1` **and** on the computer's Tailscale address, each
with its own socket: never on `0.0.0.0`, so nothing is exposed on the local network (router,
Wi-Fi). If Tailscale is stopped at startup, the application starts anyway, on this computer
only, with a warning (console and Connections page); restart it once Tailscale is connected.

### The token

From another device, an **access token** is asked for. It is created at the first start with
remote access, in the data folder (`%LOCALAPPDATA%\vfe-vision\api-token.txt` on Windows,
`~/Library/Application Support/vfe-vision/api-token.txt` on a Mac), readable by your account
only, unless `VFE_API_TOKEN` is set.

- `vfe token` prints it (the token alone on standard output); in full, from the
  application's folder: `uv run --frozen --no-dev --project backend python -m vfe_vision token`;
- `vfe token --rotate` creates a new one: restart the application, then reconnect your
  devices (all sessions are dropped);
- the Connections page shows it (hidden, "Show" button) **only** when it is open on the app's
  computer.

In a remote browser, a sign-in page asks for the token, then opens a 30-day session (`HttpOnly`,
`SameSite=Strict` cookie); "Sign out" is at the top right. After 10 wrong tokens in 5 minutes,
the device waits one minute.

### Assistants on your other devices

Replace `127.0.0.1` with the Tailscale address and add the token:

```powershell
claude mcp add --scope user --transport http vfe-vision http://100.64.12.34:8765/mcp --header "Authorization: Bearer <TOKEN>"
```

- Cursor: `"url": "http://100.64.12.34:8765/mcp", "headers": { "Authorization": "Bearer <TOKEN>" }`;
- VS Code: `"headers": { "Authorization": "Bearer ${input:vfe-token}" }` with an `inputs` entry
  of type `promptString` (`"password": true`): VS Code asks for the token once;
- Codex: `url = 'http://100.64.12.34:8765/mcp'` and `bearer_token_env_var = "VFE_VISION_TOKEN"`
  (environment variable holding the token);
- Claude Desktop on another computer: a stdio-to-HTTP bridge, for example `vfe mcp-stdio
  --url http://100.64.12.34:8765/mcp` with the `VFE_MCP_TOKEN` variable (the code of Video
  Frame Expedition is then needed on that computer), or any bridge able to send an
  `Authorization` header.

From another device, the MCP only accepts the `Authorization: Bearer` header (not the browser
cookie). On the app's computer, nothing changes: Claude Code stays registered on
`http://127.0.0.1:8765/mcp`.

### HTTPS with `tailscale serve` (optional)

`tailscale serve --bg 8765` publishes the application over HTTPS at
`https://<machine>.<tailnet>.ts.net`, in the tailnet only (certificate provided by
Tailscale), relaying the requests to `127.0.0.1:8765`. Video Frame Expedition treats as local
only a request that arrived on `127.0.0.1` **with** the host `127.0.0.1` or `localhost`: a
relayed request that carries the machine's name therefore asks for the token, like direct access
(keep `VFE_TAILSCALE=true` so that this name is recognised). **Never** use `tailscale funnel`,
which would publish the application on the internet.

### Privacy

The tailnet is private: traffic goes directly from one of your devices to another, encrypted end
to end by WireGuard, and your videos, frames and analyses never leave your devices. Only the devices of your tailnet can reach the address, and they must also present
the token. Add only trusted devices to the tailnet, and no "machine sharing" with other
accounts.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Video Frame Expedition ne répond pas à …" (the application is not responding at …) | the application is not running (`run.bat`, `run.command`) |
| The page does not open from the phone | firewall rule missing, Tailscale disconnected on one of the two devices, or application started before Tailscale |
| "Jeton d'API requis" (API token required) / 401 | token missing or changed (`vfe token`) |
| "Trop d'essais" (too many attempts) / 429 | 10 wrong tokens: wait one minute |
| 400 "Invalid host header" | address not recognised: use those of the Connections page |
| Claude Desktop does not see the tool | invalid JSON, or Claude Desktop not fully quit |
| The Claude Code plugin does not take its address or token | a `vfe-vision` entry added by `claude mcp add` wins: `claude mcp remove --scope user vfe-vision` |

Security: [SECURITY.md](../../SECURITY.md).
