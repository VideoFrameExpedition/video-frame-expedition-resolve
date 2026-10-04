import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Keyframe, Shot, Signals } from "@/api/client";
import { EmptyState } from "@/components/common";
import { breakGaps } from "@/components/charts/scale";
import { SeriesTable, SmallMultiples, type SeriesSpec } from "@/components/charts/SmallMultiples";
import { StatTile } from "@/components/charts/StatTile";
import { Button } from "@/components/ui/button";
import { formatClock, formatNumber, formatPercent } from "@/lib/format";
import { kelvinKey, median } from "@/lib/kelvin";
import { cn } from "@/lib/utils";

import { motionFamily } from "./motion";
import { mergeColorShares, type ColorShare } from "./palette";
import { usePlayer, usePlayheadSelector } from "./player";

const TRUE_PEAK_LIMIT_DBTP = -1; // EBU R128 / streaming delivery ceiling
const LOUDNESS_RANGE: readonly [number, number] = [-60, 0]; // LUFS drawn; readouts stay raw
// Colour temperature is sampled once per second and omitted on dark frames: wider gaps are
// holes in the measurement, not something to bridge with a straight line.
const CCT_MAX_GAP_S = 1.5;

function useSeries(signals: Signals): SeriesSpec[] {
  const { t, i18n } = useTranslation();
  return useMemo(() => {
    const locale = i18n.language;
    const pct = (v: number): string => formatNumber(v, locale, 0, "%");
    const specs: SeriesSpec[] = [];
    const visual = signals.visual;
    if (visual) {
      const percent = (values: number[]): number[] => values.map((v) => v * 100);
      const cct = breakGaps(visual.cct.t, visual.cct.k, CCT_MAX_GAP_S);
      specs.push(
        {
          key: "luma",
          title: t("technique.series.luma"),
          t: visual.t,
          values: percent(visual.luma),
          domain: [0, 100],
          format: pct,
        },
        {
          key: "contrast",
          title: t("technique.series.contrast"),
          hint: t("technique.series.contrastHint"),
          t: visual.t,
          values: percent(visual.contrast),
          format: pct,
        },
        {
          key: "saturation",
          title: t("technique.series.saturation"),
          t: visual.t,
          values: percent(visual.saturation),
          domain: [0, 100],
          format: pct,
        },
        {
          key: "cct",
          title: t("technique.series.cct"),
          hint: t("technique.series.cctHint"),
          t: cct.t,
          values: cct.values,
          format: (v) => formatNumber(v, locale, 0, "K"),
        },
        {
          key: "motion",
          title: t("technique.series.motion"),
          hint: t("technique.series.motionHint"),
          t: visual.t,
          values: visual.motion,
          format: (v) => formatNumber(v, locale, 1),
        },
        {
          key: "sharpness",
          title: t("technique.series.sharpness"),
          hint: t("technique.series.sharpnessHint"),
          t: visual.t,
          values: visual.sharpness,
          format: (v) => formatNumber(v, locale, 0),
        },
      );
    }
    const audio = signals.audio;
    const stats = signals.audio_stats;
    if (audio) {
      const integrated = stats?.integrated_lufs;
      specs.push({
        key: "lufs",
        title: t("technique.series.loudness"),
        hint: t("technique.series.loudnessHint"),
        t: audio.t,
        values: audio.lufs,
        domain: LOUDNESS_RANGE,
        clamp: LOUDNESS_RANGE,
        format: (v) => formatNumber(v, locale, 0, "LUFS"),
        tickFormat: (v) => formatNumber(v, locale, 0),
        ...(integrated === null || integrated === undefined
          ? {}
          : {
              reference: {
                value: integrated,
                label: t("technique.integratedLabel", {
                  value: formatNumber(integrated, locale, 1),
                }),
              },
            }),
        ...(stats?.silences.length
          ? { bands: { ranges: stats.silences, label: t("timeline.silence") } }
          : {}),
      });
    }
    return specs;
  }, [signals, t, i18n.language]);
}

export function TechniqueTab({
  duration,
  shots,
  keyframes,
  signals,
  selectedId,
  onSelectFrame,
}: {
  duration: number;
  shots: Shot[];
  keyframes: Keyframe[];
  signals: Signals | undefined;
  selectedId: string | undefined;
  onSelectFrame: (frame: Keyframe) => void;
}) {
  const { t } = useTranslation();
  if (!signals || (!signals.visual && !signals.audio_stats)) {
    return <EmptyState title={t("technique.empty")} body={t("technique.emptyBody")} />;
  }
  return (
    <div className="grid gap-6">
      <Headline signals={signals} shots={shots} />
      <SignalsSection signals={signals} duration={duration} />
      <KeyframePalette frames={keyframes} selectedId={selectedId} onSelect={onSelectFrame} />
    </div>
  );
}

function Headline({ signals, shots }: { signals: Signals; shots: Shot[] }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const stats = signals.audio_stats;
  const cct = median(signals.visual?.cct.k ?? []);
  const luma = signals.visual ? median(signals.visual.luma) : null;
  const moving = shots.filter((shot) => motionFamily(shot.motion) !== "static").length;
  const peak = stats?.true_peak_dbfs;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatTile
        label={t("technique.kpi.loudness")}
        value={formatNumber(stats?.integrated_lufs, locale, 1, "LUFS")}
        note={t("technique.kpi.loudnessNote", {
          range: formatNumber(stats?.loudness_range_lu, locale, 1, "LU"),
        })}
      />
      <StatTile
        label={t("technique.kpi.peak")}
        value={formatNumber(peak, locale, 1, "dBTP")}
        warning={
          peak !== null && peak !== undefined && peak > TRUE_PEAK_LIMIT_DBTP
            ? t("technique.kpi.peakWarning")
            : undefined
        }
      />
      <StatTile
        label={t("technique.kpi.silence")}
        value={
          stats?.silence_ratio === null || stats?.silence_ratio === undefined
            ? "—"
            : formatPercent(stats.silence_ratio)
        }
        note={t("technique.kpi.silenceNote", { count: stats?.silences.length ?? 0 })}
      />
      <StatTile
        label={t("technique.kpi.cct")}
        value={formatNumber(cct, locale, 0, "K")}
        note={cct === null ? undefined : t(`kelvin.${kelvinKey(cct)}`)}
      />
      <StatTile label={t("technique.kpi.luma")} value={luma === null ? "—" : formatPercent(luma)} />
      <StatTile
        label={t("technique.kpi.shots")}
        value={shots.length}
        note={t("technique.kpi.shotsNote", { count: moving })}
      />
    </div>
  );
}

function SignalsSection({ signals, duration }: { signals: Signals; duration: number }) {
  const { t } = useTranslation();
  const { seek } = usePlayer();
  const [view, setView] = useState<"charts" | "table">("charts");
  const series = useSeries(signals);
  const playhead = usePlayheadSelector((time) => Math.round(time * 4) / 4);
  const step = Math.max(1, Math.ceil(duration / 600));
  return (
    <section className="grid gap-3" aria-labelledby="signals-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="signals-title" className="font-semibold">
          {t("technique.signals")}
        </h3>
        <div
          className="bg-muted inline-flex rounded-md p-0.5"
          role="group"
          aria-label={t("charts.view")}
        >
          {(["charts", "table"] as const).map((option) => (
            <Button
              key={option}
              size="sm"
              variant={view === option ? "secondary" : "ghost"}
              className={cn("h-7", view === option && "bg-background shadow-sm")}
              aria-pressed={view === option}
              onClick={() => {
                setView(option);
              }}
            >
              {t(`charts.${option}`)}
            </Button>
          ))}
        </div>
      </div>
      {view === "charts" ? (
        <SmallMultiples series={series} duration={duration} playhead={playhead} onSeek={seek} />
      ) : (
        <SeriesTable series={series} duration={duration} step={step} onSeek={seek} />
      )}
    </section>
  );
}

function KeyframePalette({
  frames,
  selectedId,
  onSelect,
}: {
  frames: Keyframe[];
  selectedId: string | undefined;
  onSelect: (frame: Keyframe) => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const measured = frames.filter((frame) => frame.metrics);
  if (measured.length === 0) {
    return null;
  }
  return (
    <section className="grid gap-3" aria-labelledby="palette-title">
      <h3 id="palette-title" className="font-semibold">
        {t("technique.palette")}
      </h3>
      <div className="max-h-[28rem] overflow-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted sticky top-0 z-10 text-left">
            <tr>
              <th scope="col" className="px-3 py-1.5 font-medium">
                {t("technique.col.frame")}
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                {t("technique.col.cct")}
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                {t("technique.col.luma")}
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                {t("technique.col.clipping")}
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                {t("technique.col.sharpness")}
              </th>
              <th scope="col" className="min-w-48 px-3 py-1.5 font-medium">
                {t("technique.col.colors")}
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {measured.map((frame) => {
              const m = frame.metrics;
              if (!m) return null;
              return (
                <tr
                  key={frame.id}
                  className={cn("border-t", frame.id === selectedId && "bg-accent/40")}
                >
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    <button
                      type="button"
                      className="group focus-visible:ring-ring flex items-center gap-2 rounded focus-visible:ring-2 focus-visible:outline-none"
                      onClick={() => {
                        onSelect(frame);
                      }}
                      aria-pressed={frame.id === selectedId}
                    >
                      <img
                        src={frame.thumb_url}
                        alt=""
                        loading="lazy"
                        className="h-9 w-16 rounded-sm bg-black object-contain"
                      />
                      <span className="text-muted-foreground font-mono text-xs group-hover:underline">
                        {formatClock(frame.t_s, true)}
                      </span>
                    </button>
                  </th>
                  <td className="px-3 py-1.5 text-right">
                    {m.cct_k === null ? (
                      "—"
                    ) : (
                      <>
                        {formatNumber(m.cct_k, locale, 0, "K")}
                        <span className="text-muted-foreground block text-xs whitespace-nowrap">
                          {t(`kelvin.${kelvinKey(m.cct_k)}`)}
                        </span>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right">{formatPercent(m.luma_mean)}</td>
                  <td className="px-3 py-1.5 text-right">
                    <span title={t("technique.clippingTitle")}>
                      {formatPercent(m.clipped_shadows)} / {formatPercent(m.clipped_highlights)}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right">{formatNumber(m.sharpness, locale, 0)}</td>
                  <td className="px-3 py-1.5">
                    <ColorStrip colors={m.colors} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ColorStrip({ colors: raw }: { colors: readonly ColorShare[] }) {
  const { t } = useTranslation();
  const colors = mergeColorShares(raw);
  if (colors.length === 0) return <span className="text-muted-foreground">—</span>;
  const label = colors.map((c) => `${c.hex} ${formatPercent(c.share)}`).join(", ");
  return (
    <div
      className="flex h-6 w-full min-w-40 gap-0.5"
      role="img"
      aria-label={t("technique.colorsAria", { list: label })}
    >
      {colors.map((color, index) => (
        <span
          key={`${index}-${color.hex}`}
          className="h-full rounded-sm first:rounded-l-md last:rounded-r-md"
          style={{ background: color.hex, flexGrow: Math.max(color.share, 0.04) }}
          title={`${color.hex} · ${formatPercent(color.share)}`}
        />
      ))}
    </div>
  );
}
