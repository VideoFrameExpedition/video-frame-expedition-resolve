import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { Video } from "@/api/client";

import { type Chip, useContextChips } from "./useContextChips";

export function ChipRow({ chips, className }: { chips: Chip[]; className?: string }) {
  return (
    <span className={`flex flex-wrap items-center gap-1.5 ${className ?? ""}`}>
      {chips.map((chip) => (
        <span
          key={chip.key}
          className="bg-muted/60 text-foreground flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs"
        >
          {chip.icon}
          {chip.text}
        </span>
      ))}
    </span>
  );
}

/** Under the video title: the chips link to the Context tab. */
export function ContextChips({ video }: { video: Video }) {
  const { t } = useTranslation();
  const chips = useContextChips(video);
  if (!chips.length) return null;
  return (
    <Link
      to="/videos/$videoId"
      params={{ videoId: video.id }}
      search={{ tab: "context" }}
      title={t("context.openContext")}
      className="focus-visible:ring-ring w-fit rounded-full focus-visible:ring-2 focus-visible:outline-none"
    >
      <ChipRow chips={chips} />
    </Link>
  );
}
