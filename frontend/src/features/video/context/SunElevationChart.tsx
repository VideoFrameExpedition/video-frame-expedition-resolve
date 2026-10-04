import { useState, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "@/api/client";
import { linePath } from "@/components/charts/scale";
import { useElementWidth } from "@/components/charts/useElementWidth";
import { formatNumber } from "@/lib/format";

export type SunCurve = Schemas["SunCurveOut"];

const HEIGHT = 170;
const PAD = { top: 22, right: 8, bottom: 20, left: 34 };
// Sun elevation bands (degrees) of the photographic phases, as in domain/sun.py (PhotoPills).
const GOLDEN: readonly [number, number] = [-4, 6];
const BLUE: readonly [number, number] = [-6, -4];

function clock(minute: number): string {
  const m = ((Math.round(minute) % 1440) + 1440) % 1440;
  return `${Math.floor(m / 60)
    .toString()
    .padStart(2, "0")}:${(m % 60).toString().padStart(2, "0")}`;
}

/** Elevations as (minute of the local day, degrees) points. */
function curvePoints(curve: SunCurve): [number, number][] {
  return curve.elevations_deg.map((e, i) => [i * curve.step_min, e]);
}

/**
 * Height of the sun through the capture day, with the golden and blue hour bands and the
 * capture instant: at a glance, whether a shot is a sunrise, a sunset or broad daylight.
 */
export function SunElevationChart({
  curve,
  capture,
  captureElevation,
}: {
  curve: SunCurve;
  capture: number | null; // minutes since local midnight
  captureElevation: number;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const [ref, width] = useElementWidth();
  const [hover, setHover] = useState<number | null>(null);
  const points = curvePoints(curve);
  const values = points.map(([, e]) => e);
  const peak = points.reduce((best, p) => (p[1] > best[1] ? p : best), points[0] ?? [0, 0]);
  const low = Math.max(-30, Math.floor(Math.min(...values, -18) / 10) * 10);
  const high = Math.max(30, Math.ceil((Math.max(...values) + 5) / 10) * 10);
  const plotW = Math.max(0, width - PAD.left - PAD.right);
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (minute: number) => PAD.left + (Math.max(0, Math.min(1440, minute)) / 1440) * plotW;
  const y = (deg: number) =>
    PAD.top + ((high - Math.max(low, Math.min(high, deg))) / (high - low)) * plotH;
  // Horizon, astronomical night and round heights (the frame edges are not labelled).
  const ticks = [-18, 0, 30, 60].filter((d) => d >= low && d <= high);
  const deg = (value: number) => `${formatNumber(value, locale, 1)}°`;
  const description = [
    t("context.curve.peak", { value: deg(peak[1]), time: clock(peak[0]) }),
    capture !== null
      ? t("context.curve.capture", { value: deg(captureElevation), time: clock(capture) })
      : null,
  ]
    .filter(Boolean)
    .join(" ; ");

  const onMove = (event: PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const minute = ((event.clientX - box.left) / Math.max(1, box.width)) * 1440;
    setHover(Math.round(minute / curve.step_min));
  };
  const hovered = hover !== null ? points[Math.max(0, Math.min(points.length - 1, hover))] : null;

  return (
    <figure className="grid gap-2">
      <figcaption className="text-muted-foreground text-xs">{t("context.curve.title")}</figcaption>
      <div ref={ref} className="relative">
        {width > 0 ? (
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            aria-label={t("context.curve.label", { description })}
            className="block"
          >
            <rect
              x={PAD.left}
              y={y(GOLDEN[1])}
              width={plotW}
              height={y(GOLDEN[0]) - y(GOLDEN[1])}
              style={{ fill: "var(--phase-golden)" }}
              opacity={0.28}
            />
            <rect
              x={PAD.left}
              y={y(BLUE[1])}
              width={plotW}
              height={y(BLUE[0]) - y(BLUE[1])}
              style={{ fill: "var(--phase-blue)" }}
              opacity={0.35}
            />
            {ticks.map((tick) => (
              <g key={tick}>
                <line
                  x1={PAD.left}
                  x2={PAD.left + plotW}
                  y1={y(tick)}
                  y2={y(tick)}
                  stroke={tick === 0 ? "var(--chart-axis)" : "var(--chart-grid)"}
                  strokeWidth={tick === 0 ? 1.5 : 1}
                />
                <text
                  x={PAD.left - 6}
                  y={y(tick)}
                  dy="0.32em"
                  textAnchor="end"
                  className="fill-muted-foreground text-[10px] tabular-nums"
                >
                  {`${tick}°`}
                </text>
              </g>
            ))}
            <text
              x={PAD.left + plotW - 2}
              y={y(0) - 4}
              textAnchor="end"
              className="fill-muted-foreground text-[10px]"
            >
              {t("context.curve.horizon")}
            </text>
            {[0, 360, 720, 1080, 1440].map((minute) => (
              <text
                key={minute}
                x={x(minute)}
                y={HEIGHT - 4}
                textAnchor={minute === 0 ? "start" : minute === 1440 ? "end" : "middle"}
                className="fill-muted-foreground text-[10px] tabular-nums"
              >
                {minute === 1440 ? "24:00" : clock(minute)}
              </text>
            ))}
            <path
              d={linePath(
                points.map(([m]) => m),
                values,
                x,
                y,
              )}
              fill="none"
              stroke="var(--chart-1)"
              strokeWidth={2}
              strokeLinejoin="round"
            />
            {capture !== null ? (
              <g>
                <line
                  x1={x(capture)}
                  x2={x(capture)}
                  y1={y(captureElevation)}
                  y2={PAD.top + plotH}
                  stroke="var(--foreground)"
                  strokeDasharray="3 3"
                />
                <circle
                  cx={x(capture)}
                  cy={y(captureElevation)}
                  r={5}
                  fill="var(--foreground)"
                  stroke="var(--card)"
                  strokeWidth={2}
                />
                <text
                  x={x(capture)}
                  y={y(captureElevation) - 10}
                  textAnchor={
                    x(capture) > width - 90 ? "end" : x(capture) < 90 ? "start" : "middle"
                  }
                  className="fill-foreground text-[11px] font-medium tabular-nums"
                >
                  {`${clock(capture)} · ${deg(captureElevation)}`}
                </text>
              </g>
            ) : null}
            {hovered ? (
              <g pointerEvents="none">
                <line
                  x1={x(hovered[0])}
                  x2={x(hovered[0])}
                  y1={PAD.top}
                  y2={PAD.top + plotH}
                  stroke="var(--muted-foreground)"
                />
                <circle
                  cx={x(hovered[0])}
                  cy={y(hovered[1])}
                  r={4}
                  fill="var(--chart-1)"
                  stroke="var(--card)"
                  strokeWidth={2}
                />
              </g>
            ) : null}
            <rect
              x={PAD.left}
              y={PAD.top}
              width={plotW}
              height={plotH}
              fill="transparent"
              onPointerMove={onMove}
              onPointerLeave={() => {
                setHover(null);
              }}
            />
          </svg>
        ) : (
          <div style={{ height: HEIGHT }} />
        )}
        {hovered ? (
          <div
            role="status"
            className="bg-popover text-popover-foreground pointer-events-none absolute top-0 rounded-md border px-2 py-1 text-xs shadow-md tabular-nums"
            style={{
              left: Math.min(Math.max(0, x(hovered[0]) - 60), Math.max(0, width - 120)),
            }}
          >
            {`${clock(hovered[0])} · ${deg(hovered[1])}`}
          </div>
        ) : null}
      </div>
      <ul className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-xs">
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-0.5 w-4"
            style={{ background: "var(--chart-1)" }}
          />
          {t("context.curve.series")}
        </li>
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="border-border inline-block size-2.5 rounded-sm border"
            style={{ background: "var(--phase-golden)" }}
          />
          {t("context.curve.goldenBand")}
        </li>
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="border-border inline-block size-2.5 rounded-sm border"
            style={{ background: "var(--phase-blue)" }}
          />
          {t("context.curve.blueBand")}
        </li>
      </ul>
    </figure>
  );
}
