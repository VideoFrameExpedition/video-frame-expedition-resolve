/** How the capture time of a video is displayed (see ``sourceKey`` in ``CaptureCard``). */

import { formatUtcOffset } from "@/lib/format";

/** ``exif:composite`` → ``exif`` (":" is the i18next namespace separator). */
export function locationKey(source: string | null | undefined): string {
  if (!source || source.startsWith("exif")) return "exif";
  return ["track", "folder_default"].includes(source) ? source : "other";
}

/** Source families whose stored time is a true UTC instant (the others are wall-clock readings). */
const UTC_SOURCES: ReadonlySet<string> = new Set([
  "container",
  "containerStart",
  "gps",
  "withOffset",
]);

/** Which family of evidence a capture-time source belongs to (for its human label). */
export function sourceKey(
  source: string | null | undefined,
  dates?: readonly { source: string; kind: string }[],
): string {
  if (!source || source === "none") return "none";
  if (source === "export_date") return "export";
  if (source === "filename:date") return "nameDate";
  if (source === "filename:datetime") return "nameTime";
  if (source === "filename:datetime+Keys:AndroidTimeZone") return "nameTimeOffset";
  if (source === "filename:datetime+timezone") return "nameTimeZone";
  if (source === "filename:datetime+inferred_offset") return "nameTimeInferred";
  if (source.endsWith("-duration")) return "containerStart";
  if (source.includes("GPS")) return "gps";
  if (source.startsWith("sidecar")) return "sidecar";
  const kind = dates?.find((date) => date.source === source)?.kind;
  switch (kind) {
    case "WITH_OFFSET":
      return "withOffset";
    case "CAMERA_LOCAL":
      return "cameraLocal";
    case "FILE_SYSTEM":
      return "fileSystem";
    default:
      return "container";
  }
}

export interface CaptureZone {
  /** IANA zone of the place, when known. */
  timeZone: string | null;
  /** UTC offset in minutes, when known. */
  offsetMin: number | null;
  /** Source family key (``sourceKey``), which tells a UTC instant from a wall-clock reading. */
  source: string;
  /** Label appended to a wall-clock time whose zone is unknown. */
  unknownZone: string;
  /** Short form for cards (medium date, no zone suffix: details live in the capture card). */
  compact?: boolean;
}

/**
 * Capture time in the place's zone, else shifted by the known UTC offset, else as stored — never
 * in the browser's zone, which says nothing about where the footage was shot. Without zone nor
 * offset, a UTC source is labelled "UTC" and a wall-clock source (camera clock, file name…)
 * ``unknownZone``: its digits are the local time of an unknown place, not UTC.
 */
export function formatCapture(
  iso: string,
  locale: string,
  zone: CaptureZone,
  dateOnly: boolean,
): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const dateStyle = zone.compact ? "medium" : "full";
  const style: Intl.DateTimeFormatOptions = dateOnly
    ? { dateStyle }
    : { dateStyle, timeStyle: "short" };
  if (zone.timeZone) {
    try {
      return date.toLocaleString(locale, { ...style, timeZone: zone.timeZone });
    } catch {
      // Unknown zone name for this browser: fall back to the offset below.
    }
  }
  const offset = zone.offsetMin ?? 0;
  const shifted = new Date(date.getTime() + offset * 60_000);
  const text = shifted.toLocaleString(locale, { ...style, timeZone: "UTC" });
  if (dateOnly || zone.compact) return text;
  if (zone.offsetMin !== null) return `${text} (${formatUtcOffset(offset)})`;
  return `${text} (${UTC_SOURCES.has(zone.source) ? "UTC" : zone.unknownZone})`;
}
