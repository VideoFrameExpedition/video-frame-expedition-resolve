import { Hourglass } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "@/api/client";
import { cn } from "@/lib/utils";

import { skippedLines, useSlow } from "./timelines";

/** « DaVinci Resolve is taking a while to answer… » once a read of Resolve has lasted 3 s. */
export function SlowResolve({ pending, className }: { pending: boolean; className?: string }) {
  const { t } = useTranslation();
  const slow = useSlow(pending);
  return (
    // Always in the page (out of the layout until needed): screen readers announce the text.
    <p
      aria-live="polite"
      className={cn(
        "text-muted-foreground flex items-center gap-1.5 text-xs",
        !slow && "sr-only",
        className,
      )}
    >
      {slow ? (
        <>
          <Hourglass className="size-3.5 shrink-0" aria-hidden />
          {t("timelines.slow")}
        </>
      ) : null}
    </p>
  );
}

/** What a read of the timeline left out: titles, compound clips, audio files… (names on hover). */
export function SkippedList({
  skipped,
  errors,
}: {
  skipped: Schemas["SkippedItemsOut"];
  errors: number;
}) {
  const { t } = useTranslation();
  const lines = skippedLines(t, skipped, errors);
  if (lines.length === 0) return null;
  return (
    <div className="text-muted-foreground grid gap-1 text-xs">
      <p>{t("timelines.skippedTitle")}</p>
      <ul className="grid list-disc gap-0.5 pl-5">
        {lines.map((line) => (
          <li key={line.kind} title={line.names.length ? line.names.join(", ") : undefined}>
            {line.text}
          </li>
        ))}
      </ul>
    </div>
  );
}
