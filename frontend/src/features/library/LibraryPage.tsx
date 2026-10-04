import { useNavigate } from "@tanstack/react-router";
import { FolderPlus, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Schemas, VideoStatus } from "@/api/client";
import { useFolders, useRoots, useTimelineBins, useVideos } from "@/api/queries";
import { LIGHTS, libraryRoute, type LibraryLight, type LibrarySort } from "@/app/router";
import { EmptyState, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import { AddRootDialog } from "./AddRootDialog";
import { AddTimelineDialog } from "./AddTimelineDialog";
import { hasBin } from "./bins";
import { rememberLibrarySearch } from "./lastSearch";
import { RootsPanel } from "./RootsPanel";
import { rangeOf, selectVideos, useSelectedVideos } from "./selection";
import { SelectionBar } from "./SelectionBar";
import { TimelineBar } from "./TimelineBar";
import { VideoCard } from "./VideoCard";

const STATUSES: VideoStatus[] = [
  "ready",
  "partial",
  "analyzing",
  "queued",
  "new",
  "failed",
  "offline",
];
const SORTS: LibrarySort[] = ["recent", "name", "duration", "captured"];
const ALL = "all";
const TIMELINE_ORDER = "timeline"; // a timeline's videos in its order: no sort asked
const PAGE = 120; // videos shown at first, and added by « Load more »

/** The names from the root's bin down to the chosen folder, for the page subtitle. */
function binPath(
  folders: Schemas["RootFoldersOut"][] | undefined,
  rootId: string | undefined,
  folder: string | undefined,
): string[] | null {
  const item = folders?.find((f) => f.root_id === rootId);
  if (!item) return null;
  return [item.tree.name, ...(folder ?? "").split("/").filter(Boolean)];
}

export function LibraryPage() {
  const { t } = useTranslation();
  const search = libraryRoute.useSearch();
  const navigate = useNavigate({ from: "/library" });
  const [query, setQuery] = useState(search.q ?? "");

  // Debounce typing into the URL (the URL is the source of truth for filters).
  useEffect(() => {
    const handle = window.setTimeout(() => {
      if ((search.q ?? "") !== query) {
        void navigate({ search: (prev) => ({ ...prev, q: query || undefined }), replace: true });
      }
    }, 300);
    return () => {
      window.clearTimeout(handle);
    };
  }, [query, search.q, navigate]);

  useEffect(() => {
    rememberLibrarySearch(search); // for the way back from a video page
  }, [search]);

  const selected = useSelectedVideos();
  const anchor = useRef<string | null>(null); // last card ticked by hand, for shift-click ranges
  const roots = useRoots();
  const folders = useFolders();
  const timelines = useTimelineBins();
  const timeline = timelines.data?.find((bin) => bin.id === search.timeline);

  // A bin no longer in the tree (its folder removed, an old link): back to every video.
  const orphan =
    folders.data !== undefined &&
    search.root !== undefined &&
    !hasBin(folders.data, search.root, search.folder);
  // Likewise a timeline removed from the library.
  const orphanTimeline =
    timelines.data !== undefined && search.timeline !== undefined && timeline === undefined;
  useEffect(() => {
    if (orphan || orphanTimeline) {
      void navigate({
        search: (prev) => ({ ...prev, root: undefined, folder: undefined, timeline: undefined }),
        replace: true,
      });
    }
  }, [orphan, orphanTimeline, navigate]);

  const filters = {
    root_id: search.root,
    folder: search.root ? (search.folder ?? "") : undefined,
    timeline_bin_id: search.timeline,
    q: search.q,
    status: search.status ? [search.status] : undefined,
    light_phase: search.light ? [search.light] : undefined,
    sort: search.sort,
  };
  // « Load more » shows more of these videos; other filters, or another bin, start over.
  const shown = JSON.stringify(filters);
  const [more, setMore] = useState({ shown, limit: PAGE });
  const limit = more.shown === shown ? more.limit : PAGE;
  const videos = useVideos({ ...filters, limit });

  const adding = (
    <>
      <AddRootDialog />
      <AddTimelineDialog />
    </>
  );

  if (roots.data?.length === 0 && !timelines.data?.length) {
    return (
      <div className="grid gap-6">
        <PageHeader title={t("library.title")} subtitle={t("library.subtitle")} />
        <EmptyState
          icon={<FolderPlus className="size-10" />}
          title={t("library.emptyTitle")}
          body={t("library.emptyBody")}
          action={<div className="flex flex-wrap justify-center gap-2">{adding}</div>}
        />
      </div>
    );
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        title={t("library.title")}
        subtitle={
          timeline
            ? t("library.timelineSubtitle", {
                label: timeline.label,
                project: timeline.project.name,
              })
            : (binPath(folders.data, search.root, search.folder)?.join(" › ") ??
              t("library.subtitle"))
        }
        actions={adding}
      />
      {roots.data ? <RootsPanel roots={roots.data} /> : <Skeleton className="h-24 rounded-xl" />}
      {timeline ? <TimelineBar bin={timeline} /> : null}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-60 flex-1">
          <Search
            className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
            }}
            placeholder={t("library.search")}
            className="pl-9"
            aria-label={t("library.search")}
          />
        </div>
        <Select
          value={search.light ?? ALL}
          onValueChange={(value) => {
            void navigate({
              search: (prev) => ({
                ...prev,
                light: value === ALL ? undefined : (value as LibraryLight),
              }),
              replace: true,
            });
          }}
        >
          <SelectTrigger className="w-48" aria-label={t("library.allLights")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("library.allLights")}</SelectItem>
            {LIGHTS.map((light) => (
              <SelectItem key={light} value={light}>
                {t(`context.phase.${light}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={search.status ?? ALL}
          onValueChange={(value) =>
            void navigate({
              search: (prev) => ({
                ...prev,
                status: value === ALL ? undefined : (value as VideoStatus),
              }),
              replace: true,
            })
          }
        >
          <SelectTrigger className="w-48" aria-label={t("library.allStatuses")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("library.allStatuses")}</SelectItem>
            {STATUSES.map((status) => (
              <SelectItem key={status} value={status}>
                {t(`status.${status}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={search.sort ?? (search.timeline ? TIMELINE_ORDER : "recent")}
          onValueChange={(value) =>
            void navigate({
              search: (prev) => ({
                ...prev,
                sort: value === TIMELINE_ORDER ? undefined : (value as LibrarySort),
              }),
              replace: true,
            })
          }
        >
          <SelectTrigger className="w-44" aria-label="Tri">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {search.timeline ? (
              <SelectItem value={TIMELINE_ORDER}>{t("library.sort.timeline")}</SelectItem>
            ) : null}
            {SORTS.map((sort) => (
              <SelectItem key={sort} value={sort}>
                {t(`library.sort.${sort}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {videos.isPending ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="aspect-[4/3.2] rounded-xl" />
          ))}
        </div>
      ) : videos.data && videos.data.items.length > 0 ? (
        <>
          <SelectionBar
            ids={videos.data.items.map((video) => video.id)}
            offline={videos.data.items.filter((v) => v.status === "offline").map((v) => v.id)}
            selected={selected}
          />
          <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
            {videos.data.items.map((video, _, items) => (
              <VideoCard
                key={video.id}
                video={video}
                selected={selected.has(video.id)}
                onSelect={(on, range) => {
                  const ids = items.map((item) => item.id);
                  selectVideos(range ? rangeOf(ids, anchor.current, video.id) : [video.id], on);
                  anchor.current = video.id;
                }}
              />
            ))}
          </div>
          {videos.data.total > videos.data.items.length ? (
            <div className="text-muted-foreground flex flex-col items-center gap-3 py-2 text-sm">
              <p>
                {t("library.shownOf", {
                  count: videos.data.items.length,
                  total: videos.data.total,
                })}
              </p>
              <Button
                variant="secondary"
                disabled={videos.isPlaceholderData}
                onClick={() => {
                  setMore({ shown, limit: limit + PAGE });
                }}
              >
                {t("library.loadMore")}
              </Button>
            </div>
          ) : null}
        </>
      ) : (
        <p className="text-muted-foreground py-10 text-center text-sm">{t("library.noResults")}</p>
      )}
    </div>
  );
}
