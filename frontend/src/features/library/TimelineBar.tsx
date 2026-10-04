import { ChevronRight, Clapperboard, RefreshCw, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type Schemas } from "@/api/client";
import {
  useDeleteTimelineBin,
  useSyncTimeline,
  useTimelineItems,
  useUpdateTimelineBin,
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
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { SkippedList, SlowResolve } from "./ResolveNotes";
import { ATTENTION_STATES, stateParts } from "./timelines";

type TimelineBin = Schemas["TimelineBinOut"];

/** Above the videos of a Resolve timeline: when it was read in Resolve, where its
 * files stand, and « Update from Resolve » / « Remove from the library ». */
export function TimelineBar({ bin }: { bin: TimelineBin }) {
  const { t, i18n } = useTranslation();
  const ids = { details: useId(), auto: useId() };
  const sync = useSyncTimeline();
  const update = useUpdateTimelineBin();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const attention = ATTENTION_STATES.some((state) => bin.states[state] > 0);
  const running = bin.sync_job?.status === "running"; // the server refuses a second one

  const onSync = (): void => {
    sync.mutate(bin.id, {
      onSuccess: () => toast.success(t("timelines.bar.syncQueued")),
      onError: (error) => toast.error(errorMessage(error)),
    });
  };
  const onAutoAnalyze = (on: boolean): void => {
    update.mutate(
      { binId: bin.id, body: { auto_analyze: on } },
      {
        onSuccess: () => toast.success(t(on ? "timelines.bar.autoOn" : "timelines.bar.autoOff")),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  return (
    <section aria-label={bin.label} className="glass grid gap-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-start gap-3">
        <Clapperboard className="text-brand-teal mt-0.5 size-5 shrink-0" aria-hidden />
        <div className="grid min-w-52 flex-1 gap-1">
          <p className="text-sm">
            {t("timelines.bar.syncedAt", { date: formatDateTime(bin.synced_at, i18n.language) })}
          </p>
          <p className="text-muted-foreground text-xs" aria-live="polite">
            {stateParts(t, bin).join(" · ")}
          </p>
          {bin.sync_job?.status === "failed" ? (
            <p className="text-destructive text-xs">
              {[t("timelines.bar.failed"), bin.sync_job.message].filter(Boolean).join(" ")}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onSync}
            disabled={sync.isPending || running}
          >
            <RefreshCw className={cn("size-4", sync.isPending && "animate-spin")} aria-hidden />
            {t("timelines.bar.sync")}
          </Button>
          <RemoveTimeline bin={bin} />
        </div>
      </div>
      <SlowResolve pending={sync.isPending} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        {attention ? (
          <button
            type="button"
            aria-expanded={detailsOpen}
            aria-controls={ids.details}
            onClick={() => {
              setDetailsOpen(!detailsOpen);
            }}
            className="hover:text-foreground focus-visible:ring-ring text-muted-foreground flex items-center gap-1 rounded text-xs font-medium focus-visible:ring-2 focus-visible:outline-none"
          >
            <ChevronRight
              className={cn("size-3.5 transition-transform", detailsOpen && "rotate-90")}
              aria-hidden
            />
            {t("timelines.bar.details")}
          </button>
        ) : (
          <span />
        )}
        <div className="flex items-center gap-2">
          <Label htmlFor={ids.auto} className="text-xs font-normal">
            {t("timelines.bar.autoAnalyze")}
          </Label>
          <Switch
            id={ids.auto}
            size="sm"
            checked={bin.auto_analyze}
            disabled={update.isPending}
            onCheckedChange={onAutoAnalyze}
          />
        </div>
      </div>
      {attention && detailsOpen ? (
        <div id={ids.details}>
          <TimelineDetails binId={bin.id} />
        </div>
      ) : null}
      <SkippedList skipped={bin.report.skipped} errors={bin.report.errors} />
    </section>
  );
}

/** The files calling for a look, in the timeline's order, with what the update found. */
function TimelineDetails({ binId }: { binId: string }) {
  const { t } = useTranslation();
  const items = useTimelineItems(binId);
  if (items.isPending) return <Skeleton className="h-16 rounded-lg" />;
  if (items.isError) {
    return <p className="text-destructive text-xs">{errorMessage(items.error)}</p>;
  }
  const shown = items.data.filter((item) =>
    (ATTENTION_STATES as readonly string[]).includes(item.state),
  );
  if (shown.length === 0) {
    return <p className="text-muted-foreground text-xs">{t("timelines.bar.noDetails")}</p>;
  }
  return (
    <ul className="grid max-h-60 gap-1.5 overflow-y-auto text-xs">
      {shown.map((item) => (
        <li key={`${String(item.position)}:${item.path}`} className="grid gap-0.5">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span
              className={cn(
                "shrink-0 font-medium",
                item.state === "error" || item.state === "missing"
                  ? "text-destructive"
                  : "text-warning-ink",
              )}
            >
              {t(`timelines.bar.item.${item.state}`)}
            </span>
            <span className="font-mono break-all">{item.path}</span>
          </span>
          {item.note ? <span className="text-muted-foreground">{item.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** « Remove from the library », after a confirmation: the view only. */
function RemoveTimeline({ bin }: { bin: TimelineBin }) {
  const { t } = useTranslation();
  const remove = useDeleteTimelineBin();
  const [open, setOpen] = useState(false);
  const confirm = (): void => {
    // mutateAsync: this bar unmounts once the timeline is gone, and with it the callbacks of a
    // plain mutate().
    remove.mutateAsync(bin.id).then(
      () => {
        toast.success(t("timelines.bar.removed", { label: bin.label }));
        setOpen(false);
      },
      (error: unknown) => {
        toast.error(errorMessage(error));
      },
    );
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive">
          <Trash2 className="size-4" aria-hidden />
          {t("timelines.bar.remove")}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("timelines.bar.removeTitle", { label: bin.label })}</DialogTitle>
          <DialogDescription>{t("timelines.bar.removeBody")}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setOpen(false);
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button type="button" variant="destructive" onClick={confirm} disabled={remove.isPending}>
            <Trash2 className="size-4" aria-hidden />
            {t("timelines.bar.remove")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
