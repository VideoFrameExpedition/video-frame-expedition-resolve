import { useNavigate, useSearch } from "@tanstack/react-router";
import { ChevronRight, Clapperboard, FileVideo, Folder, FolderOpen, Library } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { errorMessage, type Schemas } from "@/api/client";
import { useFolders, useTimelineBins } from "@/api/queries";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { binKey, binsLeadingTo, isBinOpen, keepBinsOpen, setBinOpen } from "./bins";
import { sameLabels } from "./timelines";

type FolderNode = Schemas["FolderOut"];
type RootKind = Schemas["RootKind"];
type TimelineBin = Schemas["TimelineBinOut"];

/** The library's folders as bins, as in DaVinci Resolve's media pool: each root is
 * a bin named like it, each sub-folder a bin inside it. A bin shows its own videos. Below them,
 * the Resolve timelines brought into the library, each showing its videos. */
export function BinsTree() {
  const { t } = useTranslation();
  const heading = useId();
  const filesHint = useId();
  const folders = useFolders();
  const timelines = useTimelineBins();
  const search = useSearch({ strict: false });
  const navigate = useNavigate();
  const [, redraw] = useState(0);
  const selected = { root: search.root, folder: search.folder };
  const all = search.root === undefined && search.timeline === undefined;
  // The bins leading to the open one are open, so that it shows (after a link or a reload).
  const leading = new Set(search.root ? binsLeadingTo(search.root, search.folder ?? "") : []);

  // A folder (or every video), or a timeline: never both.
  const open = (root: string | undefined, folder: string | undefined): void => {
    keepBinsOpen(leading); // still open once another bin is chosen
    void navigate({
      to: "/library",
      search: (prev) => ({ ...prev, root, folder, timeline: undefined }),
    });
  };
  const openTimeline = (timeline: string): void => {
    keepBinsOpen(leading);
    void navigate({
      to: "/library",
      search: (prev) => ({ ...prev, root: undefined, folder: undefined, timeline }),
    });
  };
  const toggle = (key: string, next: boolean): void => {
    setBinOpen(key, next);
    redraw((n) => n + 1);
  };

  return (
    <>
      <nav aria-labelledby={heading} className="grid grid-cols-1 gap-1 border-t pt-5">
        <h2
          id={heading}
          className="text-muted-foreground mb-1 px-3 text-xs font-semibold tracking-wide uppercase"
        >
          {t("bins.title")}
        </h2>
        <button
          type="button"
          onClick={() => {
            open(undefined, undefined);
          }}
          aria-current={all ? "page" : undefined}
          className={row(all)}
        >
          <Library className="size-4 shrink-0" aria-hidden />
          <span className="truncate">{t("bins.all")}</span>
        </button>
        {folders.data ? (
          // Its own scroll area: with many bins, the « Analyse » panel stays in view.
          <ul className="-mx-0.5 grid max-h-[40vh] grid-cols-1 gap-0.5 overflow-y-auto p-0.5">
            {folders.data.map((item) => (
              <Bin
                key={item.root_id}
                rootId={item.root_id}
                kind={item.kind}
                filesHint={filesHint}
                node={item.tree}
                depth={0}
                selected={selected}
                leading={leading}
                onOpen={open}
                onToggle={toggle}
              />
            ))}
          </ul>
        ) : folders.isError ? (
          <p className="text-destructive px-3 text-xs">{errorMessage(folders.error)}</p>
        ) : (
          <Skeleton className="h-16 rounded-lg" />
        )}
        <span id={filesHint} hidden>
          {t("bins.filesRoot")}
        </span>
      </nav>
      {timelines.data?.length ? (
        <TimelineBins bins={timelines.data} selected={search.timeline} onOpen={openTimeline} />
      ) : null}
    </>
  );
}

/** « Resolve timelines »: one row each, showing the timeline's videos in its order. */
function TimelineBins({
  bins,
  selected,
  onOpen,
}: {
  bins: TimelineBin[];
  selected: string | undefined;
  onOpen: (id: string) => void;
}) {
  const { t } = useTranslation();
  const heading = useId();
  const ambiguous = sameLabels(bins); // told apart by their project
  return (
    <nav aria-labelledby={heading} className="grid grid-cols-1 gap-1 border-t pt-5">
      <h2
        id={heading}
        className="text-muted-foreground mb-1 px-3 text-xs font-semibold tracking-wide uppercase"
      >
        {t("bins.timelines")}
      </h2>
      <ul className="-mx-0.5 grid max-h-[30vh] grid-cols-1 gap-0.5 overflow-y-auto p-0.5">
        {bins.map((bin) => {
          const active = selected === bin.id;
          return (
            <li key={bin.id} className="flex">
              <button
                type="button"
                onClick={() => {
                  onOpen(bin.id);
                }}
                aria-current={active ? "page" : undefined}
                title={`${bin.project.name} › ${bin.timeline.name}`}
                className={row(active)}
              >
                <Clapperboard className="text-brand-teal size-4 shrink-0" aria-hidden />
                <span className="truncate">
                  {bin.label}
                  {ambiguous.has(bin.label.toLocaleLowerCase()) ? (
                    <>
                      {" "}
                      <span className="text-muted-foreground font-normal">
                        · {bin.project.name}
                      </span>
                    </>
                  ) : null}
                </span>
                {bin.videos > 0 ? (
                  <span className="text-muted-foreground ml-auto shrink-0 pl-1 font-mono text-xs">
                    {bin.videos}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function row(active: boolean): string {
  return cn(
    "text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:ring-ring flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none",
    active && "bg-secondary text-foreground font-medium",
  );
}

function Bin({
  rootId,
  kind = "folder",
  filesHint,
  node,
  depth,
  selected,
  leading,
  onOpen,
  onToggle,
}: {
  rootId: string;
  kind?: RootKind; // files: only the files a timeline brought in
  filesHint?: string;
  node: FolderNode;
  depth: number;
  selected: { root: string | undefined; folder: string | undefined };
  leading: ReadonlySet<string>;
  onOpen: (root: string, folder: string) => void;
  onToggle: (key: string, open: boolean) => void;
}) {
  const { t } = useTranslation();
  const key = binKey(rootId, node.path);
  const isOpen = isBinOpen(key, depth === 0 || leading.has(key));
  const active = selected.root === rootId && (selected.folder ?? "") === node.path;
  const files = kind === "files";
  const Icon = files ? FileVideo : active ? FolderOpen : Folder;
  return (
    <li>
      <div className="flex items-center" style={{ paddingLeft: `${depth * 0.875}rem` }}>
        {node.children.length > 0 ? (
          <button
            type="button"
            onClick={() => {
              onToggle(key, !isOpen);
            }}
            aria-expanded={isOpen}
            aria-label={t(isOpen ? "bins.collapse" : "bins.expand", { name: node.name })}
            className="text-muted-foreground hover:text-foreground focus-visible:ring-ring flex size-5 shrink-0 items-center justify-center rounded focus-visible:ring-2 focus-visible:outline-none"
          >
            <ChevronRight className={cn("size-3.5 transition-transform", isOpen && "rotate-90")} />
          </button>
        ) : (
          <span className="size-5 shrink-0" aria-hidden />
        )}
        <button
          type="button"
          onClick={() => {
            onOpen(rootId, node.path);
          }}
          aria-current={active ? "page" : undefined}
          title={files ? `${node.name} — ${t("bins.filesRoot")}` : node.path || node.name}
          aria-describedby={files ? filesHint : undefined}
          className={row(active)}
        >
          <Icon className="text-brand-teal size-4 shrink-0" aria-hidden />
          <span className="truncate">{node.name}</span>
          {node.count > 0 ? (
            <span className="text-muted-foreground ml-auto shrink-0 pl-1 font-mono text-xs">
              {node.count}
            </span>
          ) : null}
        </button>
      </div>
      {isOpen && node.children.length > 0 ? (
        <ul className="grid grid-cols-1 gap-0.5">
          {node.children.map((child) => (
            <Bin
              key={child.path}
              rootId={rootId}
              node={child}
              depth={depth + 1}
              selected={selected}
              leading={leading}
              onOpen={onOpen}
              onToggle={onToggle}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
