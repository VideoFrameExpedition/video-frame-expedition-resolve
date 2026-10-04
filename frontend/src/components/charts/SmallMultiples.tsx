import { useMemo, useState, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";

import { formatClock } from "@/lib/format";

import {
  areaPath,
  clampTo,
  linearScale,
  linePath,
  medianStep,
  nearestIndex,
  niceTicks,
  type LinearScale,
} from "./scale";
import { useElementWidth } from "./useElementWidth";

/** One time series drawn in its own panel (small multiples share the time axis, never a y axis). */
export interface SeriesSpec {
  key: string;
  title: string;
  /** Unit or short explanation shown after the title. */
  hint?: string;
  t: readonly number[];
  /** Raw values (readouts and the table show them as they are); ``null`` breaks the line. */
  values: readonly (number | null)[];
  /** Fixed y domain, or ``undefined`` to use round ticks around the data. */
  domain?: readonly [number, number];
  /** Values are limited to this range when drawn only, so outliers stay inside the plot. */
  clamp?: readonly [number, number];
  /** Formats readouts (and y ticks unless ``tickFormat`` is given). */
  format: (value: number) => string;
  tickFormat?: (value: number) => string;
  /** Horizontal reference with a direct label (e.g. integrated loudness). */
  reference?: { value: number; label: string };
  /** Shaded time ranges (e.g. silences) with their legend label. */
  bands?: { ranges: readonly number[][]; label: string };
}

const PLOT_HEIGHT = 64;
const AXIS_BAND = 18;
const GUTTER = 48; // y tick labels
const PAD_TOP = 6;

interface Props {
  series: SeriesSpec[];
  duration: number;
  /** Current playback time (quantised by the caller to avoid 60 Hz re-renders). */
  playhead: number;
  onSeek: (t: number) => void;
}

/** Raw value of the sample nearest to ``t``; ``spacing`` is the series' typical sample step. */
function valueAt(spec: SeriesSpec, t: number, spacing: number): number | null {
  const index = nearestIndex(spec.t, t);
  const sampleT = spec.t[index];
  if (sampleT === undefined) {
    return null;
  }
  // Farther than two samples apart: no value at that time (gap, or series stops early).
  return Math.abs(sampleT - t) <= Math.max(spacing * 2, 1.01) ? (spec.values[index] ?? null) : null;
}

interface PanelScale {
  y: LinearScale;
  ticks: number[];
  /** Pixel position of a value, limited to ``spec.clamp`` so drawing stays inside the plot. */
  plotY: (value: number) => number;
}

function yScaleFor(spec: SeriesSpec): PanelScale {
  const clamp = spec.clamp;
  let domain = spec.domain;
  let ticks: number[];
  if (domain) {
    const [lo, hi] = domain;
    ticks = niceTicks(lo, hi, 3).filter((v) => v >= lo && v <= hi);
  } else {
    const finite = spec.values
      .filter((v): v is number => v !== null && Number.isFinite(v))
      .map((v) => (clamp ? clampTo(v, clamp) : v));
    const lo = finite.length ? Math.min(...finite) : 0;
    const hi = finite.length ? Math.max(...finite) : 1;
    ticks = niceTicks(lo, hi, 2);
    domain = [ticks[0] ?? lo, ticks[ticks.length - 1] ?? hi];
  }
  const y = linearScale(domain, [PAD_TOP + PLOT_HEIGHT, PAD_TOP]);
  return { y, ticks, plotY: clamp ? (value) => y(clampTo(value, clamp)) : y };
}

/**
 * A horizontal reference: its line only when the value is inside the y domain (else it would be
 * drawn outside the plot), its label pinned to the nearest edge with an arrow when off scale, and
 * placed below the line when there is no room above it.
 */
function referenceMark(
  value: number,
  y: LinearScale,
): { y: number; inside: boolean; labelY: number; arrow: string } {
  const lo = Math.min(...y.domain);
  const hi = Math.max(...y.domain);
  const at = y(clampTo(value, y.domain));
  return {
    y: at,
    inside: value >= lo && value <= hi,
    labelY: at < PAD_TOP + 12 ? at + 12 : at - 4,
    arrow: value < lo ? "↓ " : value > hi ? "↑ " : "",
  };
}

export function SmallMultiples({ series, duration, playhead, onSeek }: Props) {
  const { t } = useTranslation();
  const [measureRef, width] = useElementWidth(720);
  const [hoverT, setHoverT] = useState<number | null>(null);
  const x = useMemo(
    () => linearScale([0, Math.max(duration, 0.001)], [GUTTER, Math.max(GUTTER + 1, width - 4)]),
    [duration, width],
  );
  const scales = useMemo(() => series.map(yScaleFor), [series]);
  const steps = useMemo(() => series.map((spec) => medianStep(spec.t)), [series]);
  const readoutT = hoverT ?? playhead;
  const axisTicks = useMemo(() => {
    const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800].find(
      (s) => duration / s <= Math.max(1, Math.floor((width - GUTTER) / 80)),
    );
    const out: number[] = [];
    for (let v = 0; step && v <= duration + 1e-6; v += step) out.push(v);
    return out;
  }, [duration, width]);

  const timeFromEvent = (event: MouseEvent<SVGSVGElement>): number => {
    const rect = event.currentTarget.getBoundingClientRect();
    return Math.max(0, Math.min(duration, x.invert(event.clientX - rect.left)));
  };

  return (
    <div ref={measureRef} className="grid gap-1">
      <p className="text-muted-foreground font-mono text-xs tabular-nums" aria-live="off">
        {hoverT === null
          ? t("charts.atPlayhead", { time: formatClock(readoutT, true) })
          : t("charts.atTime", { time: formatClock(readoutT, true) })}
      </p>
      {series.map((spec, index) => {
        const scale = scales[index];
        if (!scale) return null;
        const { y, ticks, plotY } = scale;
        const last = index === series.length - 1;
        const height = PAD_TOP + PLOT_HEIGHT + (last ? AXIS_BAND : 4);
        const readout = valueAt(spec, readoutT, steps[index] ?? 1);
        const baseline = PAD_TOP + PLOT_HEIGHT;
        const reference = spec.reference
          ? { label: spec.reference.label, ...referenceMark(spec.reference.value, y) }
          : null;
        return (
          <figure key={spec.key} className="grid gap-0.5">
            <figcaption className="flex items-baseline justify-between gap-3 text-sm">
              <span>
                {spec.title}
                {spec.hint ? (
                  <span className="text-muted-foreground ml-1.5 text-xs">{spec.hint}</span>
                ) : null}
              </span>
              <span className="font-semibold tabular-nums">
                {readout === null ? "—" : spec.format(readout)}
              </span>
            </figcaption>
            <svg
              width={width}
              height={height}
              className="block cursor-crosshair touch-none select-none"
              onPointerMove={(event) => {
                setHoverT(timeFromEvent(event));
              }}
              onPointerLeave={() => {
                setHoverT(null);
              }}
              onClick={(event) => {
                onSeek(timeFromEvent(event));
              }}
              role="img"
              aria-label={t("charts.aria", { title: spec.title })}
            >
              {spec.bands?.ranges.map(([start = 0, end = 0]) => (
                <rect
                  key={`${start}-${end}`}
                  x={x(start)}
                  y={PAD_TOP}
                  width={Math.max(1, x(end) - x(start))}
                  height={PLOT_HEIGHT}
                  fill="var(--chart-neutral)"
                  opacity={0.2}
                />
              ))}
              {ticks.map((tick) => (
                <g key={tick}>
                  <line
                    x1={GUTTER}
                    x2={width}
                    y1={y(tick)}
                    y2={y(tick)}
                    stroke={tick === ticks[0] ? "var(--chart-axis)" : "var(--chart-grid)"}
                    strokeWidth={1}
                  />
                  <text
                    x={GUTTER - 6}
                    y={y(tick) + 3.5}
                    fontSize={10}
                    textAnchor="end"
                    fill="var(--muted-foreground)"
                    className="tabular-nums"
                  >
                    {(spec.tickFormat ?? spec.format)(tick)}
                  </text>
                </g>
              ))}
              <path
                d={areaPath(spec.t, spec.values, x, plotY, baseline)}
                fill="var(--foreground)"
                opacity={0.07}
              />
              <path
                d={linePath(spec.t, spec.values, x, plotY)}
                fill="none"
                stroke="var(--foreground)"
                strokeOpacity={0.85}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {reference ? (
                <g>
                  {reference.inside ? (
                    <line
                      x1={GUTTER}
                      x2={width}
                      y1={reference.y}
                      y2={reference.y}
                      stroke="var(--muted-foreground)"
                      strokeWidth={1}
                    />
                  ) : null}
                  <text
                    x={width - 4}
                    y={reference.labelY}
                    fontSize={10}
                    textAnchor="end"
                    fill="var(--muted-foreground)"
                    stroke="var(--card)"
                    strokeWidth={3}
                    paintOrder="stroke"
                  >
                    {reference.arrow}
                    {reference.label}
                  </text>
                </g>
              ) : null}
              <line
                x1={x(playhead)}
                x2={x(playhead)}
                y1={PAD_TOP}
                y2={baseline}
                stroke="var(--primary)"
                strokeWidth={1.5}
              />
              {hoverT !== null ? (
                <g className="pointer-events-none">
                  <line
                    x1={x(hoverT)}
                    x2={x(hoverT)}
                    y1={PAD_TOP}
                    y2={baseline}
                    stroke="var(--foreground)"
                    strokeOpacity={0.35}
                    strokeWidth={1}
                  />
                  {readout !== null ? (
                    <circle
                      cx={x(hoverT)}
                      cy={plotY(readout)}
                      r={4}
                      fill="var(--foreground)"
                      stroke="var(--card)"
                      strokeWidth={2}
                    />
                  ) : null}
                </g>
              ) : null}
              {last
                ? axisTicks.map((tick, i) => (
                    <text
                      key={tick}
                      x={x(tick)}
                      y={baseline + 13}
                      fontSize={10}
                      fill="var(--muted-foreground)"
                      textAnchor={i === 0 ? "start" : "middle"}
                      className="tabular-nums"
                    >
                      {formatClock(tick)}
                    </text>
                  ))
                : null}
            </svg>
            {spec.bands && spec.bands.ranges.length > 0 ? (
              <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
                <span
                  className="bg-chart-neutral/25 inline-block h-2.5 w-3.5 rounded-sm"
                  aria-hidden
                />
                {spec.bands.label}
              </p>
            ) : null}
          </figure>
        );
      })}
    </div>
  );
}

/** Accessible twin of the charts: one row per ``step`` seconds, every series in a column. */
export function SeriesTable({
  series,
  duration,
  step,
  onSeek,
}: {
  series: SeriesSpec[];
  duration: number;
  step: number;
  onSeek: (t: number) => void;
}) {
  const { t } = useTranslation();
  const steps = useMemo(() => series.map((spec) => medianStep(spec.t)), [series]);
  const rows: number[] = [];
  for (let time = 0; time <= duration + 1e-6; time += step) rows.push(time);
  return (
    <div className="max-h-96 overflow-auto rounded-md border">
      <table className="w-full text-sm">
        <caption className="sr-only">{t("charts.tableCaption")}</caption>
        <thead className="bg-muted sticky top-0">
          <tr>
            <th scope="col" className="px-3 py-1.5 text-left font-medium">
              {t("charts.time")}
            </th>
            {series.map((spec) => (
              <th key={spec.key} scope="col" className="px-3 py-1.5 text-right font-medium">
                {spec.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {rows.map((time) => (
            <tr key={time} className="hover:bg-muted/60 border-t">
              <th scope="row" className="px-3 py-1 text-left font-normal">
                <button
                  type="button"
                  className="text-primary font-mono hover:underline"
                  onClick={() => {
                    onSeek(time);
                  }}
                >
                  {formatClock(time)}
                </button>
              </th>
              {series.map((spec, index) => {
                const value = valueAt(spec, time, steps[index] ?? 1);
                return (
                  <td key={spec.key} className="px-3 py-1 text-right">
                    {value === null ? "—" : spec.format(value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
