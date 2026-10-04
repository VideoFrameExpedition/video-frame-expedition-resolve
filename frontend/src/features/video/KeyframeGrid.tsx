import { useTranslation } from "react-i18next";

import type { Keyframe } from "@/api/client";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

export function KeyframeGrid({
  frames,
  selectedId,
  currentId,
  onSelect,
}: {
  frames: Keyframe[];
  selectedId: string | undefined;
  currentId: string | undefined;
  onSelect: (frame: Keyframe) => void;
}) {
  const { t } = useTranslation();
  return (
    <ol className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">
      {frames.map((frame) => {
        const caption = frame.analysis?.data.caption;
        return (
          <li key={frame.id}>
            <button
              type="button"
              onClick={() => {
                onSelect(frame);
              }}
              className={cn(
                "group bg-card w-full overflow-hidden rounded-lg border text-left transition-all",
                "hover:border-brand-teal/60 focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                selectedId === frame.id && "border-brand-coral ring-brand-coral/40 ring-2",
              )}
              aria-pressed={selectedId === frame.id}
            >
              <div className="relative aspect-video bg-black">
                <img
                  src={frame.thumb_url}
                  alt=""
                  loading="lazy"
                  className="size-full object-contain"
                />
                <span
                  className={cn(
                    "absolute bottom-1 left-1 rounded px-1 font-mono text-[0.65rem] text-white",
                    currentId === frame.id ? "bg-brand-coral" : "bg-black/70",
                  )}
                >
                  {formatClock(frame.t_s, true)}
                </span>
                {frame.selection_reason === "scene" ? (
                  <span
                    className="bg-brand-teal absolute top-1 right-1 size-2 rounded-full"
                    title={t("video.reason.scene")}
                  />
                ) : null}
              </div>
              <p
                className={cn(
                  "line-clamp-2 p-2 text-xs",
                  !caption && "text-muted-foreground italic",
                )}
              >
                {caption ?? t("video.notDescribed")}
              </p>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
