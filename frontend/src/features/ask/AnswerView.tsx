import { Link } from "@tanstack/react-router";
import { AlertTriangle, Eye, Film, RotateCcw, SearchX } from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import type { AskCheck, AskCitation, AskResult } from "@/api/client";
import { EmptyState } from "@/components/common";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { answerParts, paragraphs } from "./askState";

function sourceLabel(citation: AskCitation, t: TFunction): string {
  return citation.timecode
    ? t("ask.source", { n: citation.n, name: citation.filename, time: citation.timecode })
    : t("ask.sourceWhole", { n: citation.n, name: citation.filename });
}

function videoSearch(citation: AskCitation): { t?: number } {
  return citation.t_start !== null ? { t: Math.round(citation.t_start * 1000) / 1000 } : {};
}

/** « [n] » in the answer: opens the video at the cited moment. */
function CitationMark({ citation }: { citation: AskCitation }) {
  const { t } = useTranslation();
  if (!citation.available) {
    return <span className="text-muted-foreground font-mono text-[0.8em]">[{citation.n}]</span>;
  }
  return (
    <Link
      to="/videos/$videoId"
      params={{ videoId: citation.video_id }}
      search={videoSearch(citation)}
      aria-label={sourceLabel(citation, t)}
      title={citation.excerpt}
      className="text-accent-foreground hover:bg-accent focus-visible:ring-ring rounded px-0.5 font-mono text-[0.8em] font-medium focus-visible:ring-2 focus-visible:outline-none"
    >
      [{citation.n}]
    </Link>
  );
}

/** The answer's text: plain paragraphs, never HTML; its citations are links. */
export function AnswerText({ text, citations }: { text: string; citations: AskCitation[] }) {
  return (
    <div className="grid gap-3 text-[0.95rem] leading-relaxed">
      {paragraphs(text).map((paragraph, index) => (
        <p key={index} className="break-words whitespace-pre-line">
          {answerParts(paragraph, citations).map((part, at) =>
            "citation" in part ? (
              <CitationMark key={at} citation={part.citation} />
            ) : (
              <span key={at}>{part.text}</span>
            ),
          )}
        </p>
      ))}
    </div>
  );
}

/** A source: « [n] video @ mm:ss » with its thumbnail; opens the video at that moment. */
function SourceChip({ citation }: { citation: AskCitation }) {
  const { t } = useTranslation();
  const name = citation.title ?? citation.filename;
  const content = (
    <>
      <span className="relative aspect-video w-16 shrink-0 overflow-hidden rounded bg-black">
        {citation.thumb_url ? (
          <img src={citation.thumb_url} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <span className="text-muted-foreground flex size-full items-center justify-center">
            <Film className="size-4" aria-hidden />
          </span>
        )}
      </span>
      <span className="min-w-0 text-left">
        <span className="flex items-baseline gap-1.5">
          <span className="text-accent-foreground font-mono text-xs font-medium">
            [{citation.n}]
          </span>
          <span className="truncate text-sm">{name}</span>
        </span>
        <span className="text-muted-foreground block truncate font-mono text-xs">
          {citation.timecode ? `@ ${citation.timecode}` : t("ask.whole")}
          {citation.available ? "" : ` · ${t("ask.removed")}`}
        </span>
      </span>
    </>
  );
  const shape =
    "flex w-full max-w-xs items-center gap-2 rounded-lg border bg-card/60 p-1.5 pr-3 transition-colors";
  if (!citation.available) {
    return (
      <li>
        <span className={cn(shape, "opacity-60")} title={citation.excerpt}>
          {content}
        </span>
      </li>
    );
  }
  return (
    <li>
      <Link
        to="/videos/$videoId"
        params={{ videoId: citation.video_id }}
        search={videoSearch(citation)}
        aria-label={sourceLabel(citation, t)}
        title={citation.excerpt}
        className={cn(
          shape,
          "hover:bg-secondary/70 focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
        )}
      >
        {content}
      </Link>
    </li>
  );
}

export function SourceList({ citations }: { citations: AskCitation[] }) {
  const { t } = useTranslation();
  if (citations.length === 0) {
    return null;
  }
  return (
    <div className="grid gap-2">
      <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        {t("ask.sources")}
      </h3>
      <ul className="flex flex-wrap gap-2" aria-label={t("ask.sources")}>
        {citations.map((citation) => (
          <SourceChip key={citation.n} citation={citation} />
        ))}
      </ul>
    </div>
  );
}

const VERDICT_STYLE: Record<NonNullable<AskCheck["verdict"]>, string> = {
  confirmed: "bg-accent text-accent-foreground",
  partly_confirmed: "bg-warning/15 text-warning-ink",
  contradicted: "bg-destructive/15 text-destructive",
  not_visible: "bg-secondary text-secondary-foreground",
};

/** The visual check, apart from the answer: what the images of the cited moments say. */
export function VisualCheckNote({ check }: { check: AskCheck }) {
  const { t } = useTranslation();
  if (check.status !== "done" || !check.verdict) {
    return (
      <p className="text-muted-foreground text-xs">
        {t(check.status === "failed" ? "ask.visual.failed" : "ask.visual.skipped", {
          note: check.note,
        })}
      </p>
    );
  }
  return (
    <section
      className="bg-secondary/30 grid gap-2 rounded-lg border p-3"
      aria-label={t("ask.visual.title")}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Eye className="text-brand-azure size-4" aria-hidden />
        <span className="text-sm font-medium">{t("ask.visual.title")}</span>
        <Badge variant="secondary" className={cn("border-0", VERDICT_STYLE[check.verdict])}>
          {t(`ask.visual.verdict.${check.verdict}`)}
        </Badge>
      </div>
      <p className="text-sm leading-relaxed break-words">{check.note}</p>
      {check.frames.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {check.frames.map((frame) =>
            frame.thumb_url ? (
              <img
                key={`${frame.n.toString()}-${frame.t_s.toString()}`}
                src={frame.thumb_url}
                alt={t("ask.visual.frame", { n: frame.n, time: formatClock(frame.t_s) })}
                loading="lazy"
                className="aspect-video h-12 rounded object-cover"
              />
            ) : null,
          )}
        </div>
      ) : null}
    </section>
  );
}

/** A question and its answer, as kept: status notes, text, sources, visual check. */
export function AnswerView({ result, onAskAgain }: { result: AskResult; onAskAgain: () => void }) {
  const { t } = useTranslation();
  const seconds = ((result.timings.total_ms ?? 0) / 1000).toFixed(1);
  if (result.status === "no_passages") {
    return (
      <div className="grid gap-4">
        <h2 className="text-lg font-semibold break-words">{result.question}</h2>
        <EmptyState
          icon={<SearchX className="size-10" />}
          title={t("ask.status.noPassages.title")}
          body={t("ask.status.noPassages.body")}
          action={
            <Button variant="secondary" onClick={onAskAgain}>
              <RotateCcw className="size-4" aria-hidden />
              {t("ask.again")}
            </Button>
          }
        />
      </div>
    );
  }
  return (
    <article className="grid gap-4" aria-labelledby={`question-${result.id}`}>
      <h2 id={`question-${result.id}`} className="text-lg font-semibold break-words">
        {result.question}
      </h2>
      {result.status === "no_answer" ? (
        <Alert>
          <SearchX className="size-4" aria-hidden />
          <AlertDescription>{t("ask.status.noAnswer")}</AlertDescription>
        </Alert>
      ) : null}
      {result.status === "uncited" ? (
        <Alert>
          <AlertTriangle className="text-warning-ink size-4" aria-hidden />
          <AlertDescription>{t("ask.status.uncited")}</AlertDescription>
        </Alert>
      ) : null}
      {result.status === "failed" ? (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" aria-hidden />
          <AlertDescription>
            {t("ask.status.failed", { error: result.error ?? "" })}
          </AlertDescription>
        </Alert>
      ) : null}
      {result.answer ? <AnswerText text={result.answer} citations={result.citations} /> : null}
      {result.status === "cancelled" ? (
        <p className="text-muted-foreground text-xs">{t("ask.status.cancelled")}</p>
      ) : null}
      {result.truncated ? (
        <p className="text-muted-foreground text-xs">{t("ask.status.truncated")}</p>
      ) : null}
      <SourceList citations={result.citations} />
      {result.dropped_citations > 0 ? (
        <p className="text-muted-foreground text-xs">
          {t("ask.dropped", { count: result.dropped_citations })}
        </p>
      ) : null}
      {result.visual_check ? <VisualCheckNote check={result.visual_check} /> : null}
      <div className="text-muted-foreground flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs">
        <p>{t("ask.generated")}</p>
        <div className="flex items-center gap-3">
          <span className="font-mono">
            {t("ask.took", { model: result.model ?? "?", seconds })}
          </span>
          <Button variant="ghost" size="sm" onClick={onAskAgain}>
            <RotateCcw className="size-4" aria-hidden />
            {t("ask.again")}
          </Button>
        </div>
      </div>
    </article>
  );
}
