import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { JobStatus } from "@/api/client";
import { useJobList, useJobsSummary } from "@/api/queries";
import { PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { groupHistory } from "./history";
import { HistoryRow, QueuedRow, RunningJob, SkippedGroup } from "./JobRows";
import { JobsSummary } from "./JobsSummary";
import { StopAllButton } from "./StopAllButton";

/** Queued jobs shown before « show all ». */
const QUEUE_PREVIEW = 10;
/** The API returns at most this many jobs at once. */
const MAX_JOBS = 500;
const HISTORY_PAGE = 50;

const HISTORY_FILTERS = {
  all: ["succeeded", "partial", "failed", "cancelled"],
  failed: ["failed"],
  partial: ["partial"],
  cancelled: ["cancelled"],
  succeeded: ["succeeded"],
} satisfies Record<string, JobStatus[]>;
type HistoryFilter = keyof typeof HISTORY_FILTERS;

function RunningSection() {
  const { t } = useTranslation();
  const running = useJobList({ status: ["running"], limit: 50 });
  const jobs = running.data ?? [];
  if (jobs.length === 0) {
    return null;
  }
  return (
    <section aria-labelledby="jobs-running" className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <h2 id="jobs-running" className="text-base font-semibold">
        {t("jobs.sections.running")}
      </h2>
      <ul className="grid grid-cols-[minmax(0,1fr)] gap-3">
        {jobs.map((job) => (
          <RunningJob key={job.id} job={job} />
        ))}
      </ul>
    </section>
  );
}

function QueueSection({ total }: { total: number }) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  const queued = useJobList({
    status: ["queued"],
    order: "queue",
    limit: all ? MAX_JOBS : QUEUE_PREVIEW,
  });
  const jobs = queued.data ?? [];
  if (total === 0 || jobs.length === 0) {
    return null;
  }
  const hidden = total - jobs.length;
  return (
    <section aria-labelledby="jobs-queued" className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <h2 id="jobs-queued" className="text-base font-semibold">
        {t("jobs.sections.queued", { count: total })}
      </h2>
      <ol className="bg-card divide-y rounded-xl border">
        {jobs.map((job, index) => (
          <QueuedRow key={job.id} job={job} position={index + 1} />
        ))}
      </ol>
      {hidden > 0 || all ? (
        <Button
          variant="ghost"
          size="sm"
          className="justify-self-start"
          onClick={() => {
            setAll(!all);
          }}
        >
          {all ? t("jobs.showLess") : t("jobs.showAll", { count: hidden })}
        </Button>
      ) : null}
    </section>
  );
}

function HistorySection() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [limit, setLimit] = useState(HISTORY_PAGE);
  const history = useJobList({ status: HISTORY_FILTERS[filter], order: "finished", limit });
  const jobs = history.data ?? [];
  return (
    <section aria-labelledby="jobs-history" className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="jobs-history" className="text-base font-semibold">
          {t("jobs.sections.history")}
        </h2>
        <div role="group" aria-label={t("jobs.filterLabel")} className="flex flex-wrap gap-1">
          {(Object.keys(HISTORY_FILTERS) as HistoryFilter[]).map((key) => (
            <Button
              key={key}
              variant={filter === key ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={filter === key}
              onClick={() => {
                setFilter(key);
                setLimit(HISTORY_PAGE);
              }}
            >
              {t(`jobs.filter.${key}`)}
            </Button>
          ))}
        </div>
      </div>
      {history.isPending ? (
        <Skeleton className="h-24 rounded-xl" />
      ) : jobs.length > 0 ? (
        <ul className="bg-card divide-y rounded-xl border">
          {groupHistory(jobs).map((item) =>
            "job" in item ? (
              <HistoryRow key={item.job.id} job={item.job} />
            ) : (
              <SkippedGroup key={item.skipped[0]?.id} jobs={item.skipped} />
            ),
          )}
        </ul>
      ) : (
        <p className="text-muted-foreground text-sm">{t("jobs.historyEmpty")}</p>
      )}
      {jobs.length === limit && limit < MAX_JOBS ? (
        <Button
          variant="ghost"
          size="sm"
          className="justify-self-start"
          onClick={() => {
            setLimit(Math.min(MAX_JOBS, limit + HISTORY_PAGE));
          }}
        >
          {t("jobs.more")}
        </Button>
      ) : null}
    </section>
  );
}

export function JobsPage() {
  const { t } = useTranslation();
  const summary = useJobsSummary();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <PageHeader
        title={t("jobs.title")}
        subtitle={t("jobs.subtitle")}
        actions={<StopAllButton />}
      />
      {summary.data ? (
        <JobsSummary summary={summary.data} />
      ) : (
        <Skeleton className="h-24 rounded-xl" />
      )}
      <RunningSection />
      <QueueSection total={summary.data?.queued ?? 0} />
      <HistorySection />
    </div>
  );
}
