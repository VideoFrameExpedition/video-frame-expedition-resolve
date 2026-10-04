import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "@/api/client";
import { cn } from "@/lib/utils";
import { formatSpan } from "@/lib/format";

type Summary = Schemas["JobsSummaryOut"];

function Tile({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: number;
  detail?: ReactNode;
  tone?: "active" | "danger" | undefined;
}) {
  return (
    <div className="bg-card grid content-start gap-1 rounded-xl border p-4">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span
        className={cn(
          "text-2xl font-semibold tabular-nums",
          tone === "active" && value > 0 && "text-brand-teal",
          tone === "danger" && value > 0 && "text-destructive",
        )}
      >
        {value}
      </span>
      {detail ? <span className="text-muted-foreground text-xs">{detail}</span> : null}
    </div>
  );
}

/** The jobs at a glance: running, waiting (and the time the analyses still need), the last
 * day's outcome. */
export function JobsSummary({ summary }: { summary: Summary }) {
  const { t } = useTranslation();
  const done = (summary.finished.succeeded ?? 0) + (summary.finished.partial ?? 0);
  const partial = summary.finished.partial ?? 0;
  const failed = summary.finished.failed ?? 0;
  const cancelled = summary.finished.cancelled ?? 0;
  return (
    <section aria-label={t("jobs.summary.label")} className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile label={t("jobs.summary.running")} value={summary.running} tone="active" />
      <Tile
        label={t("jobs.summary.queued")}
        value={summary.queued}
        detail={
          summary.eta_s ? (
            <span
              title={t("jobs.summary.etaHint", {
                pace: formatSpan(summary.analysis_s),
                count: summary.parallel,
              })}
            >
              {t("jobs.summary.eta", { span: formatSpan(summary.eta_s) })}
            </span>
          ) : undefined
        }
      />
      <Tile
        label={t("jobs.summary.done")}
        value={done}
        detail={partial > 0 ? t("jobs.summary.partial", { count: partial }) : undefined}
      />
      <Tile
        label={t("jobs.summary.failed")}
        value={failed}
        tone="danger"
        detail={cancelled > 0 ? t("jobs.summary.cancelled", { count: cancelled }) : undefined}
      />
    </section>
  );
}
