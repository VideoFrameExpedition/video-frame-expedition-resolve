import { Trophy } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { BenchRow } from "./benchFormat";
import {
  CRITERIA,
  ordinal,
  PRIORITIES,
  radarPoints,
  rank,
  type Criterion,
  type Priority,
  type Ranked,
} from "./benchRanking";

const STORAGE_KEY = "vfe.bench.priority";
const RADAR = { width: 230, height: 196, centre: { x: 115, y: 100 }, radius: 62 };
const RINGS = [25, 50, 75, 100];

function storedPriority(): Priority {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return PRIORITIES.find((priority) => priority === value) ?? "balanced";
  } catch {
    return "balanced";
  }
}

const outline = (points: { x: number; y: number }[]): string =>
  points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");

/** One model's profile: a branch per measure, the larger the surface the better the model. */
function Profile({
  model,
  axes,
  first,
}: {
  model: Ranked;
  axes: readonly Criterion[];
  first: boolean;
}) {
  const { t, i18n } = useTranslation();
  const { centre, radius } = RADAR;
  const ink = first ? "var(--brand-teal)" : "var(--chart-1)";
  const shape = radarPoints(
    axes.map((criterion) => model.points[criterion] ?? 0),
    centre,
    radius,
  );
  const labels = radarPoints(
    axes.map(() => 100),
    centre,
    radius + 12,
  );
  const described = axes
    .map((criterion) =>
      t("bench.rank.profilePoint", {
        criterion: t(`bench.rank.criteria.${criterion}`),
        value:
          model.points[criterion] === undefined
            ? t("bench.rank.notMeasured")
            : String(model.points[criterion]),
      }),
    )
    .join(", ");
  return (
    <figure
      aria-label={model.row.label}
      className="grid justify-items-center gap-1 rounded-xl border p-3"
    >
      <figcaption className="w-full text-center text-sm">
        <span className={cn("font-medium", first && "text-brand-teal")}>
          {model.place === null ? "" : `${ordinal(model.place, i18n.language)} · `}
          {model.row.label}
        </span>
        {model.total === null ? null : (
          <span className="text-muted-foreground block text-xs tabular-nums">
            {t("bench.rank.points", { value: formatNumber(model.total, i18n.language, 1) })}
          </span>
        )}
      </figcaption>
      <svg
        viewBox={`0 0 ${String(RADAR.width)} ${String(RADAR.height)}`}
        role="img"
        aria-label={t("bench.rank.profile", { name: model.row.label, points: described })}
        className="block w-full max-w-[260px]"
      >
        {RINGS.map((ring) => (
          <polygon
            key={ring}
            points={outline(
              radarPoints(
                axes.map(() => ring),
                centre,
                radius,
              ),
            )}
            fill="none"
            stroke={ring === 100 ? "var(--chart-axis)" : "var(--chart-grid)"}
          />
        ))}
        {labels.map((label, index) => {
          const criterion = axes[index];
          const rim = radarPoints(
            axes.map(() => 100),
            centre,
            radius,
          )[index];
          if (!criterion || !rim) {
            return null;
          }
          const side = label.x - centre.x;
          const below = label.y - centre.y;
          return (
            <g key={criterion}>
              <line x1={centre.x} y1={centre.y} x2={rim.x} y2={rim.y} stroke="var(--chart-grid)" />
              <text
                x={label.x}
                y={label.y}
                dy={below > 8 ? "0.7em" : below < -8 ? "0" : "0.32em"}
                textAnchor={side > 8 ? "start" : side < -8 ? "end" : "middle"}
                className={cn(
                  "text-[10px]",
                  model.points[criterion] === undefined
                    ? "fill-muted-foreground line-through"
                    : "fill-muted-foreground",
                )}
              >
                {t(`bench.rank.criteria.${criterion}`)}
              </text>
            </g>
          );
        })}
        <polygon
          points={outline(shape)}
          fill={ink}
          fillOpacity={0.25}
          stroke={ink}
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
        {shape.map((point, index) => (
          <circle key={axes[index]} cx={point.x} cy={point.y} r={2.2} fill={ink} />
        ))}
      </svg>
    </figure>
  );
}

/** The models' profiles side by side, the first of the ranking standing out. */
function Profiles({
  models,
  axes,
  hint,
  firstApart,
}: {
  models: Ranked[];
  axes: readonly Criterion[];
  hint: string;
  firstApart: boolean;
}) {
  const { t } = useTranslation();
  return (
    <>
      <div>
        <h3 className="text-sm font-medium">{t("bench.rank.profiles")}</h3>
        <p className="text-muted-foreground text-xs">{hint}</p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {models.map((model) => (
          <Profile
            key={model.row.id}
            model={model}
            axes={axes}
            first={model.place === 1 && firstApart}
          />
        ))}
      </div>
    </>
  );
}

/** « Ranking »: the tested models ranked, measure by measure and in total, and each one's
 * profile. The total is a weighted mean of points out of 100, on the measures every model has;
 * the user says what matters most (kept in this browser). A model tested alone has nothing to
 * be ranked against: only its profile is drawn, without the speed, which counts against the
 * fastest model. */
export function Ranking({ rows }: { rows: BenchRow[] }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const [priority, setPriority] = useState<Priority>(storedPriority);
  const { ranked, counted, left } = rank(rows, priority);
  const [alone] = ranked;
  if (ranked.length === 1 && alone) {
    const axes = CRITERIA.filter((c) => c !== "speed" && alone.points[c] !== undefined);
    return axes.length >= 3 ? (
      <section aria-label={t("bench.rank.profiles")} className="grid gap-4">
        <Profiles
          models={[{ ...alone, total: null, place: null }]}
          axes={axes}
          hint={t("bench.rank.profileAlone")}
          firstApart={false}
        />
      </section>
    ) : null;
  }
  if (ranked.length < 2) {
    return null;
  }
  const shown = CRITERIA.filter((c) => counted.includes(c) || left.includes(c));
  const name = (criterion: Criterion): string => t(`bench.rank.criteria.${criterion}`);
  const apart = (places: (number | null | undefined)[]): boolean => new Set(places).size > 1;
  const totalsApart = apart(ranked.map((model) => model.place));
  return (
    <section aria-label={t("bench.rank.title")} className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <Trophy className="text-brand-teal size-4" aria-hidden />
          {t("bench.rank.total")}
        </h3>
        <Select
          value={priority}
          onValueChange={(value) => {
            const next = PRIORITIES.find((candidate) => candidate === value) ?? "balanced";
            setPriority(next);
            try {
              window.localStorage.setItem(STORAGE_KEY, next);
            } catch {
              // Not remembered, still used now.
            }
          }}
        >
          <SelectTrigger className="w-60 max-w-full" aria-label={t("bench.rank.priority")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRIORITIES.map((option) => (
              <SelectItem key={option} value={option}>
                {t(`bench.rank.priorities.${option}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <ol aria-label={t("bench.rank.total")} className="grid gap-1.5">
        {ranked.map((model) => {
          const first = model.place === 1 && totalsApart;
          return (
            <li
              key={model.row.id}
              className="grid grid-cols-[2.25rem_minmax(0,14rem)_minmax(0,1fr)_5.5rem] items-center gap-2 text-sm"
            >
              <span
                className={cn(
                  "text-muted-foreground tabular-nums",
                  first && "text-brand-teal font-semibold",
                )}
              >
                {model.place === null ? "—" : ordinal(model.place, locale)}
              </span>
              <span className={cn("truncate", first && "font-semibold")} title={model.row.label}>
                {model.row.label}
              </span>
              <span className="bg-secondary block h-2.5 overflow-hidden rounded-full" aria-hidden>
                {model.total === null ? null : (
                  <span
                    className={cn(
                      "block h-full rounded-full",
                      first ? "bg-brand-teal" : "bg-chart-1",
                    )}
                    style={{ width: `${String(Math.max(1.5, model.total))}%` }}
                  />
                )}
              </span>
              <span className={cn("text-right tabular-nums", first && "font-semibold")}>
                {model.total === null
                  ? "—"
                  : t("bench.rank.points", { value: formatNumber(model.total, locale, 1) })}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="text-muted-foreground text-xs">
        {[
          t("bench.rank.totalHint", { measures: counted.map(name).join(", ") }),
          priority !== "balanced" && counted.includes(priority)
            ? t("bench.rank.heavy", { name: name(priority) })
            : null,
          left.length > 0
            ? t("bench.rank.left", { count: left.length, names: left.map(name).join(", ") })
            : null,
        ]
          .filter(Boolean)
          .join(" ")}
      </p>

      <h3 className="text-sm font-medium">{t("bench.rank.byMeasure")}</h3>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[46rem] text-sm" aria-label={t("bench.rank.byMeasure")}>
          <thead className="bg-secondary/50 text-muted-foreground text-xs">
            <tr>
              <th scope="col" className="px-3 py-2 text-left font-medium">
                {t("bench.results.columns.model")}
              </th>
              <th scope="col" className="px-3 py-2 text-left font-medium">
                {t("bench.rank.totalColumn")}
              </th>
              {shown.map((criterion) => (
                <th key={criterion} scope="col" className="px-3 py-2 text-left font-medium">
                  {name(criterion)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ranked.map((model) => (
              <tr key={model.row.id} className="border-t">
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  {model.row.label}
                </th>
                <td className="px-3 py-2">
                  <span
                    className={cn(
                      "block tabular-nums",
                      model.place === 1 && totalsApart && "text-brand-teal font-semibold",
                    )}
                  >
                    {model.place === null ? "—" : ordinal(model.place, locale)}
                  </span>
                  {model.total === null ? null : (
                    <span className="text-muted-foreground block text-xs tabular-nums">
                      {t("bench.rank.pointsShort", {
                        value: formatNumber(model.total, locale, 1),
                      })}
                    </span>
                  )}
                </td>
                {shown.map((criterion) => {
                  const place = model.places[criterion];
                  const best = place === 1 && apart(ranked.map((other) => other.places[criterion]));
                  return (
                    <td key={criterion} className="px-3 py-2">
                      <span
                        className={cn(
                          "block tabular-nums",
                          best && "text-brand-teal font-semibold",
                        )}
                      >
                        {place === undefined ? "—" : ordinal(place, locale)}
                      </span>
                      {model.points[criterion] === undefined ? null : (
                        <span className="text-muted-foreground block text-xs tabular-nums">
                          {t("bench.rank.pointsShort", { value: model.points[criterion] })}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="text-muted-foreground text-xs">
        <summary className="cursor-pointer text-sm">{t("bench.rank.how.title")}</summary>
        <ul className="mt-2 grid gap-1.5">
          {(["vram", "speed", "shares", "quality", "total"] as const).map((line) => (
            <li key={line}>{t(`bench.rank.how.${line}`)}</li>
          ))}
        </ul>
      </details>

      {shown.length >= 3 ? (
        <Profiles
          models={ranked}
          axes={shown}
          hint={t("bench.rank.profilesHint")}
          firstApart={totalsApart}
        />
      ) : null}
    </section>
  );
}
