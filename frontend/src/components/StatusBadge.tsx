import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleOff,
  CirclePause,
  CircleSlash,
  LoaderCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type { JobStatus, StageStatus, VideoStatus } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-secondary text-secondary-foreground",
  info: "bg-brand-azure/15 text-brand-azure",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  danger: "bg-destructive/15 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

const VIDEO_TONES: Record<VideoStatus, Tone> = {
  new: "muted",
  queued: "neutral",
  analyzing: "info",
  ready: "success",
  partial: "warning",
  failed: "danger",
  offline: "muted",
};

const JOB_TONES: Record<JobStatus, Tone> = {
  queued: "neutral",
  running: "info",
  succeeded: "success",
  partial: "warning",
  failed: "danger",
  cancelled: "muted",
};

const STAGE_TONES: Record<StageStatus, Tone> = {
  pending: "neutral",
  running: "info",
  succeeded: "success",
  cached: "success",
  skipped: "warning",
  failed: "danger",
  cancelled: "muted",
};

function Icon({ tone, spinning }: { tone: Tone; spinning: boolean }) {
  const className = "size-3.5";
  if (spinning) {
    return <LoaderCircle className={cn(className, "animate-spin")} aria-hidden />;
  }
  switch (tone) {
    case "success":
      return <CircleCheck className={className} aria-hidden />;
    case "warning":
      return <CirclePause className={className} aria-hidden />;
    case "danger":
      return <CircleAlert className={className} aria-hidden />;
    case "muted":
      return <CircleOff className={className} aria-hidden />;
    case "info":
      return <LoaderCircle className={className} aria-hidden />;
    case "neutral":
      return <CircleDashed className={className} aria-hidden />;
  }
}

function ToneBadge({
  tone,
  label,
  spinning = false,
}: {
  tone: Tone;
  label: string;
  spinning?: boolean;
}) {
  return (
    <Badge variant="secondary" className={cn("gap-1 border-0 font-medium", TONE_CLASSES[tone])}>
      <Icon tone={tone} spinning={spinning} />
      {label}
    </Badge>
  );
}

export function VideoStatusBadge({ status }: { status: VideoStatus }) {
  const { t } = useTranslation();
  return (
    <ToneBadge
      tone={VIDEO_TONES[status]}
      label={t(`status.${status}`)}
      spinning={status === "analyzing"}
    />
  );
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const { t } = useTranslation();
  return (
    <ToneBadge
      tone={JOB_TONES[status]}
      label={t(`jobStatus.${status}`)}
      spinning={status === "running"}
    />
  );
}

export function StageStatusBadge({ status }: { status: StageStatus }) {
  const { t } = useTranslation();
  if (status === "cancelled") {
    return (
      <Badge variant="secondary" className={cn("gap-1 border-0", TONE_CLASSES.muted)}>
        <CircleSlash className="size-3.5" aria-hidden />
        {t("stageStatus.cancelled")}
      </Badge>
    );
  }
  return (
    <ToneBadge
      tone={STAGE_TONES[status]}
      label={t(`stageStatus.${status}`)}
      spinning={status === "running"}
    />
  );
}
