import { Eye, EyeOff, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useConnections, type Connections } from "@/api/access";
import { errorMessage } from "@/api/client";
import { PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { CodeBlock, CopyButton } from "./CopyButton";
import { ResolveLinkCard } from "./ResolveLinkCard";
import { ResolveToolsCard } from "./ResolveToolsCard";
import {
  CODEX_TOKEN_VARIABLE,
  claudeCode,
  claudeDesktop,
  codex,
  codexStdio,
  cursor,
  mcpUrl,
  resolveClaudeCode,
  resolveClaudeDesktop,
  stdioCommandLine,
  vscode,
  type Target,
} from "./snippets";

function UrlRow({ label, url }: { label: string; url: string }) {
  const { t } = useTranslation();
  return (
    <li className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-muted-foreground min-w-28 text-sm">{label}</span>
      <code className="min-w-0 flex-1 truncate font-mono text-sm">{url}</code>
      <CopyButton text={url} label={t("connections.copyThis", { what: url })} />
    </li>
  );
}

function Addresses({ data }: { data: Connections }) {
  const { t } = useTranslation();
  const remote = data.remote;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("connections.addresses")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-5">
        <section className="grid gap-2">
          <h3 className="text-sm font-medium">{t("connections.here")}</h3>
          <ul className="grid gap-2">
            <UrlRow label={t("connections.interface")} url={data.local_url} />
            <UrlRow label={t("connections.mcp")} url={mcpUrl(data.local_url, data.mcp_path)} />
          </ul>
        </section>
        <section className="grid gap-2">
          <h3 className="text-sm font-medium">{t("connections.remote")}</h3>
          {remote.enabled ? (
            <ul className="grid gap-2">
              {remote.urls.map((url) => (
                <UrlRow key={url} label={t("connections.interface")} url={url} />
              ))}
              {remote.urls.map((url) => (
                <UrlRow
                  key={`${url}/mcp`}
                  label={t("connections.mcp")}
                  url={mcpUrl(url, data.mcp_path)}
                />
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground text-sm">{t("connections.remoteOff")}</p>
          )}
          {remote.notices.map((notice) => (
            <p key={notice} className="text-warning flex items-start gap-2 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              {notice}
            </p>
          ))}
        </section>
      </CardContent>
    </Card>
  );
}

function TokenCard({
  data,
  revealed,
  onReveal,
}: {
  data: Connections;
  revealed: boolean;
  onReveal: (next: boolean) => void;
}) {
  const { t } = useTranslation();
  const token = data.token;
  let body: ReactNode;
  if (!token.available) {
    body = <p className="text-muted-foreground text-sm">{t("connections.tokenNone")}</p>;
  } else if (token.value === null) {
    body = <p className="text-muted-foreground text-sm">{t("connections.tokenHidden")}</p>;
  } else {
    body = (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            readOnly
            aria-label={t("connections.token")}
            type={revealed ? "text" : "password"}
            value={token.value}
            className="min-w-0 flex-1 font-mono"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-pressed={revealed}
            onClick={() => {
              onReveal(!revealed);
            }}
          >
            {revealed ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
            {revealed ? t("connections.hide") : t("connections.show")}
          </Button>
          <CopyButton text={token.value} label={t("connections.copyToken")} />
        </div>
        <p className="text-muted-foreground text-xs">
          {token.source === "env"
            ? t("connections.tokenEnv")
            : t("connections.tokenFile", { path: token.path ?? "" })}{" "}
          {t("connections.tokenRotate")}
        </p>
      </>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("connections.token")}</CardTitle>
        <CardDescription>{t("connections.tokenHelp")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2">{body}</CardContent>
    </Card>
  );
}

/** One client: how to add the server on this computer, then from another device. */
function Variants({
  name,
  local,
  remote,
  remoteUrl,
  remoteNote,
}: {
  name: string;
  local: string;
  remote: string | null;
  remoteUrl: string | undefined;
  remoteNote?: string | undefined;
}) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-3">
      <h4 className="text-sm font-medium">{t("connections.onThisComputer")}</h4>
      <CodeBlock code={local} label={t("connections.copyThis", { what: name })} />
      {remote !== null && remoteUrl ? (
        <>
          <h4 className="text-sm font-medium">{t("connections.fromDevice", { url: remoteUrl })}</h4>
          <CodeBlock
            code={remote}
            label={t("connections.copyThis", { what: `${name} (${remoteUrl})` })}
          />
          {remoteNote ? <p className="text-muted-foreground text-xs">{remoteNote}</p> : null}
        </>
      ) : null}
    </div>
  );
}

function Clients({ data, token, masked }: { data: Connections; token: string; masked: boolean }) {
  const { t } = useTranslation();
  const local: Target = { url: mcpUrl(data.local_url, data.mcp_path) };
  const base = data.remote.enabled ? data.remote.urls[0] : undefined;
  const remote: Target | null = base ? { url: mcpUrl(base, data.mcp_path), token } : null;
  const placeholder = masked ? t("connections.placeholder") : undefined;
  const posix = data.platform !== "windows"; // how the app's computer quotes a command line
  const tabs: { id: string; label: string; content: ReactNode }[] = [
    {
      id: "claude-code",
      label: "Claude Code",
      content: (
        <>
          <p className="text-sm">{t("connections.claudeCode.help")}</p>
          <Variants
            name="Claude Code"
            local={claudeCode(local)}
            remote={remote && claudeCode(remote)}
            remoteUrl={remote?.url}
            remoteNote={placeholder}
          />
        </>
      ),
    },
    {
      id: "claude-desktop",
      label: "Claude Desktop",
      content: (
        <>
          <p className="text-sm">{t("connections.claudeDesktop.help")}</p>
          <ul className="grid gap-1 text-sm">
            {data.claude_desktop.map((file) => (
              <li key={file.kind} className="flex flex-wrap items-center gap-2">
                <span className="text-muted-foreground">
                  {t(`connections.claudeDesktop.${file.kind}`)}
                </span>
                <code className="min-w-0 font-mono text-xs break-all">{file.path}</code>
                {file.installed ? (
                  <Badge variant="secondary">{t("connections.claudeDesktop.installed")}</Badge>
                ) : null}
                {file.exists ? (
                  <Badge variant="secondary">{t("connections.claudeDesktop.exists")}</Badge>
                ) : null}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground text-xs">{t("connections.claudeDesktop.merge")}</p>
          <Variants
            name="Claude Desktop"
            local={claudeDesktop(data.stdio, local)}
            remote={null}
            remoteUrl={undefined}
          />
        </>
      ),
    },
    {
      id: "cursor",
      label: "Cursor",
      content: (
        <>
          <p className="text-sm">{t("connections.cursor.help")}</p>
          <Variants
            name="Cursor"
            local={cursor(local)}
            remote={remote && cursor(remote)}
            remoteUrl={remote?.url}
            remoteNote={placeholder}
          />
        </>
      ),
    },
    {
      id: "vscode",
      label: "VS Code",
      content: (
        <>
          <p className="text-sm">{t("connections.vscode.help")}</p>
          <Variants
            name="VS Code"
            local={vscode(local)}
            remote={remote && vscode(remote)}
            remoteUrl={remote?.url}
            remoteNote={t("connections.vscode.remoteHelp")}
          />
        </>
      ),
    },
    {
      id: "codex",
      label: "Codex",
      content: (
        <>
          <p className="text-sm">{t("connections.codex.help")}</p>
          <Variants
            name="Codex"
            local={codex(local)}
            remote={remote && codex(remote)}
            remoteUrl={remote?.url}
            remoteNote={t("connections.codex.remoteHelp", { variable: CODEX_TOKEN_VARIABLE })}
          />
          <p className="text-muted-foreground text-xs">{t("connections.codex.stdioHelp")}</p>
          <CodeBlock
            code={codexStdio(data.stdio, local.url)}
            label={t("connections.copyThis", { what: "Codex (stdio)" })}
          />
        </>
      ),
    },
    {
      id: "other",
      label: t("connections.other.tab"),
      content: (
        <>
          <p className="text-sm">{t("connections.other.http")}</p>
          <CodeBlock code={local.url} label={t("connections.copyThis", { what: local.url })} />
          {remote ? (
            <>
              <p className="text-sm">{t("connections.other.httpRemote")}</p>
              <CodeBlock
                code={`${remote.url}\nAuthorization: Bearer ${token}`}
                label={t("connections.copyThis", { what: remote.url })}
              />
            </>
          ) : null}
          <p className="text-sm">{t("connections.other.stdio")}</p>
          <CodeBlock
            code={stdioCommandLine(data.stdio, local, posix)}
            label={t("connections.copyThis", { what: "stdio" })}
          />
          <p className="text-muted-foreground text-xs">{t("connections.other.stdioHelp")}</p>
        </>
      ),
    },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("connections.clients")}</CardTitle>
        <CardDescription>{t("connections.clientsHelp")}</CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="claude-code">
          <TabsList variant="line" className="flex-wrap">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
          {tabs.map((tab) => (
            <TabsContent key={tab.id} value={tab.id} className="grid gap-3 pt-3">
              {tab.content}
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
    </Card>
  );
}

function Resolve({ data }: { data: Connections }) {
  const { t } = useTranslation();
  const path = data.resolve_mcp.path;
  const posix = data.platform !== "windows";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("connections.resolve.title")}</CardTitle>
        <CardDescription>{t("connections.resolve.help")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <code className="font-mono text-xs break-all">{path}</code>
          {data.resolve_mcp.installed ? (
            <Badge variant="secondary">{t("connections.resolve.installed")}</Badge>
          ) : (
            <span className="text-warning text-xs">{t("connections.resolve.missing")}</span>
          )}
        </p>
        <h4 className="text-sm font-medium">Claude Code</h4>
        <CodeBlock
          code={resolveClaudeCode(path, posix)}
          label={t("connections.copyThis", { what: "DaVinci Resolve (Claude Code)" })}
        />
        <h4 className="text-sm font-medium">Claude Desktop</h4>
        <CodeBlock
          code={resolveClaudeDesktop(path)}
          label={t("connections.copyThis", { what: "DaVinci Resolve (Claude Desktop)" })}
        />
      </CardContent>
    </Card>
  );
}

/** « Connections »: what the MCP server is for, its addresses, the token, and the
 * configuration of each MCP client, ready to copy. */
export function ConnectionsPage() {
  const { t } = useTranslation();
  const connections = useConnections();
  const [revealed, setRevealed] = useState(false);
  const data = connections.data;
  const shown = revealed && data?.token.value ? data.token.value : undefined;
  const token = shown ?? t("connections.tokenPlaceholder");
  return (
    <div className="grid max-w-4xl gap-6">
      <PageHeader title={t("connections.title")} subtitle={t("connections.subtitle")} />
      <p className="text-sm leading-relaxed">{t("connections.intro")}</p>
      {connections.isError ? (
        <p className="text-destructive text-sm">{errorMessage(connections.error)}</p>
      ) : null}
      {data ? (
        <>
          <Addresses data={data} />
          <TokenCard data={data} revealed={revealed} onReveal={setRevealed} />
          <Clients data={data} token={token} masked={shown === undefined} />
          <Resolve data={data} />
          <ResolveLinkCard />
          <ResolveToolsCard />
          <p className="text-muted-foreground text-xs">{t("connections.guide")}</p>
        </>
      ) : connections.isPending ? (
        <Skeleton className="h-96 rounded-xl" />
      ) : null}
    </div>
  );
}
