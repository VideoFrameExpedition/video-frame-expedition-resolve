import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Connections } from "@/api/access";

import { ConnectionsPage } from "./ConnectionsPage";

let data: Connections | undefined;
vi.mock("./ResolveLinkCard", () => ({ ResolveLinkCard: () => null }));
vi.mock("./ResolveToolsCard", () => ({ ResolveToolsCard: () => null }));
vi.mock("@/api/access", () => ({
  useConnections: () => ({ data, isPending: data === undefined, isError: false, error: null }),
}));

const TOKEN = "jeton-secret-42";
const PYTHON = "/Users/me/vfe/backend/.venv/bin/python";
const RESOLVE_MCP =
  "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP";

function connections(overrides: Partial<Connections> = {}): Connections {
  return {
    viewer_local: true,
    platform: "macos",
    port: 8765,
    local_url: "http://127.0.0.1:8765",
    mcp_path: "/mcp",
    remote: {
      requested: true,
      enabled: true,
      urls: ["http://100.64.12.34:8765", "http://machine.tail1234.ts.net:8765"],
      notices: [],
    },
    token: {
      available: true,
      source: "file",
      value: TOKEN,
      path: "/Users/me/Library/Application Support/vfe-vision/api-token.txt",
    },
    stdio: { command: PYTHON, args: ["-m", "vfe_vision", "mcp-stdio"] },
    claude_desktop: [
      {
        kind: "classic",
        path: "/Users/me/Library/Application Support/Claude/claude_desktop_config.json",
        installed: true,
        exists: true,
      },
    ],
    resolve_mcp: { path: RESOLVE_MCP, installed: true },
    ...overrides,
  };
}

const codeBlocks = (): string[] =>
  Array.from(document.querySelectorAll("pre"), (block) => block.textContent);

describe("ConnectionsPage", () => {
  it("shows the addresses and, on this computer, the token behind a reveal button", async () => {
    data = connections();
    const user = userEvent.setup();
    render(<ConnectionsPage />);
    expect(screen.getByText("http://127.0.0.1:8765/mcp")).toBeVisible();
    expect(screen.getByText("http://100.64.12.34:8765/mcp")).toBeVisible();
    const field = screen.getByLabelText("Jeton d'accès");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveValue(TOKEN);
    expect(screen.getByText(/vfe token --rotate/)).toBeVisible();

    const blocks = codeBlocks();
    expect(blocks).toContain(
      "claude mcp add --scope user --transport http vfe-vision http://127.0.0.1:8765/mcp",
    );
    expect(blocks.join("\n")).toContain('--header "Authorization: Bearer <JETON>"');
    expect(blocks.join("\n")).not.toContain(TOKEN);

    await user.click(screen.getByRole("button", { name: "Afficher" }));
    expect(field).toHaveAttribute("type", "text");
    expect(codeBlocks().join("\n")).toContain(`Authorization: Bearer ${TOKEN}`);
  });

  it("gives Claude Desktop the stdio bridge and where its file is", async () => {
    data = connections();
    const user = userEvent.setup();
    render(<ConnectionsPage />);
    await user.click(screen.getByRole("tab", { name: "Claude Desktop" }));
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).getByText(/Application Support\/Claude\//)).toBeVisible();
    expect(within(panel).getAllByText("installé")).toHaveLength(1);
    const config = JSON.parse(panel.querySelector("pre")?.textContent ?? "") as {
      mcpServers: Record<string, { command: string; args: string[] }>;
    };
    expect(config.mcpServers["vfe-vision"]).toEqual({
      command: PYTHON,
      args: ["-m", "vfe_vision", "mcp-stdio", "--url", "http://127.0.0.1:8765/mcp"],
    });
    // A Mac: the shell's single quotes around a path with spaces.
    expect(
      screen.getByText(`claude mcp add --scope user davinci-resolve -- '${RESOLVE_MCP}'`),
    ).toBeVisible();
  });

  it("quotes the command lines as a Windows PC does, and finds Claude Desktop's Store install", async () => {
    const windowsPython = String.raw`C:\vfe\backend\.venv\Scripts\python.exe`;
    data = connections({
      platform: "windows",
      stdio: { command: windowsPython, args: ["-m", "vfe_vision", "mcp-stdio"] },
      claude_desktop: [
        {
          kind: "classic",
          path: String.raw`C:\Users\me\AppData\Roaming\Claude\claude_desktop_config.json`,
          installed: false,
          exists: false,
        },
        {
          kind: "store",
          path: String.raw`C:\Users\me\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`,
          installed: true,
          exists: true,
        },
      ],
      resolve_mcp: {
        path: String.raw`C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe`,
        installed: true,
      },
    });
    const user = userEvent.setup();
    render(<ConnectionsPage />);
    expect(screen.getByText(/davinci-resolve -- "C:\\Program Files/)).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Claude Desktop" }));
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).getByText(/Claude_pzs8sxrjxfjjc/)).toBeVisible();
    expect(within(panel).getAllByText("installé")).toHaveLength(1);
  });

  it("never shows the token to another device", () => {
    data = connections({
      viewer_local: false,
      token: { available: true, source: "file", value: null, path: null },
    });
    render(<ConnectionsPage />);
    expect(
      screen.getByText("Le jeton ne s'affiche que sur l'ordinateur de l'application."),
    ).toBeVisible();
    expect(screen.queryByLabelText("Jeton d'accès")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Afficher" })).not.toBeInTheDocument();
    expect(codeBlocks().join("\n")).toContain("Bearer <JETON>");
  });

  it("explains how to turn remote access on, and why it is off", () => {
    data = connections({
      remote: {
        requested: true,
        enabled: false,
        urls: [],
        notices: ["Tailscale n'est pas connecté (état : Stopped) : accès coupé."],
      },
    });
    render(<ConnectionsPage />);
    expect(screen.getByText(/VFE_TAILSCALE=true/)).toBeVisible();
    expect(screen.getByText(/état : Stopped/)).toBeVisible();
    expect(codeBlocks().join("\n")).not.toContain("100.64.12.34");
  });
});
