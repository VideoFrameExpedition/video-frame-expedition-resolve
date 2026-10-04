import { Star } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SynthesisChapter, SynthesisHighlight } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatClock, formatNumber } from "@/lib/format";

import { revealPlayer, usePlayer } from "../player";
import { clipNotes, criterionLabel, soundRange } from "./labels";

/**
 * The moments code picked as highlights (the model only wrote why): where to cut the picture,
 * and the sound when it starts earlier or ends later (J-cut, L-cut). Suggestions, not truth.
 */
export function HighlightList({
  highlights,
  chapters,
  model,
}: {
  highlights: SynthesisHighlight[];
  chapters: SynthesisChapter[];
  model: string | null;
}) {
  const { t } = useTranslation();
  if (highlights.length === 0) {
    return null;
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2 className="flex items-center gap-2">
            <Star className="text-brand-teal size-4" aria-hidden />
            {t("synthesis.highlights")}
          </h2>
        </CardTitle>
        <CardDescription className="text-xs">{t("synthesis.highlightsHint")}</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="grid gap-4" aria-label={t("synthesis.highlights")}>
          {highlights.map((highlight) => (
            <Highlight
              key={highlight.rank}
              highlight={highlight}
              chapter={chapters.find((chapter) => chapter.index === highlight.chapter)}
            />
          ))}
        </ol>
        <p className="text-muted-foreground mt-3 text-xs">
          {model ? t("synthesis.writtenBy", { model }) : t("synthesis.writtenByNoModel")}
        </p>
      </CardContent>
    </Card>
  );
}

function Highlight({
  highlight,
  chapter,
}: {
  highlight: SynthesisHighlight;
  chapter: SynthesisChapter | undefined;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const { seek } = usePlayer();
  const { clip } = highlight;
  const sound = soundRange(clip);
  const notes = clipNotes(t, clip, locale);
  const range = (start: number, end: number): string =>
    t("synthesis.range", { start: formatClock(start, true), end: formatClock(end, true) });
  return (
    <li className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-3 sm:grid-cols-[9rem_minmax(0,1fr)]">
      <button
        type="button"
        className="focus-visible:ring-ring relative self-start overflow-hidden rounded-md focus-visible:ring-2 focus-visible:outline-none"
        aria-label={t("synthesis.highlightSeek", {
          rank: highlight.rank,
          time: formatClock(clip.picture_in_s),
        })}
        onClick={() => {
          seek(clip.picture_in_s);
          revealPlayer();
        }}
      >
        {highlight.thumb_url ? (
          <img
            src={highlight.thumb_url}
            alt=""
            loading="lazy"
            className="aspect-video w-full bg-black object-contain"
          />
        ) : (
          <span className="bg-muted block aspect-video w-full" />
        )}
        <span className="absolute top-1 left-1 rounded bg-black/70 px-1.5 font-mono text-[0.7rem] font-semibold text-white">
          {highlight.rank}
        </span>
      </button>
      <div className="grid min-w-0 content-start gap-1.5">
        <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-medium">{t("synthesis.highlight", { rank: highlight.rank })}</span>
          {chapter ? (
            <span className="text-muted-foreground text-xs">
              {t("synthesis.inChapter", { index: chapter.index })} · {chapter.title}
            </span>
          ) : null}
        </p>
        {highlight.reason ? <p className="text-sm text-pretty">{highlight.reason}</p> : null}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-xs">
          <dt className="text-muted-foreground">{t("synthesis.picture")}</dt>
          <dd className="font-mono tabular-nums">
            {range(clip.picture_in_s, clip.picture_out_s)}
            <span className="text-muted-foreground font-sans">
              {" · "}
              {formatNumber(clip.picture_out_s - clip.picture_in_s, locale, 1, "s")}
            </span>
          </dd>
          {sound ? (
            <>
              <dt className="text-muted-foreground">{t("synthesis.sound")}</dt>
              <dd className="font-mono tabular-nums">{range(sound[0], sound[1])}</dd>
            </>
          ) : null}
        </dl>
        {notes.length > 0 ? (
          <ul className="text-muted-foreground grid gap-0.5 text-xs">
            {notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
        {highlight.criteria.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground" aria-hidden>
              {t("synthesis.criteria")}
            </span>
            <ul className="flex flex-wrap gap-1.5" aria-label={t("synthesis.criteria")}>
              {highlight.criteria.map((criterion) => (
                <li
                  key={criterion}
                  className="text-muted-foreground rounded-full border px-2 py-0.5 whitespace-nowrap"
                >
                  {criterionLabel(t, criterion)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </li>
  );
}
