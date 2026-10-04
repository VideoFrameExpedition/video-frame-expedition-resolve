import { Link } from "@tanstack/react-router";
import { Clapperboard } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatClock, formatDateTime, formatNumber } from "@/lib/format";

type ResolveLink = Schemas["ResolveLinkOut"];
type ResolveUse = Schemas["ResolveUseOut"];

/** « DaVinci Resolve »: the timelines of the library that use the video, and where.
 * Identifiers last; positions are a reading dated by the timeline's last update. */
export function ResolveCard({ links }: { links: ResolveLink[] }) {
  const { t } = useTranslation();
  if (links.length === 0) {
    return null;
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Clapperboard className="text-brand-teal size-4" aria-hidden />
          {t("timelines.links.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {links.map((link) => (
          <LinkUses key={link.bin_id} link={link} />
        ))}
      </CardContent>
    </Card>
  );
}

function LinkUses({ link }: { link: ResolveLink }) {
  const { t, i18n } = useTranslation();
  // The picture's uses; the sound's only when the picture is not used (linked audio doubles
  // every clip).
  const picture = link.uses.filter((use) => use.track_type === "video");
  const shown = picture.length > 0 ? picture : link.uses;
  const sound = link.uses.length - shown.length;
  return (
    <div className="grid gap-1.5 text-sm">
      <Link
        to="/library"
        search={{ timeline: link.bin_id }}
        title={t("timelines.links.open", { label: link.bin_label })}
        className="hover:text-brand-teal focus-visible:ring-ring w-fit rounded font-medium break-words focus-visible:ring-2 focus-visible:outline-none"
      >
        {link.project.name} › {link.timeline.name}
      </Link>
      <ul className="grid gap-1 text-xs">
        {shown.map((use) => (
          <UseRow
            key={`${use.track_type}${String(use.track)}:${String(use.record_in_s)}`}
            use={use}
          />
        ))}
      </ul>
      {sound > 0 ? (
        <p className="text-muted-foreground text-xs">
          {t("timelines.links.audioUses", { count: sound })}
        </p>
      ) : null}
      <p className="text-muted-foreground text-xs">
        {t("timelines.links.syncedAt", { date: formatDateTime(link.synced_at, i18n.language) })}
      </p>
    </div>
  );
}

/** « V1 · 01:00:03:12 → 01:00:07:02 · source 2.50 → 6.80 s », and « disabled » when the clip
 * or its track is. */
function UseRow({ use }: { use: ResolveUse }) {
  const { t, i18n } = useTranslation();
  const seconds = (value: number): string => formatNumber(value, i18n.language, 2);
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span className="font-mono font-medium" title={use.track_name ?? undefined}>
        {t(`timelines.links.track.${use.track_type}`, { track: use.track })}
      </span>
      <span className="font-mono">
        {use.record_in_tc ?? formatClock(use.record_in_s, true)} →{" "}
        {use.record_out_tc ?? formatClock(use.record_out_s, true)}
      </span>
      <span className="text-muted-foreground">
        {t("timelines.links.source", {
          from: seconds(use.source_in_s),
          to: seconds(use.source_out_s),
        })}
      </span>
      {use.nested_in ? (
        <span className="text-muted-foreground">
          {t("timelines.links.nested", { name: use.nested_in })}
        </span>
      ) : null}
      {!use.enabled || !use.track_enabled ? (
        <Badge variant="outline">{t("timelines.links.disabled")}</Badge>
      ) : null}
    </li>
  );
}
