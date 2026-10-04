import { useTranslation } from "react-i18next";

import { useElementWidth } from "@/components/charts/useElementWidth";

import { DAY_PHASES, type PhaseSegment } from "./format";
import { PHASE_FILL } from "./phaseColors";

const HEIGHT = 28;
const GAP = 2; // surface gap between adjacent bands (never colour alone)

function clock(minute: number): string {
  const h = Math.floor(minute / 60) % 24;
  return `${h.toString().padStart(2, "0")}:${(minute % 60).toString().padStart(2, "0")}`;
}

/**
 * The capture's local day as bands of light phases, with the capture instant (and the ± 30 min
 * window when the time is only probable, or the length of a long take).
 */
export function DayStrip({
  segments,
  capture,
  windowMinutes,
  takeMinutes,
}: {
  segments: PhaseSegment[];
  capture: number | null; // minutes since local midnight
  windowMinutes: number | null; // ± minutes when the capture time is only probable
  takeMinutes: number | null; // length of a long take
}) {
  const { t } = useTranslation();
  const [ref, width] = useElementWidth();
  const x = (minute: number) => (Math.max(0, Math.min(1440, minute)) / 1440) * width;
  const present = DAY_PHASES.filter((phase) => segments.some((s) => s.phase === phase));
  // Marks are offsets from the capture minute, clamped to this local day (no wrap-around).
  const window =
    capture !== null && windowMinutes !== null
      ? ([capture - windowMinutes, capture + windowMinutes] as const)
      : null;
  const takeEnd = capture !== null && takeMinutes !== null ? capture + takeMinutes : null;
  const phases = segments
    .map((s) => `${t(`context.phase.${s.phase}`)} ${clock(s.start)}–${clock(s.end)}`)
    .join(", ");
  const marks = [
    capture !== null ? t("context.captureAt", { time: clock(capture) }) : null,
    window
      ? t("context.windowAt", {
          from: clock(Math.max(0, window[0])),
          to: clock(Math.min(1439, window[1])),
        })
      : null,
    takeEnd !== null ? t("context.takeUntil", { time: clock(Math.min(1439, takeEnd)) }) : null,
  ].filter(Boolean);
  const description = [phases, ...marks].join(" ; ");

  return (
    <figure className="grid gap-2">
      <div ref={ref} className="relative">
        {width > 0 ? (
          <svg
            width={width}
            height={HEIGHT + 18}
            role="img"
            aria-label={t("context.dayStripLabel", { description })}
            className="block overflow-visible"
          >
            <rect
              x={0.5}
              y={0.5}
              width={width - 1}
              height={HEIGHT - 1}
              rx={4}
              className="fill-card stroke-border"
            />
            {segments.map((segment) => {
              const left = x(segment.start) + (segment.start > 0 ? GAP / 2 : 1);
              const right = x(segment.end) - (segment.end < 1440 ? GAP / 2 : 1);
              if (right - left <= 0) return null;
              return (
                <rect
                  key={`${segment.phase}-${segment.start}`}
                  x={left}
                  y={1}
                  width={right - left}
                  height={HEIGHT - 2}
                  rx={3}
                  style={{ fill: PHASE_FILL[segment.phase] }}
                >
                  <title>
                    {`${t(`context.phase.${segment.phase}`)} · ${clock(segment.start)}–${clock(segment.end)}`}
                  </title>
                </rect>
              );
            })}
            {window ? (
              <rect
                x={x(window[0])}
                y={-3}
                width={Math.max(2, x(window[1]) - x(window[0]))}
                height={HEIGHT + 6}
                rx={3}
                className="fill-foreground/15 stroke-foreground"
                strokeDasharray="3 2"
              >
                <title>{t("context.window")}</title>
              </rect>
            ) : null}
            {takeEnd !== null && capture !== null ? (
              <rect
                x={x(capture)}
                y={HEIGHT / 2 - 3}
                width={Math.max(2, x(takeEnd) - x(capture))}
                height={6}
                rx={3}
                className="fill-foreground"
              >
                <title>{t("context.take")}</title>
              </rect>
            ) : null}
            {capture !== null ? (
              <g>
                <line
                  x1={x(capture)}
                  x2={x(capture)}
                  y1={-4}
                  y2={HEIGHT + 4}
                  className="stroke-foreground"
                  strokeWidth={2}
                />
                <circle cx={x(capture)} cy={-4} r={4} className="fill-foreground stroke-card" />
              </g>
            ) : null}
            {[0, 360, 720, 1080, 1440].map((minute) => (
              <text
                key={minute}
                x={x(minute)}
                y={HEIGHT + 14}
                textAnchor={minute === 0 ? "start" : minute === 1440 ? "end" : "middle"}
                className="fill-muted-foreground text-[10px] tabular-nums"
              >
                {clock(minute)}
              </text>
            ))}
          </svg>
        ) : (
          <div style={{ height: HEIGHT + 18 }} />
        )}
      </div>
      <figcaption>
        <ul className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {present.map((phase) => (
            <li key={phase} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="border-border inline-block size-2.5 rounded-sm border"
                style={{ background: PHASE_FILL[phase] }}
              />
              {t(`context.phase.${phase}`)}
            </li>
          ))}
          {capture !== null ? (
            <li className="flex items-center gap-1.5">
              <span aria-hidden className="bg-foreground inline-block h-3 w-0.5" />
              {t("context.captureMark")}
            </li>
          ) : null}
        </ul>
      </figcaption>
    </figure>
  );
}
