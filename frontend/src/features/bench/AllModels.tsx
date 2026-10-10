import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { BenchRunSummary } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { BenchCharts } from "./BenchCharts";
import { historyLanguages, mixedImages, rowsOfHistory } from "./benchFormat";
import { filtering, passes, type ModelFilters } from "./modelCodes";
import { Ranking } from "./Ranking";
import { ResultsTable } from "./ResultsTable";

/** « General ranking »: the latest result of every model, all runs together, ranked, drawn
 * and listed, so that a model tested today compares with those tested last month. One language
 * at a time: whether a model keeps to the language depends on the language asked. */
export function AllModels({
  runs,
  preferred,
  filters,
  onOpen,
}: {
  runs: BenchRunSummary[];
  preferred: string | undefined; // the language the analyses are written in now
  filters: ModelFilters; // those of the page: only the models they keep
  onOpen: (runId: string) => void;
}) {
  const { t } = useTranslation();
  const languages = historyLanguages(runs);
  const [picked, setPicked] = useState<string>();
  const language =
    (picked && languages.includes(picked) ? picked : undefined) ??
    (preferred && languages.includes(preferred) ? preferred : undefined) ??
    languages[0];
  const rows = (language ? rowsOfHistory(runs, language) : []).filter((row) =>
    passes(row.model, filters),
  );
  const name = (code: string): string => t(`bench.language.${code}`, { defaultValue: code });
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle className="text-base">{t("bench.all.title")}</CardTitle>
        {languages.length > 1 && language ? (
          <Select value={language} onValueChange={setPicked}>
            <SelectTrigger className="w-56 max-w-full" aria-label={t("bench.all.language")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {languages.map((code) => (
                <SelectItem key={code} value={code}>
                  {t("bench.all.asked", { language: name(code) })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-8">
        {language ? (
          <>
            <div className="grid gap-1.5">
              <p className="text-muted-foreground text-sm">
                {t("bench.all.intro", { count: rows.length, language: name(language) })}
              </p>
              {rows.length === 0 && filtering(filters) ? (
                <p className="text-muted-foreground text-sm">{t("bench.filters.noRows")}</p>
              ) : null}
              {mixedImages(rows) ? (
                <p className="text-warning-ink text-sm">{t("bench.all.mixed")}</p>
              ) : null}
            </div>
            <Ranking rows={rows} />
            <BenchCharts rows={rows} />
            {rows.length > 0 ? <ResultsTable rows={rows} onOpen={onOpen} /> : null}
          </>
        ) : (
          <p className="text-muted-foreground text-sm">{t("bench.all.empty")}</p>
        )}
      </CardContent>
    </Card>
  );
}
