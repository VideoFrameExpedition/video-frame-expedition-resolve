import { Ear, Guitar, Volume2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAudio } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatClock, formatNumber } from "@/lib/format";

import { PartMissing, PartNotice } from "./PartNotice";
import { usePlayer } from "./player";

// From the query: the fetch client reads the (start, end) pairs as plain arrays.
type Audio = NonNullable<ReturnType<typeof useAudio>["data"]>;

const MAX_TIMES = 6;

/** A length (« 12 s », « 1 min 15 s »), never written like a moment of the video (01:15). */
function seconds(value: number, locale: string): string {
  const total = Math.round(value);
  if (total < 60) return `${formatNumber(value, locale, 0)} s`;
  const rest = total % 60;
  return rest
    ? `${String(Math.floor(total / 60))} min ${String(rest)} s`
    : `${String(total / 60)} min`;
}

function Presence({ audio }: { audio: Audio }) {
  const { t, i18n } = useTranslation();
  return (
    <ul className="grid gap-2" aria-label={t("audio.presenceLabel")}>
      {audio.presence
        .filter((p) => p.share >= 0.01)
        .map((p) => (
          <li
            key={p.category}
            className="grid grid-cols-[9rem_1fr_auto] items-center gap-3 text-sm"
          >
            <span>{t(`audio.category.${p.category}`, { defaultValue: p.category })}</span>
            <span className="bg-muted h-2 overflow-hidden rounded-full" aria-hidden>
              <span
                className="bg-foreground/70 block h-full rounded-full"
                style={{ width: `${Math.max(2, p.share * 100)}%` }}
              />
            </span>
            <span className="text-muted-foreground tabular-nums">
              {t("audio.share", {
                value: formatNumber(p.share * 100, i18n.language, 0),
              })}
            </span>
          </li>
        ))}
    </ul>
  );
}

/** The specific sounds heard, longest first, each with the moments to jump to. */
function HeardSounds({ audio }: { audio: Audio }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const { seek } = usePlayer();
  const heard = audio.heard ?? null;
  // Without CED's opinion: « Update » adds it when installed, else how to install it.
  const cedNote = audio.taggers.includes("ced-small")
    ? null
    : audio.ced_available
      ? t("audio.cedUpdate")
      : t("audio.cedMissing");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Ear className="text-brand-teal size-4" aria-hidden />
          {t("audio.heardSounds")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {heard === null ? (
          <p className="text-muted-foreground text-sm">{t("audio.heardNotComputed")}</p>
        ) : heard.length ? (
          <ul className="grid gap-2" aria-label={t("audio.heardSounds")}>
            {heard.map((sound) => (
              <li
                key={sound.label}
                className="grid grid-cols-[minmax(8rem,14rem)_5rem_1fr] items-baseline gap-3"
              >
                <span
                  className="text-sm font-medium"
                  title={`${sound.label} · ${t(`audio.category.${sound.category}`, {
                    defaultValue: sound.category,
                  })} · ${formatNumber(sound.score, locale, 2)}`}
                >
                  {sound.name}
                </span>
                <span
                  className="text-muted-foreground text-xs tabular-nums"
                  title={t("audio.heardFor")}
                >
                  {seconds(sound.seconds, locale)}
                </span>
                <span className="flex flex-wrap gap-x-2 gap-y-1">
                  {sound.spans.slice(0, MAX_TIMES).map(([start = 0]) => (
                    <button
                      key={start}
                      type="button"
                      className="text-brand-teal font-mono text-xs hover:underline"
                      aria-label={t("audio.listenAt", {
                        name: sound.name,
                        time: formatClock(start),
                      })}
                      onClick={() => {
                        seek(start);
                      }}
                    >
                      {formatClock(start)}
                    </button>
                  ))}
                  {sound.moments > MAX_TIMES ? (
                    <span
                      className="text-muted-foreground text-xs"
                      title={t("audio.moreMoments", { count: sound.moments - MAX_TIMES })}
                    >
                      …
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-sm">{t("audio.noHeard")}</p>
        )}
        {heard?.length ? (
          <p className="text-muted-foreground text-xs">{t("audio.heardHint")}</p>
        ) : null}
        {cedNote ? <p className="text-muted-foreground text-xs">{cedNote}</p> : null}
      </CardContent>
    </Card>
  );
}

export function AudioTab({ videoId }: { videoId: string }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const audio = useAudio(videoId);
  const { seek } = usePlayer();
  if (audio.isPending) return <Skeleton className="h-60 w-full" />;
  if (audio.isError) return null;
  const data = audio.data;
  if (data.status !== "ready") return <PartMissing part={data} prefix="audio" />;
  const withCed = data.taggers.includes("ced-small");
  // Files analysed before « sounds heard » only have the notable sounds.
  const events = data.heard === null ? data.segments.filter((s) => s.kind === "event") : [];

  return (
    <div className="grid gap-6">
      <PartNotice part={data} />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Volume2 className="text-brand-teal size-4" aria-hidden />
            {t("audio.heard")}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <Presence audio={data} />
          <p className="text-muted-foreground text-xs">{t("audio.multiLabel")}</p>
          {data.environment || data.issues.length ? (
            <p className="text-sm">
              {[
                data.environment
                  ? t(`audio.environment.${data.environment}`, { defaultValue: data.environment })
                  : null,
                ...data.issues.map((issue) => t(`audio.issue.${issue}`, { defaultValue: issue })),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <HeardSounds audio={data} />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Guitar className="text-brand-teal size-4" aria-hidden />
            {t("audio.instruments")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data.instruments.length ? (
            <ul className="flex flex-wrap gap-2">
              {data.instruments.map((instrument) => (
                <li
                  key={instrument.label}
                  className="bg-muted/60 rounded-full px-3 py-1 text-sm"
                  title={`${instrument.label} · ${formatNumber(instrument.max_score, locale, 2)}`}
                >
                  {instrument.name}
                  <span className="text-muted-foreground ml-1.5 text-xs tabular-nums">
                    {seconds(instrument.seconds, locale)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground text-sm">{t("audio.noInstrument")}</p>
          )}
        </CardContent>
      </Card>

      {events.length ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("audio.events")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="grid gap-1">
              {events.map((event) => (
                <li key={`${event.label}-${event.start_s}`} className="flex items-center gap-3">
                  <button
                    type="button"
                    className="text-brand-teal font-mono text-xs hover:underline"
                    onClick={() => {
                      seek(event.start_s);
                    }}
                  >
                    {formatClock(event.start_s)}
                  </button>
                  <span className="text-sm">{event.name ?? event.category}</span>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      ) : null}

      {data.shots.length > 1 ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("audio.perShot")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="grid gap-1.5">
              {data.shots.map((shot) => {
                const names = [
                  ...new Set([...shot.labels.map((l) => l.name), ...shot.heard.map((h) => h.name)]),
                ];
                return (
                  <li
                    key={shot.shot_idx}
                    className="grid grid-cols-[6rem_1fr] items-baseline gap-3"
                  >
                    <button
                      type="button"
                      className="text-brand-teal text-left font-mono text-xs hover:underline"
                      onClick={() => {
                        seek(shot.start_s);
                      }}
                    >
                      {t("audio.shot", {
                        index: shot.shot_idx + 1,
                        time: formatClock(shot.start_s),
                      })}
                    </button>
                    <span className="text-sm">{names.length ? names.join(" · ") : "—"}</span>
                  </li>
                );
              })}
            </ol>
          </CardContent>
        </Card>
      ) : null}

      <p className="text-muted-foreground text-xs">
        {t("audio.source", { model: withCed ? "YAMNet + CED-small" : "YAMNet" })}
      </p>
    </div>
  );
}
