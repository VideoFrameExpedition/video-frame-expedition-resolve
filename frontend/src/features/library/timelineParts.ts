/**
 * What goes with a timeline made from the ticked videos, as the user last chose it:
 * remembered in this browser, the defaults when nothing (or nothing readable) is stored.
 */

import type { Schemas } from "@/api/client";

export interface TimelineParts {
  transcript: boolean; // what is said, as subtitles (their own track)
  suggestions: boolean; // the stretches the application suggests, as duration markers
  chapters: boolean; // where chapters start, as markers
  shots: boolean; // what each shot shows, as subtitles (their own track)
}

export const PART_NAMES = ["transcript", "suggestions", "chapters", "shots"] as const;
export type PartName = (typeof PART_NAMES)[number];

export const DEFAULT_PARTS: TimelineParts = {
  transcript: true,
  suggestions: true,
  chapters: true,
  shots: false,
};

const KEY = "vfe.timelineBuild.parts";

export function readParts(): TimelineParts {
  try {
    const raw = window.localStorage.getItem(KEY);
    const stored: unknown = raw ? JSON.parse(raw) : null;
    if (typeof stored !== "object" || stored === null) return DEFAULT_PARTS;
    const parts = { ...DEFAULT_PARTS };
    for (const name of PART_NAMES) {
      const value: unknown = (stored as Record<string, unknown>)[name];
      if (typeof value === "boolean") parts[name] = value;
    }
    return parts;
  } catch {
    return DEFAULT_PARTS;
  }
}

export function saveParts(parts: TimelineParts): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(parts));
  } catch {
    // private window or blocked storage: the choice lasts while the dialog is open
  }
}

type Plan = Schemas["TimelinePlanOut"];

export const SUGGESTION_KINDS = ["highlights", "establishing", "b_roll", "avoid"] as const;

/** How many of each the timeline would carry (the preview counts them, asked or not). */
export function partCount(plan: Plan | undefined, name: PartName): number {
  if (!plan) return 0;
  switch (name) {
    case "transcript":
      return plan.speech_subtitles;
    case "suggestions":
      return SUGGESTION_KINDS.reduce((sum, kind) => sum + plan.suggestions[kind], 0);
    case "chapters":
      return plan.chapters;
    case "shots":
      return plan.shot_subtitles;
  }
}
