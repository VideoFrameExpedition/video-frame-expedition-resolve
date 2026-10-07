import {
  claudeCode,
  claudeDesktop,
  codex,
  codexStdio,
  cursor,
  mcpUrl,
  resolveClaudeCode,
  resolveClaudeDesktop,
  shellArg,
  stdioCommandLine,
  vscode,
} from "./snippets";

const LOCAL = { url: "http://127.0.0.1:8765/mcp" };
const REMOTE = { url: "http://100.64.12.34:8765/mcp", token: "abc123" };
const PYTHON = String.raw`C:\Users\me\vfe vision\backend\.venv\Scripts\python.exe`;
const STDIO = { command: PYTHON, args: ["-m", "vfe_vision", "mcp-stdio"] };

describe("MCP client snippets", () => {
  it("builds the Claude Code commands", () => {
    expect(claudeCode(LOCAL)).toBe(
      "claude mcp add --scope user --transport http vfe-vision http://127.0.0.1:8765/mcp",
    );
    expect(claudeCode(REMOTE)).toBe(
      "claude mcp add --scope user --transport http vfe-vision http://100.64.12.34:8765/mcp" +
        ' --header "Authorization: Bearer abc123"',
    );
  });

  it("launches the stdio bridge for Claude Desktop, spaces in paths kept whole", () => {
    const config = JSON.parse(claudeDesktop(STDIO, LOCAL)) as {
      mcpServers: Record<string, { command: string; args: string[]; env?: unknown }>;
    };
    expect(config.mcpServers["vfe-vision"]).toEqual({
      command: PYTHON,
      args: ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"],
    });
    const remote = JSON.parse(claudeDesktop(STDIO, REMOTE)) as typeof config;
    expect(remote.mcpServers["vfe-vision"]?.env).toEqual({ VFE_MCP_TOKEN: "abc123" });
  });

  it("writes Cursor, VS Code and Codex configurations", () => {
    expect(JSON.parse(cursor(REMOTE))).toEqual({
      mcpServers: {
        "vfe-vision": { url: REMOTE.url, headers: { Authorization: "Bearer abc123" } },
      },
    });
    expect(JSON.parse(vscode(LOCAL))).toEqual({
      servers: { "vfe-vision": { type: "http", url: LOCAL.url } },
    });
    const remote = JSON.parse(vscode(REMOTE)) as {
      inputs: { id: string; password: boolean }[];
      servers: Record<string, { headers: Record<string, string> }>;
    };
    expect(remote.inputs[0]).toMatchObject({ id: "vfe-token", password: true });
    expect(remote.servers["vfe-vision"]?.headers.Authorization).toBe("Bearer ${input:vfe-token}");
    expect(codex(LOCAL)).toBe("[mcp_servers.vfe-vision]\nurl = 'http://127.0.0.1:8765/mcp'");
    expect(codex(REMOTE)).toContain('bearer_token_env_var = "VFE_VISION_TOKEN"');
    expect(codexStdio(STDIO, LOCAL.url)).toBe(
      "[mcp_servers.vfe-vision]\n" +
        `command = '${PYTHON}'\n` +
        'args = ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"]',
    );
  });

  it("quotes command lines for Windows", () => {
    expect(shellArg("plain")).toBe("plain");
    expect(shellArg("C:\\Program Files\\x.exe")).toBe('"C:\\Program Files\\x.exe"');
    expect(stdioCommandLine(STDIO, REMOTE)).toBe(
      `"${PYTHON}" -m vfe_vision mcp-stdio --url http://100.64.12.34:8765/mcp --token abc123`,
    );
    const resolve = "C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\ResolveMCP.exe";
    expect(resolveClaudeCode(resolve)).toBe(
      `claude mcp add --scope user davinci-resolve -- "${resolve}"`,
    );
    expect(JSON.parse(resolveClaudeDesktop(resolve))).toEqual({
      mcpServers: { "davinci-resolve": { command: resolve } },
    });
    expect(mcpUrl("http://127.0.0.1:8765/")).toBe("http://127.0.0.1:8765/mcp");
  });

  it("quotes command lines for the shells of macOS and Linux", () => {
    const python = "/Users/me/vfe vision/backend/.venv/bin/python";
    const stdio = { command: python, args: ["-m", "vfe_vision", "mcp-stdio"] };
    expect(shellArg("plain", true)).toBe("plain");
    expect(shellArg("http://127.0.0.1:8765/mcp", true)).toBe("http://127.0.0.1:8765/mcp");
    expect(shellArg(python, true)).toBe(`'${python}'`);
    expect(shellArg("l'été", true)).toBe(`'l'\\''été'`);
    expect(stdioCommandLine(stdio, REMOTE, true)).toBe(
      `'${python}' -m vfe_vision mcp-stdio --url ${REMOTE.url} --token ${REMOTE.token}`,
    );
    const resolve =
      "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP";
    expect(resolveClaudeCode(resolve, true)).toBe(
      `claude mcp add --scope user davinci-resolve -- '${resolve}'`,
    );
  });
});
