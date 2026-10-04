import { Link } from "@tanstack/react-router";
import { Check, Clapperboard, Film } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Video } from "@/api/client";
import { OrientationBadge } from "@/components/common";
import { VideoStatusBadge } from "@/components/StatusBadge";
import { formatCapture, sourceKey } from "@/features/video/captureTime";
import { ChipRow } from "@/features/video/context/ContextChips";
import { useContextChips } from "@/features/video/context/useContextChips";
import { formatClock, splitAfterSeparators } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Shooting date for the card: only when known (never the export or library date). */
function shootingDate(video: Video, locale: string, unknownZone: string): string | null {
  const source = sourceKey(video.captured_at_source);
  if (!video.captured_at || source === "export" || source === "none") return null;
  return formatCapture(
    video.captured_at,
    locale,
    {
      timeZone: video.capture_timezone,
      offsetMin: video.capture_utc_offset_min,
      source,
      unknownZone,
      compact: true,
    },
    video.captured_at_source === "filename:date",
  );
}

export function VideoCard({
  video,
  selected = false,
  onSelect,
}: {
  video: Video;
  selected?: boolean;
  /** Ticked or unticked; ``range``: shift was held (extend from the last ticked card). */
  onSelect?: (on: boolean, range: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const vertical = video.orientation === "vertical";
  const shot = shootingDate(video, i18n.language, t("capture.unknownZone"));
  const size = video.width && video.height ? `${video.width}×${video.height}` : null;
  const chips = useContextChips(video);
  return (
    // The hover lift lives on the wrapper, so the card and its tick box move together.
    <div className="group relative transition-transform hover:-translate-y-0.5">
      <Link
        to="/videos/$videoId"
        params={{ videoId: video.id }}
        className={cn(
          "bg-card group-hover:border-brand-teal/50 focus-visible:ring-ring block overflow-hidden rounded-xl border transition-all group-hover:shadow-lg focus-visible:ring-2 focus-visible:outline-none",
          selected && "border-primary ring-primary/50 ring-2",
        )}
      >
        <div className="relative aspect-video overflow-hidden bg-black">
          {video.poster_url ? (
            <img
              src={video.poster_url}
              alt=""
              loading="lazy"
              className={cn(
                "size-full transition-transform duration-300 group-hover:scale-[1.03]",
                vertical ? "object-contain" : "object-cover",
              )}
            />
          ) : (
            <div className="text-muted-foreground flex size-full items-center justify-center">
              <Film className="size-8" aria-hidden />
            </div>
          )}
          <div className="absolute top-2 right-2">
            <OrientationBadge orientation={video.orientation} />
          </div>
          {video.timeline_bins.length > 0 ? (
            // Used in Resolve timelines of the library.
            <span
              role="img"
              aria-label={t("library.inTimelines", { count: video.timeline_bins.length })}
              title={t("library.inTimelines", { count: video.timeline_bins.length })}
              className="absolute bottom-2 left-2 flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 font-mono text-xs text-white"
            >
              <Clapperboard className="size-3" aria-hidden />
              {video.timeline_bins.length}
            </span>
          ) : null}
          {video.duration_s ? (
            <span className="absolute right-2 bottom-2 rounded bg-black/70 px-1.5 py-0.5 font-mono text-xs text-white">
              {formatClock(video.duration_s)}
            </span>
          ) : null}
        </div>
        <div className="grid gap-2 p-3">
          <div className="flex items-start justify-between gap-2">
            <h3 className="line-clamp-2 text-sm font-medium break-words" title={video.filename}>
              {splitAfterSeparators(video.title ?? video.filename).map((part, index) =>
                index === 0 ? part : [<wbr key={index} />, part],
              )}
            </h3>
            <VideoStatusBadge status={video.status} />
          </div>
          {video.summary ? (
            <p className="text-muted-foreground line-clamp-2 text-xs">{video.summary}</p>
          ) : null}
          {size || shot ? (
            <p className="text-muted-foreground text-xs">
              {[size, shot ?? t("library.dateUnknown")].filter(Boolean).join(" · ")}
            </p>
          ) : null}
          {chips.length ? <ChipRow chips={chips} /> : null}
        </div>
      </Link>
      {onSelect ? (
        // A sibling of the link (a checkbox inside a link would open the video). The input
        // covers the whole tile, so a shift-click anywhere on it reaches the checkbox. Hidden
        // on small screens, where the side panel with the « Analyse » button is hidden too.
        <div
          className={cn(
            "absolute top-1.5 left-1.5 z-10 hidden size-8 rounded-lg backdrop-blur-sm transition-colors md:block",
            selected ? "bg-primary/90" : "bg-black/55 hover:bg-black/75",
          )}
        >
          <input
            type="checkbox"
            checked={selected}
            onMouseDown={(event) => {
              if (event.shiftKey) {
                event.preventDefault(); // no text selection across cards…
                event.currentTarget.focus({ preventScroll: true }); // …but focus follows the click
              }
            }}
            onChange={(event) => {
              const click = event.nativeEvent as Partial<MouseEvent>;
              onSelect(event.target.checked, click.shiftKey === true);
            }}
            aria-label={t("library.selectVideo", { name: video.title ?? video.filename })}
            className="focus-visible:ring-ring absolute inset-0 size-full cursor-pointer appearance-none rounded-lg focus-visible:ring-2 focus-visible:outline-none"
          />
          <span
            aria-hidden
            className={cn(
              "pointer-events-none absolute inset-2 flex items-center justify-center rounded border-2",
              selected ? "border-white bg-white" : "border-white/80",
            )}
          >
            {selected ? <Check className="text-primary size-3.5" strokeWidth={3} /> : null}
          </span>
        </div>
      ) : null}
    </div>
  );
}
