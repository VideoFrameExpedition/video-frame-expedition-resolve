import type { TFunction } from "i18next";
import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Ear,
  History,
  ImageIcon,
  Info,
  Loader2,
  RotateCw,
  Sparkles,
  Sun,
  type LucideIcon,
} from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  errorMessage,
  type Synthesis,
  type SynthesisTag,
  type WeatherConsensus,
} from "@/api/client";
import { useAnalyze } from "@/api/queries";
import { HintBadge } from "@/components/HintBadge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import { hasSynthesis } from "./labels";

const WEATHER_ICON: Record<string, LucideIcon> = {
  clear: Sun,
  partly_cloudy: CloudSun,
  overcast: Cloud,
  fog: CloudFog,
  drizzle: CloudDrizzle,
  rain: CloudRain,
  snow: CloudSnow,
  thunderstorm: CloudLightning,
};

const TAG_ICON: Record<SynthesisTag["source"], LucideIcon> = {
  llm: Sparkles,
  frames: ImageIcon,
  sounds: Ear,
};

/** The generated texts of the video: title, logline, summary, keywords, and the weather. */
export function SynthesisCard({
  videoId,
  data,
  offline,
}: {
  videoId: string;
  data: Synthesis;
  offline: boolean;
}) {
  const { t } = useTranslation();
  const analyze = useAnalyze(videoId);
  const [confirming, setConfirming] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  // Ready with nothing written still shows the card (« no text »), and « Regenerate ».
  const filled = hasSynthesis(data) || data.status === "ready";
  const running = data.status === "running";
  const hasText = [data.title, data.logline, data.summary].some(Boolean);

  const run = (): void => {
    analyze.mutate(
      { stages: ["synthesis"], mode: "full" },
      {
        onSuccess: () => {
          setConfirming(false);
          toast.success(t("synthesis.queued"));
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };
  const disabled = offline || running || analyze.isPending;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2 className="flex items-center gap-2">
            <Sparkles className="text-brand-teal size-4" aria-hidden />
            {t("synthesis.title")}
          </h2>
        </CardTitle>
        {filled ? (
          <CardAction>
            <Button
              ref={button}
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => {
                setConfirming(true);
              }}
            >
              <RotateCw className={cn(analyze.isPending && "animate-spin")} aria-hidden />
              {t("synthesis.regenerate")}
            </Button>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-4">
        {filled ? (
          <>
            <StatusLine data={data} />
            {data.stale || data.weather?.category ? (
              <div className="flex flex-wrap items-center gap-2">
                {data.stale ? (
                  <HintBadge
                    hint={t("synthesis.staleHint")}
                    className="bg-warning/15 text-warning-ink"
                  >
                    <History aria-hidden />
                    {t("synthesis.stale")}
                  </HintBadge>
                ) : null}
                {data.weather?.category ? <WeatherBadge weather={data.weather} /> : null}
              </div>
            ) : null}
            {hasText ? (
              <div className="grid gap-2">
                {data.title ? (
                  <h3 className="text-xl font-semibold text-balance">{data.title}</h3>
                ) : null}
                {data.logline ? (
                  <p className="text-muted-foreground text-pretty italic">{data.logline}</p>
                ) : null}
                {data.summary ? (
                  <p className="leading-relaxed text-pretty">{data.summary}</p>
                ) : null}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">{t("synthesis.empty")}</p>
            )}
            {data.tags.length > 0 ? <Tags tags={data.tags} /> : null}
            <p className="text-muted-foreground border-t pt-3 text-xs">
              {data.model
                ? t("synthesis.footnote", { model: data.model })
                : t("synthesis.footnoteNoModel")}
            </p>
          </>
        ) : (
          <Missing data={data} disabled={disabled} onRun={run} />
        )}
      </CardContent>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            // Opened without a trigger: focus goes back to « Regenerate ».
            event.preventDefault();
            button.current?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>{t("synthesis.confirmTitle")}</DialogTitle>
            <DialogDescription>{t("synthesis.confirmBody")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setConfirming(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button type="button" onClick={run} disabled={analyze.isPending}>
              {t("synthesis.regenerate")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

/** Above a synthesis on screen: a new one is being written, or the last run did not succeed. */
function StatusLine({ data }: { data: Synthesis }) {
  const { t } = useTranslation();
  if (data.status === "running") {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-xs" role="status">
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        {t("synthesis.regenerating")}
      </p>
    );
  }
  if (data.status === "failed" || data.status === "skipped") {
    return (
      <p className="text-muted-foreground text-xs" role="status">
        {t("synthesis.previous", { reason: reasonOf(t, data) })}
      </p>
    );
  }
  return null;
}

/** Why the last run wrote nothing, in words: a cancelled run has no message of its own. */
function reasonOf(t: TFunction, data: Synthesis): string {
  if (data.note === "cancelled") {
    return t("synthesis.cancelled");
  }
  return data.note ?? t("synthesis.noReason");
}

/** A skip a retry cannot fix (no shot, nothing described or said): the stage's own notes. */
function nothingToSummarise(data: Synthesis): boolean {
  return data.status === "skipped" && (data.note?.includes("rien à résumer") ?? false);
}

/** No synthesis yet: why (not generated, running, skipped, failed), and how to get one. */
function Missing({
  data,
  disabled,
  onRun,
}: {
  data: Synthesis;
  disabled: boolean;
  onRun: () => void;
}) {
  const { t } = useTranslation();
  if (data.status === "running") {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        {t("synthesis.running")}
      </p>
    );
  }
  const notRun = data.status === "not_run";
  return (
    <div className="grid justify-items-start gap-3">
      <div className="flex gap-2.5">
        <Info className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
        <div className="grid gap-1" role="status">
          <p className="text-sm font-medium">
            {notRun
              ? t("synthesis.notRun")
              : data.status === "failed"
                ? t("synthesis.failed", { reason: reasonOf(t, data) })
                : t("synthesis.skipped", { reason: reasonOf(t, data) })}
          </p>
          {notRun ? (
            <p className="text-muted-foreground text-sm">{t("synthesis.notRunBody")}</p>
          ) : null}
          {nothingToSummarise(data) ? (
            <p className="text-muted-foreground text-sm">{t("synthesis.nothingHint")}</p>
          ) : null}
        </div>
      </div>
      {nothingToSummarise(data) ? null : (
        <Button size="sm" variant="secondary" disabled={disabled} onClick={onRun}>
          <Sparkles aria-hidden />
          {notRun ? t("synthesis.generate") : t("synthesis.retry")}
        </Button>
      )}
    </div>
  );
}

/** Keywords as chips, each with a small sign of where it comes from (and a legend for them). */
function Tags({ tags }: { tags: SynthesisTag[] }) {
  const { t } = useTranslation();
  const sources = (["llm", "frames", "sounds"] as const).filter((source) =>
    tags.some((tag) => tag.source === source),
  );
  return (
    <div className="grid gap-2">
      <ul className="flex flex-wrap gap-1.5" aria-label={t("synthesis.tags")}>
        {tags.map((tag, index) => {
          const Icon = TAG_ICON[tag.source];
          const source = t(`synthesis.tagSource.${tag.source}`);
          return (
            <li
              key={`${String(index)}-${tag.label}`} // a label may come from two sources
              className="bg-muted/60 inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs"
              title={source}
            >
              <Icon className="text-muted-foreground size-3" aria-hidden />
              {tag.label}
              <span className="sr-only"> ({source})</span>
            </li>
          );
        })}
      </ul>
      {sources.length > 1 ? (
        <p className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-hidden>
          {sources.map((source) => {
            const Icon = TAG_ICON[source];
            return (
              <span key={source} className="inline-flex items-center gap-1">
                <Icon className="size-3" />
                {t(`synthesis.tagSource.${source}`)}
              </span>
            );
          })}
        </p>
      ) : null}
    </div>
  );
}

/** The weather both sources agree on (or not): category and confidence, the full line on demand. */
function WeatherBadge({ weather }: { weather: WeatherConsensus }) {
  const { t } = useTranslation();
  const category = weather.category ?? "";
  const Icon = WEATHER_ICON[category] ?? Cloud;
  return (
    <HintBadge
      variant="outline"
      className="gap-1.5"
      hint={
        <span className="grid gap-1 text-left">
          <span>{t(`synthesis.weather.agreement.${weather.agreement}`)}</span>{" "}
          <span>{weather.line}</span>
        </span>
      }
    >
      <span className="sr-only">{t("synthesis.weather.label")}</span> <Icon aria-hidden />
      {t(`context.weatherCategory.${category}`, { defaultValue: category })}{" "}
      <span className="text-muted-foreground font-normal">
        · {t(`synthesis.weather.confidence.${weather.confidence}`)}
      </span>
    </HintBadge>
  );
}
