import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";

import type { VideoStatus } from "@/api/client";
import { AskPage } from "@/features/ask/AskPage";
import { ConnectionsPage } from "@/features/connections/ConnectionsPage";
import { validateAskPage } from "@/features/ask/askState";
import { BenchPage } from "@/features/bench/BenchPage";
import { JobsPage } from "@/features/jobs/JobsPage";
import { LibraryPage } from "@/features/library/LibraryPage";
import { SearchPage } from "@/features/search/SearchPage";
import { validateSearchPage } from "@/features/search/searchState";
import { SystemPage } from "@/features/system/SystemPage";
import { VideoPage } from "@/features/video/VideoPage";

import { AppShell } from "./AppShell";

export interface RouterContext {
  queryClient: QueryClient;
}

const SORTS = ["recent", "name", "duration", "captured"] as const;
export type LibrarySort = (typeof SORTS)[number];
export const LIGHTS = [
  "golden_hour",
  "blue_hour",
  "day",
  "nautical_twilight",
  "astronomical_twilight",
  "night",
] as const;
export type LibraryLight = (typeof LIGHTS)[number];
const STATUSES: VideoStatus[] = [
  "new",
  "queued",
  "analyzing",
  "ready",
  "partial",
  "failed",
  "offline",
];

export interface LibrarySearch {
  q?: string;
  status?: VideoStatus;
  sort?: LibrarySort;
  light?: LibraryLight;
  root?: string; // a bin: a root, and a folder of it ("" for the root itself)
  folder?: string;
  timeline?: string; // or a Resolve timeline of the library, never with a root
}

/** The library's filters and bin from the URL: unknown values are dropped. A timeline, when
 * given, is the bin shown: a root and a folder next to it are dropped. */
export function validateLibrarySearch(search: Record<string, unknown>): LibrarySearch {
  const result: LibrarySearch = {};
  if (typeof search.q === "string" && search.q.trim()) {
    result.q = search.q;
  }
  if (typeof search.status === "string" && (STATUSES as string[]).includes(search.status)) {
    result.status = search.status as VideoStatus;
  }
  if (typeof search.sort === "string" && (SORTS as readonly string[]).includes(search.sort)) {
    result.sort = search.sort as LibrarySort;
  }
  if (typeof search.light === "string" && (LIGHTS as readonly string[]).includes(search.light)) {
    result.light = search.light as LibraryLight;
  }
  if (typeof search.timeline === "string" && search.timeline) {
    result.timeline = search.timeline;
  } else if (typeof search.root === "string" && search.root) {
    result.root = search.root;
    result.folder = typeof search.folder === "string" ? search.folder : "";
  }
  return result;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({ component: AppShell });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router redirect idiom
    throw redirect({ to: "/library" });
  },
});

export const libraryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/library",
  component: LibraryPage,
  validateSearch: validateLibrarySearch,
});

export const VIDEO_TABS = [
  "overview",
  "shots",
  "transcript",
  "audio",
  "technique",
  "context",
  "exports",
] as const;
export type VideoTab = (typeof VIDEO_TABS)[number];

interface VideoSearch {
  t?: number;
  tab?: VideoTab;
}

export const videoRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/videos/$videoId",
  component: VideoPage,
  validateSearch: (search: Record<string, unknown>): VideoSearch => {
    const result: VideoSearch = {};
    const t = Number(search.t);
    if (search.t !== undefined && Number.isFinite(t) && t >= 0) {
      result.t = t;
    }
    if (typeof search.tab === "string" && (VIDEO_TABS as readonly string[]).includes(search.tab)) {
      result.tab = search.tab as VideoTab;
    }
    return result;
  },
});

/** « Search »: the text and every filter live in the address. */
export const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/search",
  component: SearchPage,
  validateSearch: validateSearchPage,
});

/** « Questions »: the filters and the question shown live in the address. */
export const askRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ask",
  component: AskPage,
  validateSearch: validateAskPage,
});

const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs",
  component: JobsPage,
});
const systemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/system",
  component: SystemPage,
});
/** « Model bench »: vision models of LM Studio compared on the library. */
const benchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/bench",
  component: BenchPage,
});
/** « Connections »: addresses, token and MCP client configurations. */
const connectionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/connections",
  component: ConnectionsPage,
});
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router redirect idiom
    throw redirect({ to: "/system" });
  },
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  libraryRoute,
  searchRoute,
  askRoute,
  videoRoute,
  jobsRoute,
  systemRoute,
  benchRoute,
  connectionsRoute,
  settingsRoute,
]);

export function buildRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    scrollRestoration: true,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof buildRouter>;
  }
}
