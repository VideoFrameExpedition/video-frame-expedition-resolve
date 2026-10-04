import { Download, Search } from "lucide-react";
import { Fragment, memo, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { usePatchVideo, useTranscript } from "@/api/queries";
import { spanIndexAt } from "@/components/charts/scale";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatClock, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import { PartMissing, PartNotice } from "./PartNotice";
import { usePlayer, usePlayheadSelector } from "./player";
import { activeSegmentAt, foldText } from "./transcriptSearch";

// The shape the API client delivers (tuples arrive as plain arrays).
type Transcript = NonNullable<ReturnType<typeof useTranscript>["data"]>;
type Segment = Transcript["segments"][number];

interface Word {
  start: number;
  text: string;
  spaced: boolean; // Whisper puts the space before a word in the word itself
  probability: number;
}

/** Words arrive as compact [start, end, text, probability] arrays. */
function wordsOf(segment: Segment): Word[] {
  return segment.words.map((w) => {
    const text = String(w[2]);
    return {
      start: Number(w[0]),
      text: text.trim(),
      spaced: /^\s/.test(text),
      probability: Number(w[3]),
    };
  });
}

function languageName(code: string | null | undefined, locale: string): string | null {
  if (!code) return null;
  try {
    return new Intl.DisplayNames([locale], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

const SegmentRow = memo(function SegmentRow({
  segment,
  active,
  query,
  onSeek,
}: {
  segment: Segment;
  active: boolean;
  query: string;
  onSeek: (t: number) => void;
}) {
  const { t } = useTranslation();
  const words = useMemo(() => wordsOf(segment), [segment]);
  const activeWord = usePlayheadSelector((time) =>
    active
      ? spanIndexAt(
          words.map((w) => w.start),
          time + 0.05,
        )
      : -1,
  );
  const matches = query ? foldText(segment.text).includes(query) : false;
  return (
    <li
      aria-current={active ? "true" : undefined}
      className={cn(
        "grid grid-cols-[4.5rem_1fr] gap-3 rounded-md px-2 py-1.5",
        active && "bg-muted/70",
        segment.suspect && "opacity-55",
        matches && "ring-brand-teal/60 ring-1",
      )}
    >
      <button
        type="button"
        className="text-brand-teal self-start font-mono text-xs hover:underline"
        onClick={() => {
          onSeek(segment.start_s);
        }}
      >
        {formatClock(segment.start_s)}
      </button>
      <p className="text-sm leading-relaxed">
        {words.length
          ? words.map((word, i) => (
              // A button drops its own leading space: the separator goes before it.
              <Fragment key={`${word.start}-${i}`}>
                {i > 0 && word.spaced ? " " : null}
                <button
                  type="button"
                  tabIndex={-1}
                  title={formatClock(word.start)}
                  className={cn(
                    "rounded-sm hover:underline",
                    i === activeWord && "bg-brand-teal/25",
                    word.probability < 0.5 &&
                      "decoration-muted-foreground underline decoration-dotted",
                  )}
                  onClick={() => {
                    onSeek(word.start);
                  }}
                >
                  {word.text}
                </button>
              </Fragment>
            ))
          : segment.text}
        {segment.suspect ? (
          <span className="text-muted-foreground ml-1 text-xs" title={t("transcript.suspect")}>
            (?)
          </span>
        ) : null}
      </p>
    </li>
  );
});

const MODES = ["auto", "always", "never"] as const;

/** How this video is transcribed: a change redoes (or erases) its transcription. */
function ModeSelect({ videoId, transcript }: { videoId: string; transcript: Transcript }) {
  const { t } = useTranslation();
  const patch = usePatchVideo(videoId);
  const value = transcript.transcript_mode ?? "auto";
  return (
    <Select
      value={value}
      disabled={patch.isPending}
      onValueChange={(next) => {
        if (next === value) return;
        patch.mutate(
          { transcript_mode: next === "auto" ? null : (next as "always" | "never") },
          {
            onSuccess: () => toast.success(t("transcript.modeSaved")),
            onError: (error) => toast.error(errorMessage(error)),
          },
        );
      }}
    >
      <SelectTrigger size="sm" aria-label={t("transcript.mode.label")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {MODES.map((mode) => (
          <SelectItem key={mode} value={mode}>
            {t(`transcript.mode.${mode}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function Missing({ videoId, transcript }: { videoId: string; transcript: Transcript }) {
  const { t } = useTranslation();
  const patch = usePatchVideo(videoId);
  return (
    <div className="grid gap-3">
      <PartMissing part={transcript} prefix="transcript">
        {transcript.can_force ? (
          <Button
            size="sm"
            variant="secondary"
            className="w-fit"
            disabled={patch.isPending}
            onClick={() => {
              // The server redoes the transcription when the choice changes.
              patch.mutate(
                { transcript_mode: "always" },
                {
                  onSuccess: () => toast.success(t("video.queued")),
                  onError: (error) => toast.error(errorMessage(error)),
                },
              );
            }}
          >
            {t("transcript.force")}
          </Button>
        ) : null}
      </PartMissing>
      <div className="flex justify-end">
        <ModeSelect videoId={videoId} transcript={transcript} />
      </div>
    </div>
  );
}

export function TranscriptTab({ videoId }: { videoId: string }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const transcript = useTranscript(videoId);
  const { seek } = usePlayer();
  const [search, setSearch] = useState("");
  const query = foldText(search.trim());
  const segments = useMemo(() => transcript.data?.segments ?? [], [transcript.data]);
  const starts = useMemo(() => segments.map((s) => s.start_s), [segments]);
  const activeIndex = usePlayheadSelector((time) => activeSegmentAt(segments, starts, time));
  if (transcript.isPending) return <Skeleton className="h-60 w-full" />;
  if (transcript.isError) return null;
  const data = transcript.data;
  if (data.status !== "ready" || !segments.length) {
    return <Missing videoId={videoId} transcript={data} />;
  }
  const hits = query ? segments.filter((s) => foldText(s.text).includes(query)).length : 0;
  const language = languageName(data.language, locale);

  return (
    <div className="grid gap-4">
      <PartNotice part={data} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-muted-foreground text-sm">
          {[
            language
              ? t("transcript.language", {
                  language,
                  probability: formatNumber((data.language_probability ?? 0) * 100, locale, 0),
                })
              : null,
            data.speech_s !== null
              ? t("transcript.speech", { duration: formatClock(data.speech_s) })
              : null,
            data.model,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <ModeSelect videoId={videoId} transcript={data} />
          {data.srt_url ? (
            <Button size="sm" variant="ghost" asChild>
              <a href={data.srt_url} download>
                <Download className="size-4" aria-hidden />
                SRT
              </a>
            </Button>
          ) : null}
          {data.vtt_url ? (
            <Button size="sm" variant="ghost" asChild>
              <a href={data.vtt_url} download>
                <Download className="size-4" aria-hidden />
                VTT
              </a>
            </Button>
          ) : null}
        </div>
      </div>
      <div className="relative">
        <Search
          className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2"
          aria-hidden
        />
        <Input
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
          placeholder={t("transcript.search")}
          aria-label={t("transcript.search")}
          className="pl-9"
        />
      </div>
      {query ? (
        <p className="text-muted-foreground text-xs" role="status">
          {t("transcript.hits", { count: hits })}
        </p>
      ) : null}
      <ol className="grid gap-0.5">
        {segments.map((segment, index) => (
          <SegmentRow
            key={segment.idx}
            segment={segment}
            active={index === activeIndex}
            query={query}
            onSeek={seek}
          />
        ))}
      </ol>
      <p className="text-muted-foreground text-xs">{t("transcript.untrusted")}</p>
    </div>
  );
}
