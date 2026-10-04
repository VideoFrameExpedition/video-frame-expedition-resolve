import { CircleAlert, CircleCheck, CircleHelp } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ExifSummary, VideoDetail } from "@/api/client";
import { useMetadata } from "@/api/queries";
import { Fact } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber, formatUtcOffset } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatCapture, locationKey, sourceKey } from "./captureTime";

const CONFIDENCE = {
  high: { icon: CircleCheck, className: "bg-success/15 text-success" },
  medium: { icon: CircleHelp, className: "bg-brand-azure/15 text-brand-azure" },
  low: { icon: CircleAlert, className: "bg-warning/15 text-warning" },
} as const;

type ConfidenceLevel = keyof typeof CONFIDENCE;

function isConfidence(value: string | null | undefined): value is ConfidenceLevel {
  return value === "high" || value === "medium" || value === "low";
}

/** Up to 5 decimals (about 1 m), without padding zeros that would fake precision. */
function formatCoordinate(value: number): string {
  return value.toLocaleString("en", { maximumFractionDigits: 5, useGrouping: false });
}

function exposureLine(exif: ExifSummary, locale: string): string | null {
  const parts: string[] = [];
  if (exif.iso) parts.push(`ISO ${formatNumber(exif.iso, locale)}`);
  if (exif.f_number) parts.push(`f/${formatNumber(exif.f_number, locale, 1)}`);
  if (exif.exposure_time) {
    parts.push(
      exif.exposure_time < 1
        ? `1/${Math.round(1 / exif.exposure_time)} s`
        : `${formatNumber(exif.exposure_time, locale, 1)} s`,
    );
  }
  if (exif.focal_length_mm) parts.push(`${formatNumber(exif.focal_length_mm, locale)} mm`);
  return parts.length ? parts.join(" · ") : null;
}

export function CaptureCard({ video }: { video: VideoDetail }) {
  const { t, i18n } = useTranslation();
  const metadata = useMetadata(video.id);
  const locale = i18n.language;
  const exif = metadata.data?.exif;
  const confidence = isConfidence(video.captured_at_confidence)
    ? video.captured_at_confidence
    : "low";
  const Icon = CONFIDENCE[confidence].icon;
  const source = sourceKey(video.captured_at_source, exif?.dates);
  const dateOnly = video.captured_at_source === "filename:date";
  const camera = [video.camera_make, video.camera_model].filter(Boolean).join(" ");
  const offset = video.capture_utc_offset_min;
  const warnings = exif?.warnings ?? [];
  const exposure = exif ? exposureLine(exif, locale) : null;
  const software = exif?.software ?? exif?.reencoded_by ?? video.encoder;
  const exported = exif?.exported_by_editor ?? false;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("capture.title")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-1.5">
          <p className="text-sm font-medium">
            {video.captured_at && source !== "export"
              ? formatCapture(
                  video.captured_at,
                  locale,
                  {
                    timeZone: video.capture_timezone,
                    offsetMin: offset,
                    source,
                    unknownZone: t("capture.unknownZone"),
                  },
                  dateOnly,
                )
              : t("capture.unknownDate")}
          </p>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge
              variant="outline"
              className={cn("gap-1 border-0", CONFIDENCE[confidence].className)}
            >
              <Icon className="size-3.5" aria-hidden />
              {t(`capture.confidence.${confidence}`)}
            </Badge>
            <span className="text-muted-foreground">{t(`capture.source.${source}`)}</span>
          </div>
          {video.capture_timezone ? (
            <p className="text-muted-foreground text-xs">
              {t("capture.timezone", {
                zone:
                  offset === null
                    ? video.capture_timezone
                    : `${video.capture_timezone} (${formatUtcOffset(offset)})`,
              })}
            </p>
          ) : offset !== null ? (
            <p className="text-muted-foreground text-xs">
              {t(source === "nameTimeInferred" ? "capture.offsetInferred" : "capture.offset", {
                offset: formatUtcOffset(offset),
              })}
            </p>
          ) : null}
          {(["possible_pause", "name_mismatch"] as const)
            .filter((code) => warnings.includes(code))
            .map((code) => (
              <p key={code} className="text-muted-foreground text-xs">
                {t(`capture.warning.${code}`)}
              </p>
            ))}
          {warnings.includes("utc_offset_mismatch") && exif?.utc_offset_min != null ? (
            <p className="text-warning text-xs">
              {t("capture.offsetMismatch", { offset: formatUtcOffset(exif.utc_offset_min) })}
            </p>
          ) : null}
          {exported ? (
            <p className="text-muted-foreground text-xs">{t("capture.exportedNote")}</p>
          ) : null}
        </div>

        <dl className="divide-border divide-y">
          <Fact
            label={t("capture.camera")}
            value={
              camera || video.camera_os ? (
                <span className="grid">
                  <span>{camera || t("common.unknown")}</span>
                  {video.camera_os ? (
                    <span className="text-muted-foreground text-xs">
                      {video.camera_model
                        ? video.camera_os
                        : t("capture.modelNotRecorded", { os: video.camera_os })}
                    </span>
                  ) : null}
                </span>
              ) : (
                t("common.unknown")
              )
            }
          />
          {exif?.lens ? <Fact label={t("capture.lens")} value={exif.lens} /> : null}
          {exposure ? <Fact label={t("capture.exposure")} value={exposure} /> : null}
          <Fact
            label={t("capture.location")}
            value={
              video.latitude !== null && video.longitude !== null ? (
                <span className="grid">
                  <span className="font-mono text-xs">
                    {formatCoordinate(video.latitude)}, {formatCoordinate(video.longitude)}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {t(`capture.locationSource.${locationKey(video.location_source)}`)}
                    {video.altitude_m !== null
                      ? ` · ${formatNumber(video.altitude_m, locale, 0, "m")}`
                      : ""}
                  </span>
                </span>
              ) : (
                t("capture.noLocation")
              )
            }
          />
          {exif?.track ? (
            <Fact
              label={t("capture.track")}
              value={t("capture.trackValue", {
                points: exif.track.points,
                distance: formatNumber(exif.track.distance_m / 1000, locale, 2, "km"),
              })}
            />
          ) : null}
          {software ? <Fact label={t("capture.software")} value={software} /> : null}
          {exif && exif.sidecars.length > 0 ? (
            <Fact label={t("capture.sidecars")} value={exif.sidecars.join(", ")} />
          ) : null}
        </dl>

        {metadata.isPending ? <Skeleton className="h-5 w-40" /> : null}
        {exif && exif.dates.length > 0 ? (
          <details className="text-sm">
            <summary className="text-muted-foreground cursor-pointer text-xs">
              {t("capture.candidates", { count: exif.dates.length })}
            </summary>
            <ul className="mt-2 grid gap-1">
              {exif.dates.map((date) => (
                <li key={`${date.source}-${date.value}`} className="grid text-xs">
                  <span className="font-mono">{date.value}</span>
                  <span className="text-muted-foreground">
                    {date.source} · {t(`capture.kind.${date.kind}`, { defaultValue: date.kind })}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        <RawTags videoId={video.id} />
      </CardContent>
    </Card>
  );
}

function RawTags({ videoId }: { videoId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="justify-self-start"
        onClick={() => {
          setOpen(true);
        }}
      >
        {t("capture.showRaw")}
      </Button>
    );
  }
  return <RawTagList videoId={videoId} />;
}

function RawTagList({ videoId }: { videoId: string }) {
  const { t } = useTranslation();
  const raw = useMetadata(videoId, true);
  if (raw.isPending) return <Skeleton className="h-32" />;
  const entries = Object.entries(raw.data?.raw_tags ?? {}).filter(
    ([key]) => !key.startsWith("SourceFile"),
  );
  if (entries.length === 0) {
    return <p className="text-muted-foreground text-xs">{t("capture.noRaw")}</p>;
  }
  return (
    <div className="grid gap-1">
      <p className="text-muted-foreground text-xs">
        {t("capture.rawCount", { count: entries.length })}
      </p>
      <dl className="bg-muted/50 max-h-72 overflow-auto rounded-md p-2 font-mono text-[0.7rem] leading-relaxed">
        {entries.map(([key, value]) => (
          <div key={key} className="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)] gap-2">
            <dt className="text-muted-foreground truncate" title={key}>
              {key}
            </dt>
            <dd className="break-words">
              {typeof value === "string" ? value : JSON.stringify(value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
