import { ScanEye } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type Schemas } from "@/api/client";
import { useProbeVision, useVisionProfile } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber } from "@/lib/format";

type Status = Schemas["VisionStatus"];
type Convention = Schemas["BoxConvention"];

function order(convention: Convention): string {
  if (convention.startsWith("xywh")) return "[x, y, w, h]";
  if (convention.startsWith("cxcywh")) return "[cx, cy, w, h]";
  return convention.startsWith("yxyx") ? "[y1, x1, y2, x2]" : "[x1, y1, x2, y2]";
}

function scaleKey(convention: Convention): string {
  if (convention.endsWith("_px")) return "system.visionModel.scalePx";
  if (convention.endsWith("_unit")) return "system.visionModel.scaleUnit";
  return "system.visionModel.scale1000";
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/** How the loaded vision model answers, as measured by the calibration. */
export function VisionCard() {
  const { t, i18n } = useTranslation();
  const status = useVisionProfile();
  const probe = useProbeVision();
  const data = status.data;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <CardTitle className="flex items-center gap-2 text-base">
          <ScanEye className="text-brand-teal size-4" aria-hidden />
          {t("system.visionModel.title")}
          {data?.display_name ? (
            <span className="text-muted-foreground font-normal">· {data.display_name}</span>
          ) : null}
        </CardTitle>
        <Button
          variant="secondary"
          size="sm"
          disabled={!data?.model || probe.isPending}
          onClick={() => {
            probe.mutate(undefined, {
              onSuccess: () => toast.success(t("system.visionModel.queued")),
              onError: (error) => toast.error(errorMessage(error)),
            });
          }}
        >
          {t("system.visionModel.recalibrate")}
        </Button>
      </CardHeader>
      <CardContent>
        {status.isPending ? (
          <Skeleton className="h-20 rounded-lg" />
        ) : !data?.model ? (
          <p className="text-muted-foreground text-sm">
            {data?.lmstudio_error
              ? t("system.visionModel.unreachable")
              : t("system.visionModel.noModel")}
          </p>
        ) : (
          <dl className="grid gap-2">{lines(data, t, i18n.language)}</dl>
        )}
      </CardContent>
    </Card>
  );
}

function lines(
  data: Status,
  t: (key: string, options?: Record<string, unknown>) => string,
  locale: string,
) {
  const profile = data.profile;
  const decimal = (value: number | null | undefined): string =>
    value == null ? "—" : formatNumber(value, locale, 2);
  if (!profile) {
    return (
      <Row
        label={t("system.visionModel.positions")}
        value={data.prior ? t("system.visionModel.prior") : t("system.visionModel.notCalibrated")}
      />
    );
  }
  const g = profile.grounding;
  const when = new Date(profile.probed_at).toLocaleString(locale, {
    dateStyle: "short",
    timeStyle: "short",
  });
  const reasoning = profile.reasoning_tokens_seen
    ? t("system.visionModel.reasoningOn", { count: profile.reasoning_tokens_seen })
    : data.reasoning_capable
      ? t("system.visionModel.reasoningOff")
      : t("system.visionModel.reasoningNone");
  const positions =
    g.enabled && g.convention
      ? t("system.visionModel.verified", {
          order: order(g.convention),
          scale: t(scaleKey(g.convention)),
          field: g.box_field,
          iou: decimal(g.mean_iou),
        }) + (g.precise ? "" : ` ${t("system.visionModel.approximate")}`)
      : t("system.visionModel.disabled", { reason: g.reason ?? "—" });
  return (
    <>
      <Row
        label={t("system.visionModel.answers")}
        value={
          profile.truncated ? t("system.visionModel.truncated") : t("system.visionModel.complete")
        }
      />
      <Row label={t("system.visionModel.reasoning")} value={reasoning} />
      <Row label={t("system.visionModel.positions")} value={positions} />
      <Row
        label={t("system.visionModel.measured")}
        value={t("system.visionModel.measuredValue", {
          when,
          seconds: formatNumber(profile.wall_ms / 1000, locale, 1),
        })}
      />
    </>
  );
}
