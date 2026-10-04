import {
  Ban,
  CircleCheck,
  CircleMinus,
  Layers,
  Mountain,
  Scissors,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { Fragment, useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { Keyframe, Shot, ShotStory, ShotUsability, SynthesisSuggestion } from "@/api/client";
import { EmptyState } from "@/components/common";
import { spanIndexAt } from "@/components/charts/scale";
import { HintBadge } from "@/components/HintBadge";
import { Badge } from "@/components/ui/badge";
import { formatClock, formatNumber, formatPercent } from "@/lib/format";
import { kelvinKey } from "@/lib/kelvin";
import { cn } from "@/lib/utils";

import { FAMILY_FILL, motionFamily, motionGlyph } from "./motion";
import { usePlayer, usePlayheadSelector } from "./player";
import { storyModels } from "./stories";
import {
  reasonLabel,
  rolesByShot,
  usabilityLevel,
  type EditingRole,
  type UsabilityLevel,
} from "./synthesis/labels";

const COLUMNS = 6;
const NO_USABILITY: ShotUsability[] = [];
const NO_SUGGESTIONS: SynthesisSuggestion[] = [];

export function ShotsTab({
  shots,
  keyframes,
  usability = NO_USABILITY,
  suggestions = NO_SUGGESTIONS,
}: {
  shots: Shot[];
  keyframes: Keyframe[];
  /** From the synthesis: the usability of each shot, indicative. */
  usability?: ShotUsability[];
  /** From the synthesis: editing roles suggested for some shots. */
  suggestions?: SynthesisSuggestion[];
}) {
  const { t, i18n } = useTranslation();
  const { seek } = usePlayer();
  const starts = useMemo(() => shots.map((shot) => shot.start_s), [shots]);
  const current = usePlayheadSelector((time) => spanIndexAt(starts, time));
  const posters = useMemo(() => {
    const byShot = new Map<string, Keyframe>();
    for (const frame of keyframes) {
      if (frame.shot_id && !byShot.has(frame.shot_id)) {
        byShot.set(frame.shot_id, frame);
      }
    }
    return byShot;
  }, [keyframes]);
  const models = useMemo(() => storyModels(shots), [shots]);
  const scores = useMemo(
    () => new Map(usability.map((entry) => [entry.shot_idx, entry])),
    [usability],
  );
  const roles = useMemo(() => rolesByShot(suggestions), [suggestions]);

  if (shots.length === 0) {
    return <EmptyState title={t("shots.empty")} body={t("shots.emptyBody")} />;
  }
  const locale = i18n.language;
  // Once the synthesis has scored the shots.
  const withUsability = scores.size > 0 || roles.size > 0;
  return (
    <div className="grid gap-2">
      {/* A query container: a story stays within the visible width when the table scrolls. */}
      <div className="@container overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <caption className="sr-only">{t("shots.caption")}</caption>
          <thead className="bg-muted text-left">
            <tr>
              <th scope="col" className="px-3 py-1.5 font-medium">
                {t("shots.col.shot")}
              </th>
              <th scope="col" className="px-3 py-1.5 font-medium">
                {t("shots.col.motion")}
              </th>
              <th scope="col" className="px-3 py-1.5 font-medium">
                {t("shots.col.stability")}
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                {t("shots.col.duration")}
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                {t("shots.col.luma")}
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                {t("shots.col.cct")}
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {shots.map((shot, index) => {
              const poster = posters.get(shot.id);
              const cct = shot.metrics.cct_k;
              const score = scores.get(shot.idx);
              return (
                <Fragment key={shot.id}>
                  <tr
                    className={cn("border-t", index === current && "bg-accent/40")}
                    aria-current={index === current ? "true" : undefined}
                  >
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">
                      <button
                        type="button"
                        className="group focus-visible:ring-ring flex items-center gap-2.5 rounded focus-visible:ring-2 focus-visible:outline-none"
                        onClick={() => {
                          seek(shot.start_s);
                        }}
                      >
                        {poster ? (
                          <img
                            src={poster.thumb_url}
                            alt=""
                            loading="lazy"
                            className="h-9 w-16 rounded-sm bg-black object-contain"
                          />
                        ) : (
                          <span className="bg-muted h-9 w-16 rounded-sm" aria-hidden />
                        )}
                        <span className="grid text-left">
                          <span className="font-medium">
                            {t("timeline.shot", { index: shot.idx + 1 })}
                          </span>
                          <span className="text-muted-foreground font-mono text-xs group-hover:underline">
                            {formatClock(shot.start_s, true)}
                          </span>
                        </span>
                      </button>
                      {withUsability ? (
                        // Under the shot rather than in a column of its own: the table keeps
                        // its width (every column visible next to the details panel).
                        <span className="mt-1.5 flex max-w-48 flex-wrap items-center gap-1">
                          {score ? <UsabilityBadge usability={score} /> : null}
                          {roles.get(shot.idx)?.map((role) => (
                            <RoleChip key={role} role={role} />
                          ))}
                        </span>
                      ) : null}
                    </th>
                    <td className="px-3 py-1.5">
                      <span className="flex items-center gap-2">
                        <span
                          className="inline-block h-3 w-3 shrink-0 rounded-sm"
                          style={{ background: FAMILY_FILL[motionFamily(shot.motion)] }}
                          aria-hidden
                        />
                        <span aria-hidden className="w-3 text-center">
                          {motionGlyph(shot.motion)}
                        </span>
                        {t(`motion.${shot.motion}`)}
                      </span>
                      {shot.boundary === "fade" ? (
                        <span className="text-muted-foreground text-xs">{t("shots.fadeIn")}</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5">
                      <span className="flex items-center gap-2">
                        <span
                          className="bg-muted relative h-1.5 w-16 overflow-hidden rounded-full"
                          aria-hidden
                        >
                          <span
                            className="bg-foreground/70 absolute inset-y-0 left-0 rounded-full"
                            style={{ width: `${Math.round(shot.stability * 100)}%` }}
                          />
                        </span>
                        {formatPercent(shot.stability)}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {formatNumber(shot.end_s - shot.start_s, locale, 1, "s")}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {shot.metrics.luma === null ? "—" : formatPercent(shot.metrics.luma)}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {cct === null ? (
                        "—"
                      ) : (
                        <span title={t(`kelvin.${kelvinKey(cct)}`)}>
                          {formatNumber(cct, locale, 0, "K")}
                        </span>
                      )}
                    </td>
                  </tr>
                  {shot.stories.length > 0 ? (
                    // Stacked under its shot rather than in a column: a strip of frames needs room.
                    <tr className={cn(index === current && "bg-accent/40")}>
                      <td colSpan={COLUMNS} className="px-3 pt-0.5 pb-3">
                        <ShotStories stories={shot.stories} locale={locale} onSeek={seek} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {withUsability ? (
        <p className="text-muted-foreground text-xs">{t("synthesis.usability.footnote")}</p>
      ) : null}
      {models.length > 0 ? (
        <p className="text-muted-foreground text-xs">
          {t("shots.story.footnote", { model: models.join(", ") })}
        </p>
      ) : null}
    </div>
  );
}

/** What happens in a shot, part by part, as told by the vision model from a few of its frames. */
function ShotStories({
  stories,
  locale,
  onSeek,
}: {
  stories: ShotStory[];
  locale: string;
  onSeek: (t: number) => void;
}) {
  const { t } = useTranslation();
  // Seconds with a decimal under a minute (« 12.3 s »), then minutes like the part labels.
  const seconds = (value: number): string =>
    value < 60 ? formatNumber(value, locale, 1, "s") : formatClock(value);
  return (
    <div className="sticky left-3 grid max-w-[calc(100cqw-1.5rem)] gap-3 border-l-2 pl-3">
      <p className="text-muted-foreground text-xs font-medium">{t("shots.story.title")}</p>
      {stories.map((story) => (
        <div key={story.part} className="grid gap-1.5">
          {story.parts > 1 ? (
            <p className="text-muted-foreground font-mono text-xs">
              {t("shots.story.part", {
                part: story.part,
                parts: story.parts,
                start: formatClock(story.start_s),
                end: formatClock(story.end_s),
              })}
            </p>
          ) : null}
          <p className="leading-relaxed text-pretty">
            {story.summary}{" "}
            {story.main_action ? (
              <Badge variant="secondary" className="align-middle font-normal whitespace-normal">
                <span className="sr-only">{t("shots.story.action")}</span>
                {story.main_action}
              </Badge>
            ) : null}{" "}
            {story.possible_cut ? <PossibleCut /> : null}
          </p>
          {story.frames.length > 0 ? (
            <ul className="flex flex-wrap gap-2">
              {story.frames.map((frame) => {
                const time = seconds(frame.t_s);
                return (
                  <li key={frame.t_s}>
                    <figure className="grid w-28 gap-1">
                      <button
                        type="button"
                        className="focus-visible:ring-ring rounded-sm focus-visible:ring-2 focus-visible:outline-none"
                        aria-label={t("shots.story.seek", { time })}
                        onClick={() => {
                          // A first keyframe may sit a frame before the part: seeking there
                          // would make the previous shot the current one.
                          onSeek(Math.max(frame.t_s, story.start_s));
                        }}
                      >
                        <img
                          src={frame.thumb_url}
                          alt={t("shots.story.frameAlt", { time })}
                          loading="lazy"
                          className="aspect-video w-full rounded-sm bg-black object-contain"
                        />
                      </button>
                      <figcaption className="text-muted-foreground text-xs leading-snug">
                        {frame.note
                          ? t("shots.story.seenNote", { time, note: frame.note })
                          : t("shots.story.seen", { time })}
                      </figcaption>
                    </figure>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** The « possible cut » hint: hovering, focusing or tapping it shows why. */
function PossibleCut() {
  const { t } = useTranslation();
  return (
    <HintBadge hint={t("shots.story.possibleCutHint")} className="bg-warning/15 text-warning-ink">
      <Scissors aria-hidden />
      {t("shots.story.possibleCut")}
    </HintBadge>
  );
}

const LEVEL_STYLE: Record<UsabilityLevel, { icon: LucideIcon; className: string }> = {
  good: { icon: CircleCheck, className: "bg-success/15 text-success-ink" },
  fair: { icon: CircleMinus, className: "bg-secondary text-secondary-foreground" },
  poor: { icon: TriangleAlert, className: "bg-warning/15 text-warning-ink" },
};

/**
 * How usable a shot is (0–100, indicative): the score, an icon for its band (never the colour
 * alone) and, on hover, focus or tap, the reasons it lost points.
 */
function UsabilityBadge({ usability }: { usability: ShotUsability }) {
  const { t } = useTranslation();
  const level = usabilityLevel(usability.score);
  const { icon: Icon, className } = LEVEL_STYLE[level];
  const reasons = usability.reasons.map((reason) => reasonLabel(t, reason));
  return (
    <HintBadge
      hint={
        reasons.length
          ? t("synthesis.usability.hint", { reasons: reasons.join(", ") })
          : t("synthesis.usability.noReason")
      }
      className={cn("tabular-nums", className)}
    >
      <Icon aria-hidden />
      <span aria-hidden>{usability.score}</span>
      <span className="sr-only">
        {t("synthesis.usability.label", {
          score: usability.score,
          level: t(`synthesis.usability.level.${level}`),
        })}
      </span>
    </HintBadge>
  );
}

const ROLE_ICON: Record<EditingRole, LucideIcon> = {
  establishing: Mountain,
  b_roll: Layers,
  avoid: Ban,
};

/** An editing role suggested for the shot (« establishing shot », « B-roll », « avoid »). */
function RoleChip({ role }: { role: EditingRole }) {
  const { t } = useTranslation();
  const Icon = ROLE_ICON[role];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs whitespace-nowrap",
        role === "avoid" ? "border-warning/40 text-warning-ink" : "text-muted-foreground",
      )}
      title={t("synthesis.role.hint")}
    >
      <Icon className="size-3" aria-hidden />
      <span className="sr-only">{t("synthesis.role.suggestion")}</span>
      {t(`synthesis.role.${role}`)}
    </span>
  );
}
