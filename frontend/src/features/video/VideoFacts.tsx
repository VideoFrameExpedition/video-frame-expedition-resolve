import { RotateCw } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type VideoDetail } from "@/api/client";
import { useAnalyze, useStages } from "@/api/queries";
import { Fact } from "@/components/common";
import { StageStatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { hardDependents, hasResult, isHeavy } from "@/features/library/stagePlan";
import { cn } from "@/lib/utils";
import { formatBytes, formatClock, formatDuration, formatNumber } from "@/lib/format";

export function VideoFacts({ video }: { video: VideoDetail }) {
  const { t, i18n } = useTranslation();
  const hdr = video.hdr_format ?? (video.is_hdr ? t("video.hdr") : null);
  const color = video.color_profile
    ? t(`video.colorProfile.${video.color_profile}`, { defaultValue: video.color_profile })
    : hdr
      ? `${hdr} (${video.color_transfer ?? "?"})`
      : `SDR${video.color_transfer ? ` (${video.color_transfer})` : ""}`;
  // Capture rate vs playback rate: slow motion (> 1) or time-lapse (< 1).
  const speed = video.capture_fps && video.fps ? video.capture_fps / video.fps : null;
  const speedLabel =
    speed === null || Math.abs(speed - 1) <= 0.1
      ? null
      : speed > 1
        ? t("video.slowMotion", {
            factor: formatNumber(speed, i18n.language, speed < 10 ? 1 : 0),
            fps: formatNumber(video.capture_fps, i18n.language),
          })
        : t("video.timeLapse", { factor: formatNumber(1 / speed, i18n.language, 0) });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("video.facts")}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="divide-border divide-y">
          <Fact label={t("video.duration")} value={formatClock(video.duration_s)} />
          <Fact
            label={t("video.resolution")}
            value={video.width && video.height ? `${video.width} × ${video.height}` : "—"}
          />
          <Fact
            label={t("video.fps")}
            value={
              video.fps === null
                ? "—"
                : `${formatNumber(video.fps, i18n.language, Number.isInteger(video.fps) ? 0 : 3)}${
                    video.is_vfr ? ` (${t("video.vfr")})` : ""
                  }`
            }
          />
          {speedLabel ? <Fact label={t("video.speed")} value={speedLabel} /> : null}
          <Fact
            label={t("video.codec")}
            value={[video.video_codec?.toUpperCase(), hdr].filter(Boolean).join(" · ") || "—"}
          />
          <Fact label={t("video.color")} value={color} />
          <Fact
            label={t("video.audio")}
            value={
              video.has_audio
                ? (video.audio_codec?.toUpperCase() ?? t("common.yes"))
                : t("video.noAudio")
            }
          />
          <Fact label={t("video.timecode")} value={video.start_timecode ?? "—"} />
          <Fact label={t("video.size")} value={formatBytes(video.size_bytes, i18n.language)} />
          <Fact
            label={t("video.file")}
            value={<span className="font-mono text-xs break-all">{video.path}</span>}
          />
        </dl>
      </CardContent>
    </Card>
  );
}

export function StagesCard({ video }: { video: VideoDetail }) {
  const { t } = useTranslation();
  const stages = useStages();
  const catalogue = stages.data ?? [];
  if (video.stages.length === 0) {
    return null;
  }
  // What a rerun of ``stage`` redoes with it: the stages needing its result that have one.
  const redoneWith = (stage: string): string[] => {
    const dependents = hardDependents(catalogue, new Set([stage]));
    const redone = new Set([stage, ...dependents]);
    return dependents.filter((name) => {
      const run = video.stages.find((r) => r.stage === name);
      return run !== undefined && hasResult(run, redone);
    });
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("video.stages")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {video.stages.map((run) => (
          <div key={run.stage} className="grid gap-1">
            <div className="flex items-center justify-between gap-2 text-sm">
              <span>{t(`stage.${run.stage}`, { defaultValue: run.stage })}</span>
              <span className="flex shrink-0 items-center gap-2">
                <span className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                  {formatDuration(run.duration_ms)}
                </span>
                <StageStatusBadge status={run.status} />
                <RerunStage
                  videoId={video.id}
                  stage={run.stage}
                  redone={redoneWith(run.stage)}
                  heavy={isHeavy(catalogue, redoneWith(run.stage))}
                  // Without the catalogue, what else would be redone is unknown: wait for it.
                  disabled={video.status === "offline" || run.status === "running" || !stages.data}
                />
              </span>
            </div>
            {(run.error ?? run.skip_reason) ? (
              <p className="text-muted-foreground text-xs">{run.error ?? run.skip_reason}</p>
            ) : null}
          </div>
        ))}
        <p className="text-muted-foreground border-t pt-3 text-xs">{t("video.rerunHint")}</p>
      </CardContent>
    </Card>
  );
}

/** Redo one stage now, with the stages that need its result (``redone``): when there are
 * some, the user confirms first, since they may include the vision model or Whisper. */
function RerunStage({
  videoId,
  stage,
  redone,
  heavy,
  disabled,
}: {
  videoId: string;
  stage: string;
  redone: string[];
  heavy: boolean;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const analyze = useAnalyze(videoId);
  const name = t(`stage.${stage}`, { defaultValue: stage });
  const label = t("video.rerun", { stage: name });
  const rerun = (): void => {
    analyze.mutate(
      { stages: [stage], mode: "full" },
      {
        onSuccess: () => {
          setConfirming(false);
          toast.success(t("video.rerunQueued", { stage: name }));
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            ref={button}
            variant="ghost"
            size="icon-xs"
            aria-label={label}
            disabled={disabled || analyze.isPending}
            onClick={() => {
              if (redone.length > 0) setConfirming(true);
              else rerun();
            }}
          >
            <RotateCw className={cn(analyze.isPending && "animate-spin")} />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            // Opened without a trigger: focus goes back to this row's ↻ button.
            event.preventDefault();
            button.current?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>{t("video.rerunConfirmTitle", { stage: name })}</DialogTitle>
            <DialogDescription>{t("video.rerunConfirmBody")}</DialogDescription>
          </DialogHeader>
          <ul className="grid list-disc gap-1 pl-5 text-sm">
            {redone.map((dep) => (
              <li key={dep}>{t(`stage.${dep}`, { defaultValue: dep })}</li>
            ))}
          </ul>
          {heavy ? <p className="text-warning text-sm">{t("video.rerunHeavy")}</p> : null}
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
            <Button type="button" onClick={rerun} disabled={analyze.isPending}>
              {t("video.rerunConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
