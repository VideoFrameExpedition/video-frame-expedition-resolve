/** Ready-to-copy configurations for MCP clients. Pure: tested on their own. */

export const SERVER_NAME = "vfe-vision";
export const RESOLVE_NAME = "davinci-resolve";

export interface StdioCommand {
  command: string;
  args: string[];
}

/** Where a client reaches the MCP server, and the token it must send (remote only). */
export interface Target {
  url: string;
  token?: string | undefined;
}

const json = (value: unknown): string => JSON.stringify(value, null, 2);

/** A command line argument, quoted when it needs to be: double quotes for Windows (cmd,
 * PowerShell), single quotes for the shells of macOS and Linux (`posix`). */
export function shellArg(value: string, posix = false): string {
  if (posix) {
    return /^[\w./:=@%+,-]+$/.test(value) ? value : `'${value.replace(/'/g, "'\\''")}'`;
  }
  return /[\s"&|<>^]/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value;
}

/** TOML string: a literal one (no escapes) unless it holds a single quote. */
function toml(value: string): string {
  return value.includes("'") ? JSON.stringify(value) : `'${value}'`;
}

export function mcpUrl(baseUrl: string, path = "/mcp"): string {
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

export function claudeCode({ url, token }: Target): string {
  const header = token ? ` --header "Authorization: Bearer ${token}"` : "";
  return `claude mcp add --scope user --transport http ${SERVER_NAME} ${url}${header}`;
}

/** Claude Desktop only launches commands: the ``vfe mcp-stdio`` bridge relays to the app. */
export function claudeDesktop(stdio: StdioCommand, { url, token }: Target): string {
  const server: Record<string, unknown> = {
    command: stdio.command,
    args: [...stdio.args, "--url", url],
  };
  if (token) {
    server.env = { VFE_MCP_TOKEN: token };
  }
  return json({ mcpServers: { [SERVER_NAME]: server } });
}

export function cursor({ url, token }: Target): string {
  const server: Record<string, unknown> = { url };
  if (token) {
    server.headers = { Authorization: `Bearer ${token}` };
  }
  return json({ mcpServers: { [SERVER_NAME]: server } });
}

/** VS Code asks for the token once and keeps it in its secret storage. */
export function vscode({ url, token }: Target): string {
  const server: Record<string, unknown> = { type: "http", url };
  if (!token) {
    return json({ servers: { [SERVER_NAME]: server } });
  }
  server.headers = { Authorization: "Bearer ${input:vfe-token}" };
  return json({
    inputs: [
      {
        type: "promptString",
        id: "vfe-token",
        description: "Jeton Video Frame Expedition",
        password: true,
      },
    ],
    servers: { [SERVER_NAME]: server },
  });
}

export const CODEX_TOKEN_VARIABLE = "VFE_VISION_TOKEN";

export function codex({ url, token }: Target): string {
  const lines = [`[mcp_servers.${SERVER_NAME}]`, `url = ${toml(url)}`];
  if (token) {
    lines.push(`bearer_token_env_var = "${CODEX_TOKEN_VARIABLE}"`);
  }
  return lines.join("\n");
}

export function codexStdio(stdio: StdioCommand, url: string): string {
  const args = [...stdio.args, "--url", url].map((arg) => JSON.stringify(arg)).join(", ");
  return [
    `[mcp_servers.${SERVER_NAME}]`,
    `command = ${toml(stdio.command)}`,
    `args = [${args}]`,
  ].join("\n");
}

/** The bridge as a single command line (any client that launches a stdio server). */
export function stdioCommandLine(
  stdio: StdioCommand,
  { url, token }: Target,
  posix = false,
): string {
  const parts = [stdio.command, ...stdio.args, "--url", url];
  if (token) {
    parts.push("--token", token);
  }
  return parts.map((part) => shellArg(part, posix)).join(" ");
}

export function resolveClaudeCode(path: string, posix = false): string {
  return `claude mcp add --scope user ${RESOLVE_NAME} -- ${shellArg(path, posix)}`;
}

export function resolveClaudeDesktop(path: string): string {
  return json({ mcpServers: { [RESOLVE_NAME]: { command: path } } });
}
