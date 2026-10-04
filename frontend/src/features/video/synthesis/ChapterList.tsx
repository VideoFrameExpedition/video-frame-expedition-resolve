import { ListOrdered } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { SynthesisChapter } from "@/api/client";
import { spanIndexAt } from "@/components/charts/scale";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { revealPlayer, usePlayer, usePlayheadSelector } from "../player";

/** The chapters of the video (none when it is a single one): a click plays from the start. */
export function ChapterList({
  chapters,
  model,
}: {
  chapters: SynthesisChapter[];
  model: string | null;
}) {
  const { t } = useTranslation();
  const { seek } = usePlayer();
  const starts = useMemo(() => chapters.map((chapter) => chapter.start_s), [chapters]);
  const current = usePlayheadSelector((time) => spanIndexAt(starts, time));
  if (chapters.length === 0) {
    return null;
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2 className="flex items-center gap-2">
            <ListOrdered className="text-brand-teal size-4" aria-hidden />
            {t("synthesis.chapters")}
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="-mx-2 grid gap-1">
          {chapters.map((chapter, index) => (
            <li key={chapter.index}>
              <button
                type="button"
                className={cn(
                  "hover:bg-accent/40 focus-visible:ring-ring grid w-full grid-cols-[auto_minmax(0,1fr)] gap-x-3 rounded-md px-2 py-2 text-left focus-visible:ring-2 focus-visible:outline-none",
                  index === current && "bg-accent/40",
                )}
                aria-current={index === current ? "true" : undefined}
                onClick={() => {
                  seek(chapter.start_s);
                  revealPlayer();
                }}
              >
                <span
                  className="bg-muted mt-0.5 flex size-6 items-center justify-center rounded-full text-xs font-semibold tabular-nums"
                  aria-hidden
                >
                  {chapter.index}
                </span>
                <span className="grid gap-0.5">
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    {/* Spaces between the parts: accessible names join them as they are. */}
                    <span className="sr-only">
                      {t("synthesis.chapter", { index: chapter.index })}
                    </span>{" "}
                    <span className="font-medium">{chapter.title}</span>{" "}
                    <span className="text-muted-foreground font-mono text-xs tabular-nums">
                      {formatClock(chapter.start_s)} – {formatClock(chapter.end_s)}
                    </span>
                  </span>{" "}
                  {chapter.summary ? (
                    <span className="text-muted-foreground text-sm text-pretty">
                      {chapter.summary}
                    </span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
        </ol>
        <p className="text-muted-foreground mt-3 text-xs">
          {model ? t("synthesis.writtenBy", { model }) : t("synthesis.writtenByNoModel")}
        </p>
      </CardContent>
    </Card>
  );
}
