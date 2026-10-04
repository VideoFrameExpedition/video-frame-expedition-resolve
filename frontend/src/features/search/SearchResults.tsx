import { Link } from "@tanstack/react-router";
import { Film } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SearchHit } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Highlighted } from "./Highlighted";
import type { Hit, VideoGroup } from "./searchState";

const KIND_STYLE: Record<SearchHit["kind"], string> = {
  video: "bg-brand-azure/15 text-brand-azure",
  chapter: "bg-primary/15 text-primary",
  shot: "bg-brand-teal/15 text-brand-teal",
  keyframe: "bg-secondary text-secondary-foreground",
  transcript: "bg-warning/15 text-warning",
};

function when(hit: Hit): string | null {
  if (hit.t_start === null) return null;
  const start = formatClock(hit.t_start);
  const end = hit.t_end !== null ? formatClock(hit.t_end) : null;
  return end && end !== start ? `${start} – ${end}` : start;
}

function HitRow({ hit, filename }: { hit: Hit; filename: string }) {
  const { t } = useTranslation();
  const time = when(hit);
  const label =
    hit.t_start !== null
      ? t("search.openAt", { name: filename, time: formatClock(hit.t_start) })
      : t("search.open", { name: filename });
  const found = hit.retrievers.map((r) => t(`search.by.${r}`)).join(" · ");
  return (
    <li>
      <Link
        to="/videos/$videoId"
        params={{ videoId: hit.video_id }}
        search={hit.t_start !== null ? { t: Math.round(hit.t_start * 1000) / 1000 } : {}}
        aria-label={label}
        className="hover:bg-secondary/60 focus-visible:ring-ring flex gap-3 rounded-lg p-2 transition-colors focus-visible:ring-2 focus-visible:outline-none"
      >
        <div className="relative aspect-video w-28 shrink-0 self-start overflow-hidden rounded-md bg-black sm:w-36">
          {hit.thumb_url ? (
            <img src={hit.thumb_url} alt="" loading="lazy" className="size-full object-cover" />
          ) : (
            <div className="text-muted-foreground flex size-full items-center justify-center">
              <Film className="size-5" aria-hidden />
            </div>
          )}
          {time ? (
            <span className="absolute right-1 bottom-1 rounded bg-black/70 px-1 font-mono text-[0.65rem] text-white">
              {formatClock(hit.t_start)}
            </span>
          ) : null}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-muted-foreground mb-1 flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="secondary" className={cn("border-0", KIND_STYLE[hit.kind])}>
              {t(`search.kind.${hit.kind}`)}
              {hit.kind === "shot" && hit.shot_idx !== null ? ` ${hit.shot_idx + 1}` : ""}
            </Badge>
            {time ? <span className="font-mono">{time}</span> : null}
            {found ? <span title={t("search.foundBy", { how: found })}>{found}</span> : null}
          </div>
          <p className="text-sm leading-relaxed break-words">
            <Highlighted text={hit.snippet} ranges={hit.highlights} />
          </p>
        </div>
      </Link>
    </li>
  );
}

export function SearchResults({ groups }: { groups: VideoGroup[] }) {
  return (
    <div className="grid gap-4">
      {groups.map((group) => (
        <Card key={group.videoId} className="gap-2 py-3">
          <CardHeader className="px-4">
            <CardTitle className="min-w-0 text-base">
              <Link
                to="/videos/$videoId"
                params={{ videoId: group.videoId }}
                className="hover:text-brand-teal block truncate"
                title={group.filename}
              >
                {group.name}
              </Link>
              {group.name !== group.filename ? (
                <span className="text-muted-foreground block truncate text-xs font-normal">
                  {group.filename}
                </span>
              ) : null}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-2">
            <ul className="grid gap-1">
              {group.hits.map((hit) => (
                <HitRow key={hit.chunk_id} hit={hit} filename={group.filename} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
