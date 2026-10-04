import { Link, Outlet, useLocation } from "@tanstack/react-router";
import {
  Activity,
  Cable,
  CircleHelp,
  Film,
  FlaskConical,
  ListChecks,
  MessageSquareText,
  Search,
  Settings2,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useLiveEvents } from "@/api/events";
import { useJobsSummary, useLmModels } from "@/api/queries";
import { Logo } from "@/components/Logo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { LogoutButton } from "@/features/access/AccessGate";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AnalyzePanel } from "@/features/library/AnalyzePanel";
import { BinsTree } from "@/features/library/BinsTree";
import { cn } from "@/lib/utils";

import { PreferencesMenu } from "./PreferencesMenu";

function NavItem({
  to,
  icon,
  label,
  disabled = false,
}: {
  to:
    "/library" | "/search" | "/ask" | "/jobs" | "/system" | "/bench" | "/settings" | "/connections";
  icon: ReactNode;
  label: string;
  disabled?: boolean;
}) {
  if (disabled) {
    return (
      <span className="text-muted-foreground/60 flex cursor-not-allowed items-center gap-3 rounded-lg px-3 py-2 text-sm">
        {icon}
        {label}
      </span>
    );
  }
  return (
    <Link
      to={to}
      className="text-muted-foreground hover:bg-secondary hover:text-foreground flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors"
      activeProps={{ className: "bg-secondary !text-foreground font-medium" }}
    >
      {icon}
      {label}
    </Link>
  );
}

/** « Help »: the presentation page, a file placed in `public/help/`, outside the router.
 * The server serves it as is, and the same folder can be hosted elsewhere unchanged. */
const HELP_PAGE = "/help/index.html";

function NavExternal({ href, icon, label }: { href: string; icon: ReactNode; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-muted-foreground hover:bg-secondary hover:text-foreground flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors"
    >
      {icon}
      {label}
    </a>
  );
}

function LmStudioStatus() {
  const { t } = useTranslation();
  const models = useLmModels();
  if (models.isError) {
    return (
      <Badge variant="secondary" className="bg-destructive/15 text-destructive border-0">
        {t("lmstudio.offline")}
      </Badge>
    );
  }
  const vision = models.data?.find((m) => m.vision && m.loaded_instances.length > 0);
  return (
    <Badge
      variant="secondary"
      className={cn(
        "max-w-64 truncate border-0",
        vision ? "bg-accent text-accent-foreground" : "bg-warning/15 text-warning",
      )}
      title={vision?.key}
    >
      <span className={cn("mr-1.5 size-2 rounded-full", vision ? "bg-brand-teal" : "bg-warning")} />
      {vision ? vision.display_name : t("lmstudio.noVision")}
    </Badge>
  );
}

function JobsIndicator() {
  const { t } = useTranslation();
  const summary = useJobsSummary();
  const running = summary.data?.running ?? 0;
  const queued = summary.data?.queued ?? 0;
  return (
    <Button variant="ghost" size="sm" asChild>
      <Link to="/jobs" className="gap-2">
        <Activity className={cn("size-4", running > 0 && "text-brand-teal animate-pulse")} />
        <span className="hidden sm:inline">
          {running > 0 ? t("jobs.running", { count: running }) : t("jobs.idle")}
        </span>
        {queued > 0 ? <Badge variant="secondary">+{queued}</Badge> : null}
      </Link>
    </Button>
  );
}

export function AppShell() {
  const { t } = useTranslation();
  const inLibrary = useLocation({
    // Paths match case-insensitively, with or without a trailing slash.
    select: (location) => location.pathname.replace(/\/+$/, "").toLowerCase() === "/library",
  });
  useLiveEvents();
  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex min-h-svh">
        <aside className="bg-card/40 sticky top-0 hidden h-svh w-60 shrink-0 flex-col gap-6 overflow-y-auto border-r px-4 py-5 md:flex">
          <Link to="/library" aria-label={t("app.fullName")}>
            <Logo className="h-14" />
          </Link>
          <nav className="flex flex-col gap-1" aria-label="Navigation">
            <NavItem to="/library" icon={<Film className="size-4" />} label={t("nav.library")} />
            <NavItem to="/search" icon={<Search className="size-4" />} label={t("nav.search")} />
            <NavItem
              to="/ask"
              icon={<MessageSquareText className="size-4" />}
              label={t("nav.ask")}
            />
            <NavItem to="/jobs" icon={<ListChecks className="size-4" />} label={t("nav.jobs")} />
            <NavItem to="/system" icon={<Settings2 className="size-4" />} label={t("nav.system")} />
            <NavItem
              to="/bench"
              icon={<FlaskConical className="size-4" />}
              label={t("nav.bench")}
            />
            <NavItem
              to="/connections"
              icon={<Cable className="size-4" />}
              label={t("connections.nav")}
            />
            <NavExternal
              href={HELP_PAGE}
              icon={<CircleHelp className="size-4" />}
              label={t("nav.help")}
            />
          </nav>
          {inLibrary ? (
            <>
              <BinsTree />
              <AnalyzePanel />
            </>
          ) : null}
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="bg-background/80 sticky top-0 z-20 flex h-14 items-center gap-3 border-b px-4 backdrop-blur md:px-6">
            <Link to="/library" className="md:hidden" aria-label={t("app.fullName")}>
              <Logo variant="compact" className="h-9" />
            </Link>
            <div className="ml-auto flex items-center gap-2">
              <LmStudioStatus />
              <JobsIndicator />
              <PreferencesMenu />
              <LogoutButton />
            </div>
          </header>
          <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-6 md:px-6">
            <Outlet />
          </main>
        </div>
      </div>
      <Toaster richColors closeButton />
    </TooltipProvider>
  );
}
