import { ChevronLeft, ChevronRight, EyeOff } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type BenchRun } from "@/api/client";
import { useRateBench } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatClock } from "@/lib/format";

import { blindOrder, LETTERS, MARKS, ratingProgress } from "./benchFormat";

/** « Blind rating »: one frame of the run and what each model said of it, under letters
 * that change with the frame; the marks (0–3) become the « Quality » column. */
export function BlindRating({ run }: { run: BenchRun }) {
  const { t } = useTranslation();
  const rate = useRateBench(run.id);
  const [index, setIndex] = useState(0);
  const position = Math.min(index, run.frames.length - 1);
  const frame = run.frames[position];
  if (!frame) {
    return null;
  }
  const answered = Object.keys(frame.answers).filter((key) => frame.answers[key]?.ok);
  const order = blindOrder(run.id, frame.keyframe_id, answered);
  const progress = ratingProgress(run);
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <EyeOff className="text-brand-teal size-4" aria-hidden />
          {t("bench.blind.title")}
        </CardTitle>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-xs">
            {t("bench.blind.rated", { count: progress.rated, total: progress.total })}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={position === 0}
            onClick={() => {
              setIndex(position - 1);
            }}
          >
            <ChevronLeft className="size-4" />
            {t("bench.blind.previous")}
          </Button>
          <span className="text-sm tabular-nums">
            {t("bench.blind.image", { index: position + 1, total: run.frames.length })}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={position >= run.frames.length - 1}
            onClick={() => {
              setIndex(position + 1);
            }}
          >
            {t("bench.blind.next")}
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-muted-foreground max-w-prose text-sm">{t("bench.blind.intro")}</p>
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <figure className="grid gap-1 lg:sticky lg:top-20">
            <img
              src={frame.image_url}
              alt={t("bench.blind.imageAlt", { index: position + 1 })}
              className="max-h-[32rem] w-full rounded-lg bg-black/40 object-contain"
            />
            <figcaption className="text-muted-foreground truncate text-xs">
              {t("bench.blind.at", { file: frame.filename, time: formatClock(frame.t_s) })}
            </figcaption>
          </figure>
          {order.length === 0 ? (
            <p className="text-muted-foreground text-sm">{t("bench.blind.failed")}</p>
          ) : (
            <ol className="grid gap-3">
              {order.map((key, rank) => {
                const answer = frame.answers[key];
                const letter = LETTERS[rank] ?? "?";
                const mark = run.ratings[frame.keyframe_id]?.[key];
                if (!answer) {
                  return null;
                }
                return (
                  <li key={key} className="bg-card grid gap-2 rounded-xl border p-3">
                    <p className="font-medium">
                      <Badge variant="secondary" className="mr-2">
                        {letter}
                      </Badge>
                      {answer.caption}
                    </p>
                    <p className="text-sm">{answer.description}</p>
                    {answer.subjects.length > 0 ? (
                      <p className="text-muted-foreground text-xs">
                        {t("bench.blind.subjects")} : {answer.subjects.join(" ; ")}
                      </p>
                    ) : null}
                    <p className="text-muted-foreground text-xs">
                      {t("bench.blind.text")} :{" "}
                      {answer.visible_text ? `« ${answer.visible_text} »` : t("bench.blind.noText")}
                    </p>
                    <div
                      role="group"
                      aria-label={t("bench.blind.answer", { letter })}
                      className="flex flex-wrap gap-2"
                    >
                      {MARKS.map((value) => (
                        <Button
                          key={value}
                          size="sm"
                          variant={mark === value ? "default" : "secondary"}
                          aria-pressed={mark === value}
                          onClick={() => {
                            rate.mutate(
                              {
                                keyframe_id: frame.keyframe_id,
                                model: key,
                                rating: mark === value ? null : value,
                              },
                              { onError: (error) => toast.error(errorMessage(error)) },
                            );
                          }}
                        >
                          {t(`bench.blind.marks.${value}`)}
                        </Button>
                      ))}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
