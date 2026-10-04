import { useNavigate } from "@tanstack/react-router";
import { Clapperboard, Film, Info, RotateCw, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState, type ReactNode, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { ApiError, errorMessage, type Schemas } from "@/api/client";
import {
  useImportTimeline,
  useResolveProject,
  useTimelineBins,
  useTimelinePreview,
} from "@/api/queries";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { formatClock, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import { SkippedList, SlowResolve } from "./ResolveNotes";
import { setTimelineBuildOpen, useSelectedVideos } from "./selection";
import { previewParts } from "./timelines";

type ResolveTimeline = Schemas["ResolveTimelineOut"];
type Preview = Schemas["TimelinePreviewOut"];

/** Past this many timelines, a field above the list filters them by name. */
const FILTER_FROM = 8;
/** What Resolve names a project never saved (English and French interfaces). */
const UNTITLED = new Set(["untitled project", "projet sans titre"]);

/** « Import from Resolve »: the timelines of the project open in DaVinci Resolve;
 * the one chosen brings its videos into the library, and becomes a view of them. The other way
 * (ticked videos into Resolve) is « Create a timeline », which this dialog points to. */
export function AddTimelineDialog({ trigger }: { trigger?: ReactNode }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // Here rather than in the form: an import still under way shows so when the dialog reopens.
  const importTimeline = useImportTimeline();
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button variant="outline">
            <Clapperboard className="size-4" />
            {t("library.addTimeline")}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        {/* Mounted while open only: Resolve is read again each time the dialog opens. */}
        <TimelineForm
          importTimeline={importTimeline}
          onClose={() => {
            setOpen(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

function TimelineForm({
  importTimeline,
  onClose,
}: {
  importTimeline: ReturnType<typeof useImportTimeline>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const ids = { label: useId(), auto: useId() };
  const project = useResolveProject(true);
  const bins = useTimelineBins();
  const [chosen, setChosen] = useState<string>();
  // What was typed or switched, for one timeline: another one starts from its own values.
  const [label, setLabel] = useState({ timeline: "", text: "" });
  const [auto, setAuto] = useState({ timeline: "", on: true });

  const timelines = project.data?.timelines ?? [];
  // The current timeline is chosen at first (listed first by the server).
  const selected =
    timelines.find((timeline) => timeline.id === chosen) ??
    timelines.find((timeline) => timeline.is_current) ??
    timelines[0];
  const preview = useTimelinePreview(project.data?.project.id, selected?.id);
  const labelText = label.timeline === selected?.id ? label.text : "";
  // A timeline already in the library keeps its choice unless switched here; a new one: on.
  const autoAnalyze =
    auto.timeline === selected?.id
      ? auto.on
      : (bins.data?.find((bin) => bin.id === selected?.bin_id)?.auto_analyze ?? true);

  // Another project was opened in Resolve since the list was read: read it again.
  const refetchProject = project.refetch;
  const projectChanged = preview.error instanceof ApiError && preview.error.status === 409;
  useEffect(() => {
    if (projectChanged) void refetchProject();
  }, [projectChanged, refetchProject]);

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    if (!project.data || !selected) return;
    // mutateAsync: the toast and the page still come when the dialog was closed meanwhile.
    importTimeline
      .mutateAsync({
        project_id: project.data.project.id,
        timeline_id: selected.id,
        snapshot_id: preview.data?.snapshot_id ?? null, // no second read of Resolve
        label: labelText.trim() || null,
        auto_analyze: autoAnalyze,
      })
      .then(
        (result) => {
          const done = result.created ? "timelines.add.added" : "timelines.add.updated";
          toast.success(t(done, { label: result.bin.label }));
          onClose();
          void navigate({ to: "/library", search: { timeline: result.bin.id } });
        },
        (error: unknown) => {
          toast.error(errorMessage(error));
          if (error instanceof ApiError && error.status === 409) void refetchProject();
        },
      );
  };

  return (
    <form onSubmit={submit} className="grid min-w-0 gap-5">
      <DialogHeader>
        <DialogTitle>{t("timelines.add.title")}</DialogTitle>
        <DialogDescription>{t("timelines.add.description")}</DialogDescription>
      </DialogHeader>
      <OtherWay onClose={onClose} />

      {project.isError ? (
        <ReadError
          error={project.error}
          retrying={project.isFetching}
          onRetry={() => void refetchProject()}
        />
      ) : project.data ? (
        <>
          <ProjectLine project={project.data} />
          {timelines.length === 0 ? (
            <p className="text-muted-foreground text-sm">{t("timelines.add.empty")}</p>
          ) : (
            <TimelineList timelines={timelines} selected={selected} onSelect={setChosen} />
          )}
        </>
      ) : (
        <div className="grid gap-2">
          <p className="text-muted-foreground text-sm">{t("timelines.add.reading")}</p>
          <Skeleton className="h-24 rounded-lg" />
        </div>
      )}
      <SlowResolve pending={project.isFetching} />

      {selected && !project.isError ? (
        <>
          <section aria-live="polite" className="bg-secondary/40 grid gap-2 rounded-lg p-3 text-sm">
            {preview.data ? (
              <PreviewSummary preview={preview.data} autoAnalyze={autoAnalyze} />
            ) : preview.isError ? (
              <ReadError
                error={preview.error}
                retrying={preview.isFetching}
                onRetry={() => void preview.refetch()}
              />
            ) : (
              <p className="text-muted-foreground">{t("timelines.add.previewReading")}</p>
            )}
            <SlowResolve pending={preview.isFetching} />
          </section>
          <div className="grid gap-2">
            <Label htmlFor={ids.label}>{t("timelines.add.label")}</Label>
            <Input
              id={ids.label}
              value={labelText}
              maxLength={200}
              placeholder={selected.bin_label ?? selected.name}
              onChange={(e) => {
                setLabel({ timeline: selected.id, text: e.target.value });
              }}
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <div className="grid gap-1">
              <Label htmlFor={ids.auto}>{t("timelines.add.autoAnalyze")}</Label>
              <p className="text-muted-foreground text-xs">{t("timelines.add.autoAnalyzeHelp")}</p>
            </div>
            <Switch
              id={ids.auto}
              checked={autoAnalyze}
              onCheckedChange={(on) => {
                setAuto({ timeline: selected.id, on });
              }}
            />
          </div>
        </>
      ) : null}

      <SlowResolve pending={importTimeline.isPending} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          {t("common.cancel")}
        </Button>
        <Button
          type="submit"
          // One read of Resolve at a time: the preview's, then the import (which reuses it).
          disabled={!selected || project.isError || importTimeline.isPending || preview.isFetching}
        >
          {selected?.bin_id ? t("timelines.add.update") : t("timelines.add.submit")}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Videos are ticked: the user may want them in Resolve, which is « Create a timeline ». */
function OtherWay({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const ticked = useSelectedVideos().size;
  if (ticked === 0) return null;
  return (
    <Alert>
      <Info aria-hidden />
      <AlertDescription className="text-foreground gap-3">
        <p>{t("timelines.add.ticked", { count: ticked })}</p>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => {
            onClose();
            setTimelineBuildOpen(true);
          }}
        >
          <Film className="size-4" aria-hidden />
          {t("timelines.add.toBuild", { count: ticked })}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

/** What Resolve said instead of answering (``detail`` says what to do), and « Try again ». */
function ReadError({
  error,
  retrying,
  onRetry,
}: {
  error: unknown;
  retrying: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Alert>
      <TriangleAlert className="text-warning-ink" aria-hidden />
      <AlertDescription className="text-foreground gap-3">
        <p>{errorMessage(error)}</p>
        <Button type="button" variant="secondary" size="sm" onClick={onRetry} disabled={retrying}>
          <RotateCw className={cn("size-4", retrying && "animate-spin")} aria-hidden />
          {t("timelines.retry")}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

function ProjectLine({ project }: { project: Schemas["ResolveProjectOut"] }) {
  const { t } = useTranslation();
  const untitled = UNTITLED.has(project.project.name.trim().toLocaleLowerCase());
  return (
    <div className="grid gap-1">
      <p className="text-sm">
        <span className="font-medium">
          {t("timelines.add.project", { name: project.project.name })}
        </span>
        <span className="text-muted-foreground"> · {project.database.name}</span>
      </p>
      {untitled ? (
        <p className="text-warning-ink flex items-start gap-1.5 text-xs">
          <TriangleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
          {t("timelines.add.untitled")}
        </p>
      ) : null}
    </div>
  );
}

/** The timelines as radio buttons (arrow keys move between them), in a scrolling list. */
function TimelineList({
  timelines,
  selected,
  onSelect,
}: {
  timelines: ResolveTimeline[];
  selected: ResolveTimeline | undefined;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const heading = useId();
  const name = useId();
  const [filter, setFilter] = useState("");
  const needle = filter.trim().toLocaleLowerCase();
  const shown = needle
    ? timelines.filter((timeline) => timeline.name.toLocaleLowerCase().includes(needle))
    : timelines;
  return (
    <div className="grid min-w-0 gap-2">
      <p id={heading} className="text-sm font-medium">
        {t("timelines.add.timelines")}
      </p>
      {timelines.length > FILTER_FROM ? (
        <Input
          type="search"
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value);
          }}
          placeholder={t("timelines.add.filter")}
          aria-label={t("timelines.add.filter")}
        />
      ) : null}
      <div
        role="radiogroup"
        aria-labelledby={heading}
        className="grid max-h-64 min-w-0 gap-1 overflow-y-auto rounded-lg border p-1"
      >
        {shown.map((timeline) => (
          <TimelineOption
            key={timeline.id}
            timeline={timeline}
            name={name}
            checked={timeline.id === selected?.id}
            onSelect={onSelect}
          />
        ))}
        {shown.length === 0 ? (
          <p className="text-muted-foreground p-2 text-sm">{t("timelines.add.noMatch")}</p>
        ) : null}
      </div>
    </div>
  );
}

function TimelineOption({
  timeline,
  name,
  checked,
  onSelect,
}: {
  timeline: ResolveTimeline;
  name: string;
  checked: boolean;
  onSelect: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const fps = formatNumber(timeline.fps, i18n.language, Number.isInteger(timeline.fps) ? 0 : 2);
  const facts = [
    t("timelines.add.fps", { fps }),
    formatClock(timeline.duration_s),
    timeline.video_clips === null
      ? null
      : t("timelines.add.clips", { count: timeline.video_clips }),
  ];
  return (
    <label className="has-checked:border-primary has-checked:bg-primary/5 has-focus-visible:ring-ring/50 grid min-w-0 cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-1 rounded-md border border-transparent p-2.5 has-focus-visible:ring-[3px]">
      <input
        type="radio"
        name={name}
        value={timeline.id}
        checked={checked}
        onChange={() => {
          onSelect(timeline.id);
        }}
        className="accent-primary row-span-2 mt-0.5 size-4 outline-none"
      />
      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="max-w-full truncate text-sm font-medium" title={timeline.name}>
          {timeline.name}
        </span>
        {timeline.is_current ? (
          <Badge variant="secondary">{t("timelines.add.current")}</Badge>
        ) : null}
        {timeline.bin_id ? <Badge variant="outline">{t("timelines.add.inLibrary")}</Badge> : null}
        {timeline.bin_id && timeline.changed_since_sync ? (
          <Badge
            variant="outline"
            className="border-warning/50 text-warning-ink"
            title={t("timelines.add.changedHint")}
          >
            {t("timelines.add.changed")}
          </Badge>
        ) : null}
      </span>
      <span className="text-muted-foreground col-start-2 text-xs">
        {facts.filter(Boolean).join(" · ")}
      </span>
    </label>
  );
}

/** « 98 videos: 98 already in the library · … » (parts with a count only), what is left
 * out, and the analyses that adding it asks for. */
function PreviewSummary({ preview, autoAnalyze }: { preview: Preview; autoAnalyze: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="grid min-w-0 gap-2">
      {preview.files === 0 ? (
        <p>{t("timelines.add.noFiles")}</p>
      ) : (
        <p>
          <span className="font-medium">{t("timelines.add.files", { count: preview.files })}</span>{" "}
          {previewParts(t, preview).join(" · ")}
        </p>
      )}
      {preview.new_folders.length > 0 ? (
        <div className="text-muted-foreground grid gap-0.5 text-xs">
          <p>{t("timelines.add.newFolders")}</p>
          <ul className="grid gap-0.5 font-mono">
            {preview.new_folders.map((folder) => (
              <li key={folder} className="truncate" title={folder}>
                {folder}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview.disabled_only > 0 ? (
        <p className="text-muted-foreground text-xs">
          {t("timelines.add.disabledOnly", { count: preview.disabled_only })}
        </p>
      ) : null}
      <SkippedList skipped={preview.skipped} errors={preview.read_errors} />
      {autoAnalyze ? (
        <p className="text-brand-teal text-xs">
          {t("timelines.add.toAnalyze", { count: preview.to_analyze })}
        </p>
      ) : null}
    </div>
  );
}
