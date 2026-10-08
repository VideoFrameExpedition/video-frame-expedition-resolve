import { Clapperboard, Download, Film, Info, RotateCw } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type Schemas } from "@/api/client";
import {
  useBuildResolveTimeline,
  useExportTimeline,
  useResolveProject,
  useTimelinePlan,
  type TimelineBuild,
} from "@/api/queries";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { saveBlob } from "@/lib/download";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { SlowResolve } from "./ResolveNotes";
import { setTimelineBuildOpen, useTimelineBuildOpen } from "./selection";
import { partCount, readParts, saveParts, type PartName } from "./timelineParts";
import { TimelinePartsField } from "./TimelinePartsField";

type Plan = Schemas["TimelinePlanOut"];
type Order = NonNullable<TimelineBuild["order"]>;
type RateName = NonNullable<TimelineBuild["frame_rate"]>;
type SkipReason = Schemas["TimelineSkippedOut"]["reason"];

const ORDERS: readonly Order[] = ["capture", "name", "selection"];
/** Every rate the server takes, as Resolve writes them. */
const RATES = [
  "23.976", "24", "25", "29.97", "30", "47.952", "48", "50", "59.94", "60", "100", "119.88", "120",
] as const satisfies readonly RateName[]; // prettier-ignore
/** Offered besides the videos' own rates and sizes. */
const COMMON_RATES: readonly RateName[] = [
  "23.976",
  "24",
  "25",
  "29.97",
  "30",
  "50",
  "59.94",
  "60",
];
const COMMON_SIZES: readonly (readonly [number, number])[] = [
  [3840, 2160],
  [2160, 3840],
  [1920, 1080],
  [1080, 1920],
];
const SKIP_REASONS: readonly SkipReason[] = ["offline", "not_examined", "no_folder_pair"];
const FIRST_NAMES = 3;

function isRate(value: string): value is RateName {
  return (RATES as readonly string[]).includes(value);
}

function sizeKey(width: number, height: number): string {
  return `${width.toString()}x${height.toString()}`;
}

/** « 29.97 » → « 29,97 » in French. */
function rateText(name: string, locale: string): string {
  return Number(name).toLocaleString(locale, { maximumFractionDigits: 3 });
}

/**
 * « Create a timeline »: the ticked videos, whole and end to end, as files
 * to import in DaVinci Resolve, or straight into the project open in Resolve when it is; with,
 * as chosen (and remembered), subtitles of what is said and of what each shot shows, markers
 * over the suggested stretches and where chapters start.
 */
export function TimelineBuildDialog({ ids }: { ids: readonly string[] }) {
  const { t } = useTranslation();
  const open = useTimelineBuildOpen(); // « Import from Resolve » may hand over to it
  // Here rather than in the form: a timeline still being built shows so when the dialog reopens.
  const build = useBuildResolveTimeline();
  return (
    <Dialog open={open} onOpenChange={setTimelineBuildOpen}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="xs" title={t("timelineBuild.hint")}>
          <Film aria-hidden />
          {t("timelineBuild.button")}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90svh] grid-cols-[minmax(0,1fr)] overflow-y-auto sm:max-w-2xl">
        {/* Mounted while open only: Resolve is looked for each time the dialog opens. */}
        <BuildForm
          ids={ids}
          build={build}
          onClose={() => {
            setTimelineBuildOpen(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

function BuildForm({
  ids,
  build,
  onClose,
}: {
  ids: readonly string[];
  build: ReturnType<typeof useBuildResolveTimeline>;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const field = {
    name: useId(),
    order: useId(),
    rate: useId(),
    size: useId(),
  };
  const [name, setName] = useState("");
  const [order, setOrder] = useState<Order>("capture");
  const [rate, setRate] = useState<RateName | null>(null); // null: the most frequent
  const [size, setSize] = useState<readonly [number, number] | null>(null);
  const [parts, setParts] = useState(readParts);
  const choose = (part: PartName, on: boolean): void => {
    const next = { ...parts, [part]: on };
    setParts(next);
    saveParts(next);
  };
  const request: TimelineBuild = {
    video_ids: [...ids],
    order,
    frame_rate: rate,
    width: size?.[0] ?? null,
    height: size?.[1] ?? null,
    // The preview counts all of them either way: a ticked box does not ask again.
    transcript: true,
    suggestions: true,
    chapters: true,
    shots: true,
  };
  const plan = useTimelinePlan(request);
  const project = useResolveProject(true);
  const exportFile = useExportTimeline();
  const named: TimelineBuild = { ...request, ...parts, name: name.trim() || null };
  const brings = (part: PartName): boolean => parts[part] && partCount(plan.data, part) > 0;
  const ready = (plan.data?.videos ?? 0) > 0 && !build.isPending && !exportFile.isPending;

  const download = (): void => {
    exportFile.mutate(named, {
      onSuccess: ({ blob, filename }) => {
        saveBlob(blob, filename);
        toast.success(t("timelineBuild.downloaded", { filename }), {
          description: t("timelineBuild.importHint"),
        });
        onClose();
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  const addToResolve = (): void => {
    const asked = plan.data?.fps;
    // mutateAsync: the toasts still come when the dialog was closed meanwhile.
    build.mutateAsync(named).then(
      (result) => {
        toast.success(
          t("timelineBuild.created", {
            name: result.timeline_name,
            project: result.project.name,
            count: result.clips,
          }),
          result.markers > 0
            ? { description: t("timelineBuild.markersAdded", { count: result.markers }) }
            : undefined,
        );
        if (result.markers_missed > 0) {
          toast.warning(t("timelineBuild.markersMissed", { count: result.markers_missed }));
        }
        if (result.subtitles_laid.length > 0) {
          toast.info(
            t("timelineBuild.subtitlesLaid", { tracks: result.subtitles_laid.join(", ") }),
            { description: t("timelineBuild.subtitlesLaidHint") },
          );
        } else if (result.subtitles.length > 0) {
          // Resolve elsewhere gets the videos' own files when no folder took the timeline's
          // tracks; here, it did not lay them.
          const remote = Boolean(plan.data?.resolve_host);
          toast.info(
            t(remote ? "timelineBuild.subtitlesInBin" : "timelineBuild.subtitlesNotLaid", {
              count: result.subtitles.length,
            }),
            {
              description: t(
                remote ? "timelineBuild.subtitlesHint" : "timelineBuild.subtitlesNotLaidHint",
                { names: result.subtitles.join(", ") },
              ),
              duration: 20_000,
            },
          );
        }
        const left = result.subtitle_files.filter((file) => file.status !== "written");
        if (left.length > 0) {
          // A file of that name the application did not write: said in the interface's
          // language, with how to have it written.
          const conflicts = left.filter((file) => file.status === "conflict").length;
          const lines = left.map((file) =>
            t("timelineBuild.subtitleLeft", {
              file: file.file,
              folder: file.folder,
              reason:
                file.status === "conflict"
                  ? t("timelineBuild.subtitleConflict")
                  : (file.detail ?? ""),
            }),
          );
          if (conflicts > 0) {
            lines.push(t("timelineBuild.subtitleConflictHint", { count: conflicts }));
          }
          toast.warning(t("timelineBuild.subtitlesLeft", { count: left.length }), {
            description: lines.join("\n"),
            duration: 20_000,
          });
        }
        if (result.missing.length > 0) {
          toast.warning(t("timelineBuild.missing", { count: result.missing.length }), {
            description: result.missing.join("\n"),
          });
        }
        if (result.fps && asked && Math.abs(result.fps - asked) > 0.001) {
          toast.info(
            t("timelineBuild.projectRate", { fps: rateText(String(result.fps), i18n.language) }),
          );
        }
        onClose();
      },
      (error: unknown) => toast.error(errorMessage(error)),
    );
  };

  return (
    <div className="grid min-w-0 gap-5">
      <DialogHeader>
        <DialogTitle>{t("timelineBuild.title")}</DialogTitle>
        <DialogDescription>{t("timelineBuild.description")}</DialogDescription>
      </DialogHeader>

      <div className="grid gap-2">
        <Label htmlFor={field.name}>{t("timelineBuild.name")}</Label>
        <Input
          id={field.name}
          value={name}
          maxLength={120}
          placeholder={plan.data?.suggested_name}
          onChange={(e) => {
            setName(e.target.value);
          }}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Choice id={field.order} label={t("timelineBuild.order")}>
          <Select
            value={order}
            onValueChange={(value) => {
              setOrder(value as Order);
            }}
          >
            <SelectTrigger id={field.order} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ORDERS.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`timelineBuild.orders.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Choice>
        <Choice id={field.rate} label={t("timelineBuild.rate")}>
          <Select
            value={rate ?? plan.data?.frame_rate ?? ""}
            onValueChange={(value) => {
              if (isRate(value)) setRate(value);
            }}
          >
            <SelectTrigger id={field.rate} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {rateOptions(plan.data, rate).map(({ value, videos }) => (
                <SelectItem key={value} value={value}>
                  {t("timelineBuild.rateValue", { fps: rateText(value, i18n.language) })}
                  {videos ? ` · ${t("timelineBuild.videos", { count: videos })}` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Choice>
        <Choice id={field.size} label={t("timelineBuild.size")}>
          <Select
            value={
              size ? sizeKey(...size) : plan.data ? sizeKey(plan.data.width, plan.data.height) : ""
            }
            onValueChange={(value) => {
              const [width = 0, height = 0] = value.split("x").map(Number);
              if (width > 0 && height > 0) setSize([width, height]);
            }}
          >
            <SelectTrigger id={field.size} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sizeOptions(plan.data, size).map(({ width, height, videos }) => (
                <SelectItem key={sizeKey(width, height)} value={sizeKey(width, height)}>
                  {t("timelineBuild.sizeValue", { width, height })}
                  {videos ? ` · ${t("timelineBuild.videos", { count: videos })}` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Choice>
      </div>

      <TimelinePartsField plan={plan.data} parts={parts} onChange={choose} />

      <section aria-live="polite" className="bg-secondary/40 grid gap-2 rounded-lg p-3 text-sm">
        {plan.data ? (
          <PlanSummary plan={plan.data} />
        ) : plan.isError ? (
          <p>{errorMessage(plan.error)}</p>
        ) : (
          <Skeleton className="h-10 rounded-md" />
        )}
      </section>

      <ResolveState
        project={project}
        markers={brings("chapters") || brings("suggestions")}
        subtitles={brings("transcript") || brings("shots")}
        remote={Boolean(plan.data?.resolve_host)}
      />

      <SlowResolve pending={build.isPending} />
      <DialogFooter className="sm:flex-wrap">
        <Button type="button" variant="ghost" onClick={onClose}>
          {t("common.cancel")}
        </Button>
        <Button
          type="button"
          variant={project.data ? "outline" : "default"}
          disabled={!ready}
          onClick={download}
        >
          <Download aria-hidden />
          {t("timelineBuild.download")}
        </Button>
        {project.data ? (
          <Button type="button" disabled={!ready} onClick={addToResolve}>
            <Clapperboard aria-hidden />
            {t("timelineBuild.add")}
          </Button>
        ) : null}
      </DialogFooter>
    </div>
  );
}

function Choice({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

/** The videos' own rates (with how many have each), the usual ones, and the one chosen. */
function rateOptions(
  plan: Plan | undefined,
  chosen: RateName | null,
): { value: RateName; videos: number }[] {
  const found = new Map(plan?.rates.map((rate) => [rate.frame_rate, rate.videos]));
  const names = new Set<string>([
    ...found.keys(),
    ...COMMON_RATES,
    chosen ?? "",
    plan?.frame_rate ?? "",
  ]);
  return [...names]
    .filter(isRate)
    .sort((a, b) => Number(a) - Number(b))
    .map((value) => ({ value, videos: found.get(value) ?? 0 }));
}

/** The videos' own picture sizes (the most frequent first), then the usual ones. */
function sizeOptions(
  plan: Plan | undefined,
  chosen: readonly [number, number] | null,
): { width: number; height: number; videos: number }[] {
  const options = new Map<string, { width: number; height: number; videos: number }>();
  const add = (width: number, height: number, videos = 0): void => {
    const key = sizeKey(width, height);
    if (!options.has(key)) options.set(key, { width, height, videos });
  };
  for (const size of plan?.sizes ?? []) add(size.width, size.height, size.videos);
  if (plan) add(plan.width, plan.height);
  if (chosen) add(...chosen);
  for (const [width, height] of COMMON_SIZES) add(width, height);
  return [...options.values()];
}

/** « 42 videos · 12:30 · 3840 × 2160 · 29.97 fps », the first names, what is left out. */
function PlanSummary({ plan }: { plan: Plan }) {
  const { t, i18n } = useTranslation();
  if (plan.videos === 0) {
    return (
      <div className="grid gap-2">
        <p>{t("timelineBuild.nothing")}</p>
        <SkippedVideos skipped={plan.skipped} />
      </div>
    );
  }
  const first = plan.files.slice(0, FIRST_NAMES);
  const more = plan.videos - first.length;
  return (
    <div className="grid min-w-0 gap-2">
      <p>
        <span className="font-medium">{t("timelineBuild.videos", { count: plan.videos })}</span>
        {" · "}
        {[
          formatClock(plan.duration_s),
          t("timelineBuild.sizeValue", { width: plan.width, height: plan.height }),
          t("timelineBuild.rateValue", { fps: rateText(plan.frame_rate, i18n.language) }),
        ].join(" · ")}
      </p>
      <p className="text-muted-foreground truncate text-xs" title={plan.files.join("\n")}>
        {t("timelineBuild.first", { names: first.join(", ") })}
        {more > 0 ? ` ${t("timelineBuild.more", { count: more })}` : ""}
      </p>
      <SkippedVideos skipped={plan.skipped} />
      {plan.resolve_host ? (
        <p className="text-muted-foreground text-xs">
          {t("timelineBuild.remotePaths", { host: plan.resolve_host })}
        </p>
      ) : null}
    </div>
  );
}

/** « 2 offline videos (files not found) » for each reason (names on hover). */
function SkippedVideos({ skipped }: { skipped: Plan["skipped"] }) {
  const { t } = useTranslation();
  if (skipped.length === 0) return null;
  return (
    <div className="text-muted-foreground grid gap-1 text-xs">
      <p>{t("timelineBuild.skippedTitle")}</p>
      <ul className="grid list-disc gap-0.5 pl-5">
        {SKIP_REASONS.map((reason) => {
          const names = skipped.filter((video) => video.reason === reason).map((v) => v.filename);
          return names.length > 0 ? (
            <li key={reason} title={names.join("\n")}>
              {t(`timelineBuild.skipped.${reason}`, { count: names.length })}
            </li>
          ) : null;
        })}
      </ul>
    </div>
  );
}

/** Whether DaVinci Resolve is open, and in which project; else why not, and the files instead.
 * ``markers``: markers go on the media pool clips; ``subtitles``: tracks laid on the timeline
 * (``remote``: Resolve on another computer, the tracks' files next to the first video). */
function ResolveState({
  project,
  markers,
  subtitles,
  remote,
}: {
  project: ReturnType<typeof useResolveProject>;
  markers: boolean;
  subtitles: boolean;
  remote: boolean;
}) {
  const { t } = useTranslation();
  const heading = useId();
  return (
    <section aria-labelledby={heading} aria-live="polite" className="grid min-w-0 gap-1.5 text-sm">
      <h3 id={heading} className="font-medium">
        {t("timelineBuild.resolve.heading")}
      </h3>
      {project.data ? (
        <>
          <p>{t("timelineBuild.resolve.open", { name: project.data.project.name })}</p>
          <p className="text-muted-foreground text-xs">
            {t("timelineBuild.resolve.what")}
            {markers ? ` ${t("timelineBuild.resolve.whatMarkers")}` : ""}
            {subtitles
              ? ` ${t(remote ? "timelineBuild.resolve.whatSubtitlesRemote" : "timelineBuild.resolve.whatSubtitles")}`
              : ""}
          </p>
        </>
      ) : project.isError ? (
        <div className="grid justify-items-start gap-2">
          <p className="text-muted-foreground flex items-start gap-1.5">
            <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
            {errorMessage(project.error)}
          </p>
          <p className="text-muted-foreground text-xs">{t("timelineBuild.resolve.fileInstead")}</p>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => void project.refetch()}
            disabled={project.isFetching}
          >
            <RotateCw className={cn("size-4", project.isFetching && "animate-spin")} aria-hidden />
            {t("timelines.retry")}
          </Button>
        </div>
      ) : (
        <p className="text-muted-foreground">{t("timelineBuild.resolve.looking")}</p>
      )}
      <SlowResolve pending={project.isFetching} />
    </section>
  );
}
