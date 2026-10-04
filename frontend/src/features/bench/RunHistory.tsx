import { Trash2, Trophy } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { BenchRunSummary } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ALL_RUNS, isActive } from "./benchFormat";

const SHOWN = 6; // more runs than that: the older ones are folded

/** « History »: every run, the latest first; one click shows a run again, and « General
 * ranking » ranks the latest result of every model, all runs together. */
export function RunHistory({
  runs,
  shown,
  canCompare,
  onShow,
  onDelete,
  deleting,
}: {
  runs: BenchRunSummary[];
  shown: string | undefined; // a run id, or ALL_RUNS
  canCompare: boolean;
  onShow: (id: string) => void;
  onDelete: (id: string) => void;
  deleting: boolean;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const [unfolded, setUnfolded] = useState(false);
  const position = runs.findIndex((run) => run.id === shown);
  const visible = unfolded ? runs : runs.slice(0, Math.max(SHOWN, position + 1));
  const columns = ["date", "note", "models", "images", "language", "status"] as const;
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle className="text-base">{t("bench.history.title")}</CardTitle>
        {canCompare ? (
          <Button
            size="sm"
            variant={shown === ALL_RUNS ? "default" : "secondary"}
            aria-pressed={shown === ALL_RUNS}
            onClick={() => {
              onShow(ALL_RUNS);
            }}
          >
            <Trophy className="size-4" />
            {t("bench.all.title")}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full min-w-[44rem] text-sm" aria-label={t("bench.history.title")}>
            <thead className="bg-secondary/50 text-muted-foreground text-xs">
              <tr>
                {columns.map((column) => (
                  <th key={column} scope="col" className="px-3 py-2 text-left font-medium">
                    {t(`bench.history.columns.${column}`)}
                  </th>
                ))}
                <th scope="col" className="w-10 px-3 py-2">
                  <span className="sr-only">{t("bench.results.delete")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((run) => {
                const date = formatDateTime(run.created_at, locale);
                const current = run.id === shown;
                return (
                  <tr key={run.id} className={cn("border-t", current && "bg-secondary/60")}>
                    <th scope="row" className="px-3 py-2 text-left align-top font-medium">
                      <button
                        type="button"
                        className="text-left whitespace-nowrap underline-offset-2 hover:underline"
                        aria-current={current ? "true" : undefined}
                        aria-label={t("bench.history.open", { date })}
                        onClick={() => {
                          onShow(run.id);
                        }}
                      >
                        {date}
                      </button>
                    </th>
                    <td className="max-w-[22rem] px-3 py-2 align-top">
                      {run.note ?? <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="text-muted-foreground max-w-[20rem] px-3 py-2 align-top text-xs">
                      {run.models.join(" · ")}
                    </td>
                    <td className="px-3 py-2 align-top tabular-nums">{run.images}</td>
                    <td className="px-3 py-2 align-top">
                      {t(`bench.language.${run.language}`, { defaultValue: run.language })}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <Badge variant={isActive(run.status) ? "default" : "secondary"}>
                        {t(`bench.status.${run.status}`)}
                      </Badge>
                    </td>
                    <td className="px-1 py-1 align-top">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={t("bench.history.delete", { date })}
                        title={t("bench.results.delete")}
                        disabled={deleting || isActive(run.status)}
                        onClick={() => {
                          if (window.confirm(t("bench.results.deleteConfirm"))) {
                            onDelete(run.id);
                          }
                        }}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {runs.length > visible.length || unfolded ? (
          <Button
            variant="ghost"
            size="sm"
            className="justify-self-start"
            onClick={() => {
              setUnfolded(!unfolded);
            }}
          >
            {unfolded ? t("bench.history.less") : t("bench.history.more", { count: runs.length })}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
