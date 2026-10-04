import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type BenchRun } from "@/api/client";
import { useActiveJobs, useCancelJob } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { formatPercent } from "@/lib/format";

/** A run under way: which model is on the card, how far it is, and the button to stop it. */
export function RunProgress({ run }: { run: BenchRun }) {
  const { t } = useTranslation();
  const jobs = useActiveJobs();
  const cancel = useCancelJob();
  // The job follows the event stream (every answer); the run itself is read every few seconds.
  const job = jobs.data?.find((candidate) => candidate.id === run.job_id);
  const progress = job?.progress ?? run.progress;
  const message = job?.message ?? run.message;
  const stopping = job?.cancel_requested ?? false;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <CardTitle className="text-base">{t("bench.progress.title")}</CardTitle>
        <Button
          variant="secondary"
          size="sm"
          disabled={!run.job_id || cancel.isPending || stopping}
          onClick={() => {
            if (run.job_id) {
              cancel.mutate(run.job_id, { onError: (error) => toast.error(errorMessage(error)) });
            }
          }}
        >
          <X className="size-4" />
          {stopping ? t("bench.progress.stopping") : t("bench.progress.stop")}
        </Button>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex items-center gap-3">
          <Progress
            value={progress * 100}
            className="h-1.5"
            aria-label={t("bench.progress.title")}
          />
          <span className="text-muted-foreground w-12 text-right font-mono text-xs">
            {formatPercent(progress)}
          </span>
        </div>
        <p className="text-sm">
          {run.status === "queued" ? t("bench.progress.queued") : (message ?? "…")}
        </p>
        {run.model_runs.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {run.model_runs.map((model) => (
              <li key={model.key}>
                <Badge variant={model.status === "running" ? "default" : "secondary"}>
                  {model.display_name} · {t(`bench.modelStatus.${model.status}`)}
                </Badge>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}
