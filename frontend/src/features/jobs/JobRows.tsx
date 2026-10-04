import { Link } from "@tanstack/react-router";
import { RotateCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type Job } from "@/api/client";
import { useCancelJob, useRetryJob } from "@/api/queries";
import { JobStatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { formatDateTime, formatPercent, formatSpan } from "@/lib/format";

/** Finished jobs that can be run again (a success has nothing left to do). */
const RETRYABLE = new Set<Job["status"]>(["failed", "partial", "cancelled"]);

function onError(error: unknown): void {
  toast.error(errorMessage(error));
}

function seconds(from: string, to: string | number): number {
  return ((typeof to === "number" ? to : Date.parse(to)) - Date.parse(from)) / 1000;
}

/** The current time, ticking every ``everyMs`` (for elapsed times). */
function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const handle = window.setInterval(() => {
      setNow(Date.now());
    }, everyMs);
    return () => {
      window.clearInterval(handle);
    };
  }, [everyMs]);
  return now;
}

/** What the job is about, linked to it: the video, the folder or the timeline. */
function JobTarget({ job, className }: { job: Job; className?: string }) {
  const label = job.target ?? job.video_id?.slice(-8) ?? null;
  const binId = typeof job.payload.bin_id === "string" ? job.payload.bin_id : undefined;
  const style = `hover:text-brand-teal truncate hover:underline ${className ?? ""}`;
  if (label === null) {
    return null;
  }
  if (job.video_id) {
    return (
      <Link to="/videos/$videoId" params={{ videoId: job.video_id }} className={style}>
        {label}
      </Link>
    );
  }
  if (job.kind === "scan_root" && job.root_id) {
    return (
      <Link to="/library" search={{ root: job.root_id }} className={style}>
        {label}
      </Link>
    );
  }
  if (job.kind === "sync_timeline" && binId && job.target) {
    return (
      <Link to="/library" search={{ timeline: binId }} className={style}>
        {label}
      </Link>
    );
  }
  return <span className={`truncate ${className ?? ""}`}>{label}</span>;
}

function CancelButton({ job, compact = false }: { job: Job; compact?: boolean }) {
  const { t } = useTranslation();
  const cancel = useCancelJob();
  if (job.cancel_requested) {
    return <span className="text-muted-foreground text-xs">{t("jobs.stopping")}</span>;
  }
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={compact ? t("jobs.cancelOne", { target: job.target ?? "" }) : undefined}
      onClick={() => {
        cancel.mutate(job.id, { onError });
      }}
      disabled={cancel.isPending}
    >
      <X className="size-4" />
      {compact ? null : t("jobs.cancel")}
    </Button>
  );
}

/** A job running now: its current step, its progress and how long it has been going. */
export function RunningJob({ job }: { job: Job }) {
  const { t } = useTranslation();
  const now = useNow();
  return (
    <li className="bg-card grid grid-cols-[minmax(0,1fr)] gap-2 rounded-xl border p-4">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="text-muted-foreground shrink-0 text-xs">{t(`jobKind.${job.kind}`)}</span>
          <JobTarget job={job} className="font-medium" />
          {job.place ? (
            <span className="text-muted-foreground hidden truncate text-xs sm:inline">
              {job.place}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          {job.started_at ? (
            <span className="text-muted-foreground text-xs">
              {t("jobs.since", { span: formatSpan(seconds(job.started_at, now)) })}
            </span>
          ) : null}
          <CancelButton job={job} />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Progress
          value={job.progress * 100}
          className="h-1.5"
          aria-label={t("jobs.progressOf", { target: job.target ?? "" })}
        />
        <span className="text-muted-foreground w-12 text-right font-mono text-xs">
          {formatPercent(job.progress)}
        </span>
      </div>
      {job.message ? <p className="text-muted-foreground truncate text-sm">{job.message}</p> : null}
    </li>
  );
}

/** A job waiting its turn, numbered in the order the worker will take it. */
export function QueuedRow({ job, position }: { job: Job; position: number }) {
  const { t } = useTranslation();
  return (
    <li className="flex min-w-0 items-center gap-3 px-4 py-2">
      <span className="text-muted-foreground w-8 shrink-0 text-right font-mono text-xs">
        {position}
      </span>
      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="text-muted-foreground shrink-0 text-xs">{t(`jobKind.${job.kind}`)}</span>
        <JobTarget job={job} className="text-sm" />
        {job.place ? (
          <span className="text-muted-foreground hidden truncate text-xs sm:inline">
            {job.place}
          </span>
        ) : null}
      </div>
      <CancelButton job={job} compact />
    </li>
  );
}

/** A finished job: how it ended, what it said, when and how long it took. */
export function HistoryRow({ job }: { job: Job }) {
  const { t, i18n } = useTranslation();
  const retry = useRetryJob();
  const note = job.error ?? job.message;
  const took =
    job.started_at && job.finished_at ? formatSpan(seconds(job.started_at, job.finished_at)) : null;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
      <JobStatusBadge status={job.status} />
      <div className="grid min-w-0 flex-1 gap-0.5">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="text-muted-foreground shrink-0 text-xs">{t(`jobKind.${job.kind}`)}</span>
          <JobTarget job={job} className="text-sm font-medium" />
          {job.place ? (
            <span className="text-muted-foreground hidden truncate text-xs sm:inline">
              {job.place}
            </span>
          ) : null}
        </div>
        {note ? (
          <p
            className={
              job.error
                ? "text-destructive truncate text-xs"
                : "text-muted-foreground truncate text-xs"
            }
            title={note}
          >
            {note}
          </p>
        ) : null}
      </div>
      <div className="text-muted-foreground flex items-center gap-3 text-xs">
        <span>{formatDateTime(job.finished_at ?? job.created_at, i18n.language)}</span>
        {took ? <span className="font-mono">{took}</span> : null}
        {RETRYABLE.has(job.status) ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              retry.mutate(job.id, { onError });
            }}
            disabled={retry.isPending}
          >
            <RotateCw className="size-4" />
            {t("jobs.retry")}
          </Button>
        ) : null}
      </div>
    </li>
  );
}

/** Jobs cancelled before they started, one row until unfolded. */
export function SkippedGroup({ jobs }: { jobs: Job[] }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [latest] = jobs;
  return (
    <li className="grid">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
        <JobStatusBadge status="cancelled" />
        <span className="text-muted-foreground min-w-0 flex-1 text-sm">
          {t("jobs.skipped", { count: jobs.length })}
        </span>
        <span className="text-muted-foreground text-xs">
          {formatDateTime(latest?.finished_at ?? latest?.created_at, i18n.language)}
        </span>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          onClick={() => {
            setOpen(!open);
          }}
        >
          {open ? t("jobs.collapse") : t("jobs.expand")}
        </Button>
      </div>
      {open ? (
        <ul className="bg-muted/30 divide-y border-t">
          {jobs.map((job) => (
            <HistoryRow key={job.id} job={job} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
