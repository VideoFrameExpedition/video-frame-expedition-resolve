import type { TFunction } from "i18next";
import { useEffect, useState } from "react";

import type { Schemas } from "@/api/client";

/**
 * The DaVinci Resolve timelines of the library: what their reads and their files say,
 * in words. The interface never says « bin » for them (Resolve's bins are left alone).
 */

type Preview = Schemas["TimelinePreviewOut"];
type Skipped = Schemas["SkippedItemsOut"];
type TimelineBin = Schemas["TimelineBinOut"];

/** Past this, a read of DaVinci Resolve still going says so (it takes up to several seconds). */
export const SLOW_MS = 3000;

/** Whether ``pending`` has lasted ``delayMs`` without a break. */
export function useSlow(pending: boolean, delayMs = SLOW_MS): boolean {
  const [late, setLate] = useState(false);
  useEffect(() => {
    if (!pending) return undefined;
    const handle = window.setTimeout(() => {
      setLate(true);
    }, delayMs);
    return () => {
      window.clearTimeout(handle);
      setLate(false);
    };
  }, [pending, delayMs]);
  return pending && late;
}

/** The files of a timeline by what adding it would do, in reading order. */
const PREVIEW_PARTS = [
  "in_library",
  "other_path",
  "in_folder",
  "new_folder",
  "missing",
  "unknown",
] as const;

/** The preview's sentence, its parts with a count only: « 98 already in the library · … ». */
export function previewParts(t: TFunction, preview: Preview): string[] {
  return PREVIEW_PARTS.filter((part) => preview[part] > 0).map((part) =>
    t(`timelines.add.part.${part}`, { count: preview[part] }),
  );
}

export interface SkippedLine {
  kind: string;
  text: string;
  names: string[]; // the items' names, when Resolve gave them
}

/** What a read of the timeline left out, a line per kind with a count. */
export function skippedLines(t: TFunction, skipped: Skipped, errors: number): SkippedLine[] {
  const kinds: [string, number, string[]][] = [
    ["graphics", skipped.graphics, skipped.graphics_names],
    ["containers", skipped.containers, skipped.container_names],
    ["unsupported", skipped.unsupported, skipped.unsupported_names],
    ["not_video", skipped.not_video, skipped.not_video_names],
    ["elsewhere", skipped.elsewhere, skipped.elsewhere_names],
    ["errors", errors, []],
  ];
  return kinds
    .filter(([, count]) => count > 0)
    .map(([kind, count, names]) => ({
      kind,
      text: t(`timelines.skipped.${kind}`, { count }),
      names,
    }));
}

/** The states of a timeline's files that call for a look (the bar's « Details »). */
export const ATTENTION_STATES = [
  "not_processed",
  "removed",
  "missing",
  "outside",
  "error",
] as const;

/** Whether its update job is under way: waiting, or registering its files. */
export function isSyncing(bin: TimelineBin): boolean {
  return bin.sync_job?.status === "queued" || bin.sync_job?.status === "running";
}

/** The bar's summary of a timeline's files: those in the library, the update under way, then
 * each state calling for a look (states with no file left out). */
export function stateParts(t: TFunction, bin: TimelineBin): string[] {
  const parts = [t("timelines.bar.state.in_library", { count: bin.states.in_library })];
  const job = bin.sync_job;
  if (job?.status === "queued") {
    parts.push(t("timelines.bar.state.queued"));
  } else if (job?.status === "running" || bin.states.adding > 0) {
    const progress = Math.max(0, Math.min(1, job?.progress ?? 0));
    parts.push(
      t("timelines.bar.state.adding", {
        done: Math.round(progress * bin.items),
        total: bin.items,
      }),
    );
  }
  for (const state of ATTENTION_STATES) {
    const count = bin.states[state];
    if (count > 0) parts.push(t(`timelines.bar.state.${state}`, { count }));
  }
  return parts;
}

/** Names that would read the same in the list: their project is shown next to them. */
export function sameLabels(bins: readonly TimelineBin[]): Set<string> {
  const seen = new Map<string, number>();
  for (const bin of bins) {
    const key = bin.label.toLocaleLowerCase();
    seen.set(key, (seen.get(key) ?? 0) + 1);
  }
  return new Set([...seen].filter(([, count]) => count > 1).map(([key]) => key));
}
