import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { formatDateTime, formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  best,
  fileGigabytes,
  gigabytes,
  MEASURES,
  same,
  usualContext,
  type BenchRow,
} from "./benchFormat";
import { FamilyDot, ParamsChip, QuantChip } from "./CodeBadges";
import { familyOf } from "./modelCodes";

function Cell({
  main,
  detail,
  isBest = false,
  children,
}: {
  main: ReactNode;
  detail?: ReactNode;
  isBest?: boolean;
  children?: ReactNode;
}) {
  return (
    <td className="px-3 py-3 align-top">
      <span
        className={cn(
          "block whitespace-nowrap tabular-nums",
          isBest && "text-brand-teal font-semibold",
        )}
      >
        {main}
      </span>
      {detail ? <span className="text-muted-foreground block text-xs">{detail}</span> : null}
      {children}
    </td>
  );
}

/** What each model measured, one row per model; the best value of each column stands out.
 * ``onOpen``: the rows come from several runs, and each one leads to its own. */
export function ResultsTable({
  rows,
  onOpen,
}: {
  rows: BenchRow[];
  onOpen?: (runId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const gigabyte = t("bench.unit.gb");
  const bests = {
    vram: best(rows, MEASURES.vram.value, "min"),
    speed: best(rows, MEASURES.speed.value, "min"),
    valid: best(rows, MEASURES.valid.value, "max"),
    language: best(rows, MEASURES.language.value, "max"),
    text: best(rows, MEASURES.text.value, "max"),
    positions: best(rows, MEASURES.positions.value, "max"),
    quality: best(rows, MEASURES.quality.value, "max"),
  };
  const context = usualContext(rows);
  const columns = [
    "model",
    "vram",
    "speed",
    "answers",
    "language",
    "text",
    "positions",
    "quality",
  ] as const;

  const line = (row: BenchRow) => {
    const { model, rating, origin } = row;
    const s = model.scores;
    const facts = [
      formatNumber(fileGigabytes(model.size_bytes), locale, 1, gigabyte),
      model.context_length != null && context !== null && model.context_length !== context
        ? t("bench.results.context", { value: formatNumber(model.context_length, locale) })
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const family = familyOf(model);
    const name = (
      <th scope="row" className="px-3 py-3 text-left align-top font-medium">
        <span className="flex items-center gap-2" title={family || undefined}>
          <FamilyDot family={family} />
          {model.display_name}
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-1.5 text-xs font-normal">
          <ParamsChip params={model.params} />
          <QuantChip quantization={model.quantization} />
          <span className="text-muted-foreground">{facts}</span>
        </span>
        {origin && onOpen ? (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground block text-left text-xs font-normal underline-offset-2 hover:underline"
            onClick={() => {
              onOpen(origin.id);
            }}
          >
            {t("bench.all.origin", {
              date: formatDateTime(origin.created_at, locale),
              images: origin.images,
            })}
          </button>
        ) : null}
      </th>
    );
    if (model.status !== "done") {
      return (
        <tr key={row.id} className="border-t">
          {name}
          <td colSpan={columns.length - 1} className="px-3 py-3 align-top">
            <Badge variant="secondary">{t(`bench.modelStatus.${model.status}`)}</Badge>
            {model.error ? (
              <span className="text-muted-foreground ml-2 text-xs break-words">{model.error}</span>
            ) : null}
          </td>
        </tr>
      );
    }
    const answersDetail = [
      s.repaired > 0 ? t("bench.results.repaired", { count: s.repaired }) : null,
      s.truncated > 0 ? t("bench.results.truncated", { count: s.truncated }) : null,
      s.reasoning_tokens > 0 ? t("bench.results.reasons", { count: s.reasoning_tokens }) : null,
    ]
      .filter(Boolean)
      .join(" · ");
    return (
      <tr key={row.id} className="border-t">
        {name}
        <Cell
          main={formatNumber(gigabytes(s.vram_mib), locale, 1, gigabyte)}
          detail={
            s.vram_free_mib == null
              ? undefined
              : t("bench.results.vramLeft", {
                  value: formatNumber(gigabytes(s.vram_free_mib), locale, 1, gigabyte),
                })
          }
          isBest={same(MEASURES.vram.value(row), bests.vram)}
        >
          {s.vram_full ? (
            <span className="text-warning-ink block text-xs font-medium">
              {t("bench.results.vramFull")}
            </span>
          ) : null}
        </Cell>
        <Cell
          main={t("bench.results.perImage", {
            value: formatNumber(s.seconds_per_image, locale, 1),
          })}
          detail={
            s.seconds_per_position == null
              ? undefined
              : t("bench.results.perPosition", {
                  value: formatNumber(s.seconds_per_position, locale, 1),
                })
          }
          isBest={same(s.seconds_per_image, bests.speed)}
        />
        <Cell
          main={t("bench.results.outOf", { part: s.valid, whole: s.requests })}
          detail={answersDetail || undefined}
          isBest={same(MEASURES.valid.value(row), bests.valid)}
        />
        <Cell
          main={
            s.language_checked > 0
              ? t("bench.results.outOf", {
                  part: s.language_checked - s.wrong_language,
                  whole: s.language_checked,
                })
              : "—"
          }
          detail={
            s.foreign_script > 0
              ? t("bench.results.foreign", { count: s.foreign_script })
              : undefined
          }
          isBest={same(MEASURES.language.value(row), bests.language)}
        />
        <Cell
          main={
            s.text_recall == null
              ? "—"
              : t("bench.results.textRecall", { value: formatPercent(s.text_recall) })
          }
          detail={
            [
              s.text_frames > 0
                ? t("bench.results.textFrames", { count: s.text_frames })
                : t("bench.results.noText"),
              s.blank_frames > 0
                ? t("bench.results.unconfirmed", {
                    count: s.unconfirmed_text,
                    whole: s.blank_frames,
                  })
                : null,
            ]
              .filter(Boolean)
              .join(" · ") || undefined
          }
          isBest={same(s.text_recall, bests.text)}
        />
        {s.positions_enabled === false ? (
          <Cell
            main={t("bench.results.positionsOff")}
            detail={model.calibration?.reason ?? undefined}
          />
        ) : (
          <Cell
            main={
              s.position_recall == null
                ? "—"
                : t("bench.results.positionsRecall", { value: formatPercent(s.position_recall) })
            }
            detail={
              [
                s.position_beings > 0
                  ? t("bench.results.positionsDetail", {
                      count: s.position_beings,
                      iou: formatNumber(s.position_iou, locale, 2),
                    })
                  : t("bench.results.noBeings"),
                s.calibration_iou == null
                  ? null
                  : t("bench.results.calibration", {
                      iou: formatNumber(s.calibration_iou, locale, 2),
                    }),
              ]
                .filter(Boolean)
                .join(" · ") || undefined
            }
            isBest={same(s.position_recall, bests.positions)}
          />
        )}
        <Cell
          main={
            rating.mean === null
              ? "—"
              : t("bench.results.rating", { value: formatNumber(rating.mean, locale, 2) })
          }
          detail={
            rating.count > 0
              ? t("bench.results.rated", { count: rating.count })
              : t(onOpen ? "bench.results.notRatedThere" : "bench.results.notRated")
          }
          isBest={same(rating.mean, bests.quality)}
        />
      </tr>
    );
  };

  return (
    <div className="grid gap-4">
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[56rem] text-sm" aria-label={t("bench.results.title")}>
          <thead className="bg-secondary/50 text-muted-foreground text-xs">
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col" className="px-3 py-2 text-left font-medium">
                  {t(`bench.results.columns.${column}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{rows.map(line)}</tbody>
        </table>
      </div>
      <details className="text-muted-foreground text-xs">
        <summary className="cursor-pointer text-sm">{t("bench.results.help.title")}</summary>
        <ul className="mt-2 grid gap-1.5">
          {(["vram", "speed", "answers", "language", "text", "positions", "quality"] as const).map(
            (column) => (
              <li key={column}>{t(`bench.results.help.${column}`)}</li>
            ),
          )}
        </ul>
      </details>
    </div>
  );
}
