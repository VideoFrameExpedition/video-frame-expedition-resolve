import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useBenchOverview, useBenchRun, useBenchRuns, useDeleteBenchRun } from "@/api/queries";
import { PageHeader } from "@/components/common";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";

import { AllModels } from "./AllModels";
import { BenchCharts } from "./BenchCharts";
import { ALL_RUNS, historyLanguages, isActive, rowsOfRun } from "./benchFormat";
import { BlindRating } from "./BlindRating";
import { ModelPicker } from "./ModelPicker";
import { Ranking } from "./Ranking";
import { ResultsTable } from "./ResultsTable";
import { RunFacts } from "./RunFacts";
import { RunHistory } from "./RunHistory";
import { RunProgress } from "./RunProgress";

/** « Model bench »: the vision models of LM Studio compared on frames of the
 * library — pick them, follow the run, read how they rank and what each measured (charts and
 * table), rate their descriptions, and find every past run again in the history, with a general
 * ranking of every model ever tested. */
export function BenchPage() {
  const { t, i18n } = useTranslation();
  const overview = useBenchOverview();
  const runs = useBenchRuns();
  const remove = useDeleteBenchRun();
  const [chosen, setChosen] = useState<string>();
  const listed = runs.data ?? [];
  const canCompare = historyLanguages(listed).length > 0;
  const all = chosen === ALL_RUNS && canCompare;
  const shownId = all
    ? undefined
    : ((chosen && listed.some((run) => run.id === chosen) ? chosen : undefined) ??
      overview.data?.active_run_id ??
      listed[0]?.id);
  const run = useBenchRun(shownId);
  const data = all ? undefined : run.data;
  const rows = data ? rowsOfRun(data) : [];
  const rateable = data && !isActive(data.status) && data.frames.length > 0;
  return (
    <div className="grid gap-6">
      <PageHeader title={t("bench.title")} subtitle={t("bench.subtitle")} />
      {overview.data ? (
        <ModelPicker overview={overview.data} onStarted={setChosen} />
      ) : overview.isError ? (
        <p className="text-destructive text-sm">{errorMessage(overview.error)}</p>
      ) : (
        <Skeleton className="h-64 rounded-xl" />
      )}
      {data && isActive(data.status) ? <RunProgress run={data} /> : null}
      {listed.length > 0 ? (
        <RunHistory
          runs={listed}
          shown={all ? ALL_RUNS : shownId}
          canCompare={canCompare}
          onShow={setChosen}
          deleting={remove.isPending}
          onDelete={(id) => {
            remove.mutate(id, {
              onSuccess: () => {
                if (id === shownId) {
                  setChosen(undefined);
                }
              },
              onError: (error) => toast.error(errorMessage(error)),
            });
          }}
        />
      ) : null}
      {all ? (
        <AllModels runs={listed} preferred={overview.data?.language} onOpen={setChosen} />
      ) : (
        <Card>
          <CardHeader className="flex flex-row flex-wrap items-baseline justify-between gap-3">
            <CardTitle className="text-base">{t("bench.results.title")}</CardTitle>
            {data ? (
              <span className="text-muted-foreground text-sm">
                {t("bench.results.runLabel", {
                  date: formatDateTime(data.created_at, i18n.language),
                  count: data.models.length,
                  images: data.images,
                })}
              </span>
            ) : null}
          </CardHeader>
          <CardContent className="grid gap-4">
            {!shownId ? (
              <p className="text-muted-foreground text-sm">{t("bench.results.empty")}</p>
            ) : !data ? (
              <Skeleton className="h-40 rounded-xl" />
            ) : (
              <>
                <RunFacts run={data} />
                <Ranking rows={rows} />
                <BenchCharts rows={rows} />
                {rows.length > 0 ? <ResultsTable rows={rows} /> : null}
              </>
            )}
          </CardContent>
        </Card>
      )}
      {rateable ? <BlindRating key={data.id} run={data} /> : null}
    </div>
  );
}
