# Connect the MCP and reach Video Frame Expedition from your other devices

**English** · [Français](connect-mcp.fr.md)

Video Frame Expedition for DaVinci Resolve contains an **MCP server**: an assistant (Claude
Code, Claude Desktop, Cursor, VS Code, Codex…) can consult your analysed videos there. It looks
for shots (place, light, weather, subjects), reads what is said, looks at frames and prepares an
edit in DaVinci Resolve. The server answers as long as the application is running (`run.bat`); it
never starts it.

The **Connections** page of the interface (left-hand menu) gives everything below **for your
computer**: addresses, token, and configurations ready to copy with the right paths.

## On the app's computer

MCP server address: `http://127.0.0.1:8765/mcp` (Streamable HTTP transport). No token is asked
for on this computer.

### Claude Code

```powershell
claude mcp add --scope user --transport http vfe-vision http://127.0.0.1:8765/mcp
```

### Claude Desktop

Claude Desktop's configuration file only launches **local commands** ("stdio" servers). Video
Frame Expedition therefore provides a bridge, `vfe mcp-stdio`, which relays the messages to the
application already running, without ever starting it: if it is not running, Claude Desktop
receives a clear error ("Video Frame Expedition ne répond pas… lancez l'application (run.bat)",
that is, "Video Frame Expedition is not responding… start the application (run.bat)").

File to edit:

- Microsoft Store installation (MSIX):
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
- installation from claude.ai: `%APPDATA%\Claude\claude_desktop_config.json`

Add the `vfe-vision` entry under `mcpServers`, **keeping the rest of the file** (it already
contains your preferences). The program is the application's Python, as an absolute path: Claude
Desktop then needs neither the `PATH` nor a particular working directory.

```json
{
  "mcpServers": {
    "vfe-vision": {
      "command": "C:\\…\\video-frame-expedition-resolve-windows\\backend\\.venv\\Scripts\\python.exe",
      "args": ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"]
    }
  }
}
```

The Connections page shows this block with the exact path. Then quit Claude Desktop
**completely** (icon near the clock, "Quit") and start it again. Its server log is in the `logs`
folder next to the configuration file (`mcp-server-vfe-vision.log`).

Claude Desktop's "custom connectors" (remote URL) go through Anthropic's servers: they cannot
reach a local address or a Tailscale address. Use the bridge above.

### Cursor

`%USERPROFILE%\.cursor\mcp.json` (or `.cursor/mcp.json` in a project):

```json
{ "mcpServers": { "vfe-vision": { "url": "http://127.0.0.1:8765/mcp" } } }
```

### VS Code

Command "MCP: Open User Configuration" (or `.vscode/mcp.json` in a project):

```json
{ "servers": { "vfe-vision": { "type": "http", "url": "http://127.0.0.1:8765/mcp" } } }
```

### OpenAI Codex CLI

`%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.vfe-vision]
url = 'http://127.0.0.1:8765/mcp'
```

A version of Codex that does not take HTTP servers goes through the stdio bridge:

```toml
[mcp_servers.vfe-vision]
command = 'C:\…\backend\.venv\Scripts\python.exe'
args = ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"]
```

### Other MCP client

- Streamable HTTP: `http://127.0.0.1:8765/mcp`.
- stdio: `"<the application's Python>" -m vfe_vision mcp-stdio --url http://127.0.0.1:8765/mcp`
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
No existing timeline is modified and the project is not saved (Ctrl+S in Resolve if you keep the
edit).
No other server needs to be plugged in.

**The MCP of DaVinci Resolve Studio 21** (`C:\Program Files\Blackmagic Design\DaVinci
Resolve\ResolveMCP.exe`, over stdio), for everything these four tools do not cover
(cross dissolves, effects): the assistant writes its scripts there, and Video Frame Expedition
gives it the shots (`find_clips`: paths, in and out points, timecodes). Plug both into the same
assistant:

```powershell
claude mcp add --scope user davinci-resolve -- "C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe"
```

For Claude Desktop, add under `mcpServers`:

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
Network**, and the Mac's firewall lets this PC in (port 1144). This PC keeps DaVinci Resolve
installed (without starting it): the application uses its scripting library to read the Mac's
Resolve.

## Access through Tailscale

[Tailscale](https://tailscale.com) links your devices in an encrypted private network (the
"tailnet"). Video Frame Expedition can listen on it so that your laptop, your tablet or your
phone open the interface and their assistants use the MCP.

### Turn it on

1. Create (or add to) the `.env` file at the root of the repository, next to `run.bat`:

   ```ini
   VFE_TAILSCALE=true
   ```

   For a single launch: `run.bat tailscale`. You can also give specific addresses:
   `VFE_EXTRA_HOSTS=100.64.12.34` (IP addresses only, separated by commas).

2. Allow the port in Windows Firewall, **once**, in a PowerShell opened **as administrator**
   (Windows classifies the tailnet as "Private"):

   ```powershell
   New-NetFirewallRule -DisplayName "Video Frame Expedition (Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Private -Action Allow
   ```

   Check the profile with `Get-NetConnectionProfile -InterfaceAlias Tailscale`: if it is
   "Public", replace `-Profile Private` with `-Profile Any`. If Windows once offered to allow
   Python and you refused, a **block** rule for `python.exe` may exist (it wins over any
   allow rule): delete it in "Windows Defender Firewall with Advanced Security" → "Inbound
   Rules".

3. Restart the application. The console shows the addresses reachable from your devices, for
   example `http://100.64.12.34:8765` and `http://<machine>.<tailnet>.ts.net:8765` (MagicDNS name).

The application then listens on `127.0.0.1` **and** on the computer's Tailscale address, each
with its own socket: never on `0.0.0.0`, so nothing is exposed on the local network (router,
Wi-Fi). If Tailscale is stopped at startup, the application starts anyway, on this computer
only, with a warning (console and Connections page); restart it once Tailscale is connected.

### The token

From another device, an **access token** is asked for. It is created at the first start with
remote access, in the data folder (`%LOCALAPPDATA%\vfe-vision\api-token.txt`, readable by your
Windows account only), unless `VFE_API_TOKEN` is set.

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
| "Video Frame Expedition ne répond pas à …" (the application is not responding at …) | the application is not running (`run.bat`) |
| The page does not open from the phone | firewall rule missing, Tailscale disconnected on one of the two devices, or application started before Tailscale |
| "Jeton d'API requis" (API token required) / 401 | token missing or changed (`vfe token`) |
| "Trop d'essais" (too many attempts) / 429 | 10 wrong tokens: wait one minute |
| 400 "Invalid host header" | address not recognised: use those of the Connections page |
| Claude Desktop does not see the tool | invalid JSON, or Claude Desktop not fully quit |

Security: [SECURITY.md](../../SECURITY.md).
