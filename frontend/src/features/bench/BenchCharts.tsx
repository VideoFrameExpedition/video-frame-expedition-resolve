import { useTranslation } from "react-i18next";

import { linearScale, logTicks, niceTicks } from "@/components/charts/scale";
import { useElementWidth } from "@/components/charts/useElementWidth";
import { formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  best,
  gigabytes,
  MEASURES,
  placeLabels,
  same,
  type BenchRow,
  type MeasureId,
} from "./benchFormat";

const HEIGHT = 320;
const PAD = { top: 26, right: 18, bottom: 42, left: 44 };
const CHAR_PX = 6.3; // an 11 px label, on average
const MAX_RATING = 3;
// A model this many times slower than the fastest (one that spills into the system memory)
// would crush the others at the bottom of a linear axis: the time axis becomes logarithmic.
const LOG_FROM = 8;

function cardGigabytes(rows: BenchRow[]): number | null {
  const totals = rows
    .map((row) => gigabytes(row.model.scores.vram_total_mib))
    .filter((value): value is number => value !== null);
  return totals.length > 0 ? Math.max(...totals) : null;
}

/** Memory against speed, one point per model: the corner to look for is the bottom left one
 * (little memory, little time per image). */
function MemorySpeedChart({ rows }: { rows: BenchRow[] }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const gigabyte = t("bench.unit.gb");
  const [ref, width] = useElementWidth();
  const points = rows.flatMap((row) => {
    const memory = MEASURES.vram.value(row);
    const seconds = MEASURES.speed.value(row);
    return memory !== null && typeof seconds === "number" ? [{ row, memory, seconds }] : [];
  });
  if (points.length === 0) {
    return null;
  }
  const card = cardGigabytes(rows);
  const xTicks = niceTicks(0, Math.max(card ?? 0, ...points.map((point) => point.memory)), 6);
  const slowest = Math.max(...points.map((point) => point.seconds));
  const fastest = Math.min(...points.map((point) => point.seconds));
  const log = fastest > 0 && slowest / fastest >= LOG_FROM;
  const yTicks = log ? logTicks(fastest, slowest) : niceTicks(0, slowest, 5);
  const depth = (seconds: number): number => (log ? Math.log10(seconds) : seconds);
  const x = linearScale([0, xTicks.at(-1) ?? 1], [PAD.left, Math.max(PAD.left, width - PAD.right)]);
  const [bottom, top] = [HEIGHT - PAD.bottom, PAD.top];
  const height = linearScale([depth(yTicks[0] ?? 1), depth(yTicks.at(-1) ?? 1)], [bottom, top]);
  const y = (seconds: number): number => height(depth(seconds));
  const [left, right] = x.range;
  const spots = placeLabels(
    points.map((point) => ({
      x: x(point.memory),
      y: y(point.seconds),
      width: point.row.label.length * CHAR_PX + 2,
    })),
    { left, right: width, top: 0, bottom },
  );
  const describe = ({ row, memory, seconds }: (typeof points)[number]): string =>
    [
      t("bench.charts.scatter.point", {
        name: row.label,
        memory: formatNumber(memory, locale, 1, gigabyte),
        seconds: formatNumber(seconds, locale, 1),
      }),
      row.model.scores.vram_full ? t("bench.results.vramFull") : null,
      row.rating.mean === null
        ? null
        : t("bench.charts.scatter.quality", { value: formatNumber(row.rating.mean, locale, 2) }),
    ]
      .filter(Boolean)
      .join(", ");
  return (
    <figure className="grid gap-2">
      <figcaption>
        <span className="text-sm font-medium">{t("bench.charts.scatter.title")}</span>
        <span className="text-muted-foreground block text-xs">
          {t("bench.charts.scatter.hint")}
        </span>
      </figcaption>
      <div ref={ref}>
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={t("bench.charts.scatter.label", {
            points: points.map(describe).join(" ; "),
          })}
          className="block"
        >
          {yTicks.map((tick) => (
            <g key={tick}>
              <line
                x1={left}
                x2={right}
                y1={y(tick)}
                y2={y(tick)}
                stroke={tick === yTicks[0] ? "var(--chart-axis)" : "var(--chart-grid)"}
              />
              <text
                x={left - 6}
                y={y(tick)}
                dy="0.32em"
                textAnchor="end"
                className="fill-muted-foreground text-[10px] tabular-nums"
              >
                {formatNumber(tick, locale)}
              </text>
            </g>
          ))}
          {xTicks.map((tick) => (
            <g key={tick}>
              <line
                x1={x(tick)}
                x2={x(tick)}
                y1={top}
                y2={bottom}
                stroke={tick === 0 ? "var(--chart-axis)" : "var(--chart-grid)"}
              />
              <text
                x={x(tick)}
                y={bottom + 14}
                textAnchor="middle"
                className="fill-muted-foreground text-[10px] tabular-nums"
              >
                {formatNumber(tick, locale)}
              </text>
            </g>
          ))}
          <text x={left} y={12} className="fill-muted-foreground text-[10px]">
            {t(log ? "bench.charts.scatter.yLog" : "bench.charts.scatter.y")}
          </text>
          <text
            x={right}
            y={HEIGHT - 6}
            textAnchor="end"
            className="fill-muted-foreground text-[10px]"
          >
            {t("bench.charts.scatter.x", { unit: gigabyte })}
          </text>
          {card !== null ? (
            <g>
              <line
                x1={x(card)}
                x2={x(card)}
                y1={top}
                y2={bottom}
                stroke="var(--chart-2)"
                strokeDasharray="4 3"
              />
              <text
                x={x(card) - 5}
                y={top + 10}
                textAnchor="end"
                className="fill-muted-foreground text-[10px]"
              >
                {t("bench.charts.scatter.card", {
                  value: formatNumber(card, locale, 0, gigabyte),
                })}
              </text>
            </g>
          ) : null}
          {points.map((point, index) => {
            const spot = spots[index];
            const full = point.row.model.scores.vram_full;
            return (
              <g key={point.row.id}>
                <circle
                  cx={x(point.memory)}
                  cy={y(point.seconds)}
                  r={5}
                  fill={full ? "var(--chart-2)" : "var(--chart-1)"}
                  stroke="var(--background)"
                  strokeWidth={1.5}
                >
                  <title>{describe(point)}</title>
                </circle>
                {spot ? (
                  <text
                    x={spot.x}
                    y={spot.y}
                    textAnchor={spot.anchor}
                    className="fill-foreground text-[11px]"
                  >
                    {point.row.label}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>
    </figure>
  );
}

const PANELS: readonly MeasureId[] = ["vram", "speed", "language", "text", "positions", "quality"];

/** One measure, one bar per model, all on the same scale; the best one stands out. */
function MeasureBars({ id, rows }: { id: MeasureId; rows: BenchRow[] }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const measure = MEASURES[id];
  const values = rows.map((row) => measure.value(row) ?? null);
  const known = values.filter((value): value is number => value !== null);
  if (known.length === 0) {
    return null;
  }
  const whole = {
    vram: cardGigabytes(rows) ?? Math.max(...known),
    speed: Math.max(...known),
    valid: 1,
    language: 1,
    text: 1,
    positions: 1,
    quality: MAX_RATING,
  }[id];
  const write = (value: number): string =>
    id === "vram"
      ? formatNumber(value, locale, 1, t("bench.unit.gb"))
      : id === "speed"
        ? formatNumber(value, locale, 1, "s")
        : id === "quality"
          ? formatNumber(value, locale, 2)
          : formatPercent(value);
  const top = best(rows, measure.value, measure.better);
  return (
    <figure
      aria-label={t(`bench.charts.bars.${id}`)}
      className="grid content-start gap-2 rounded-xl border p-3"
    >
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-sm font-medium">{t(`bench.charts.bars.${id}`)}</span>
        <span className="text-muted-foreground text-xs">
          {t(`bench.charts.bars.${measure.better === "min" ? "lower" : "higher"}`)}
        </span>
      </figcaption>
      <ul className="grid gap-1.5">
        {rows.map((row, index) => {
          const value = values[index] ?? null;
          const isBest = same(value, top);
          const off = id === "positions" && row.model.scores.positions_enabled === false;
          return (
            <li
              key={row.id}
              className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_4.75rem] items-center gap-2 text-xs"
            >
              <span className="truncate" title={row.label}>
                {row.label}
              </span>
              <span className="bg-secondary block h-2.5 overflow-hidden rounded-full" aria-hidden>
                {value !== null ? (
                  <span
                    className={cn(
                      "block h-full rounded-full",
                      isBest ? "bg-brand-teal" : "bg-chart-1",
                    )}
                    style={{ width: `${Math.max(1.5, Math.min(100, (value / whole) * 100))}%` }}
                  />
                ) : null}
              </span>
              <span
                className={cn(
                  "text-right tabular-nums",
                  isBest && "text-brand-teal font-semibold",
                  value === null && "text-muted-foreground",
                )}
              >
                {value !== null ? write(value) : off ? t("bench.charts.bars.off") : "—"}
              </span>
            </li>
          );
        })}
      </ul>
    </figure>
  );
}

/** The measures of the tested models as charts: memory against speed, then one bar chart per
 * measure. Models that could not be tested are left out. */
export function BenchCharts({ rows }: { rows: BenchRow[] }) {
  const { t } = useTranslation();
  const tested = rows.filter((row) => row.model.status === "done");
  if (tested.length === 0) {
    return null;
  }
  return (
    <section aria-label={t("bench.charts.title")} className="grid gap-4">
      <MemorySpeedChart rows={tested} />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {PANELS.map((id) => (
          <MeasureBars key={id} id={id} rows={tested} />
        ))}
      </div>
    </section>
  );
}
