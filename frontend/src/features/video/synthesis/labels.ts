import type { TFunction } from "i18next";

import type { Clip, Synthesis, SynthesisSuggestion } from "@/api/client";
import { formatNumber } from "@/lib/format";

/** Whether the synthesis holds anything written for the video (a previous run's, maybe). */
export function hasSynthesis(data: Synthesis): boolean {
  return (
    [data.title, data.logline, data.summary].some(Boolean) ||
    data.chapters.length > 0 ||
    data.highlights.length > 0
  );
}

/*
 * The synthesis API writes its short labels in French (usability reasons, the
 * criteria of a highlight, the notes of a clip). The known ones are translated here; an
 * unknown one (a newer backend) is shown as the API wrote it.
 */

/** Usability reasons (backend ``domain/usability.py``). */
const REASONS: Record<string, string> = {
  "très court": "veryShort",
  court: "short",
  instable: "unstable",
  noir: "black",
  "image figée": "frozen",
  sombre: "dark",
  surexposé: "overexposed",
  "sous-exposé": "underexposed",
  "hautes lumières brûlées": "clippedHighlights",
  "moins net que le reste": "softer",
  flou: "blur",
  "flou de bougé": "motionBlur",
  "sujet flou": "subjectBlur",
  obstruction: "obstruction",
  bruit: "noise",
  "horizon penché": "tiltedHorizon",
};

/** Why code ranked a highlight (backend ``services/synthesis.py``, ``_criteria``). */
const CRITERIA: Record<string, string> = {
  "plan fort": "hero",
  action: "action",
  parole: "speech",
};

/** Notes of a clip that carry no number (backend ``domain/editing.py``, ``clip_for``). */
const NOTES: Record<string, string> = {
  "entrée déplacée entre deux mots": "inMoved",
  "sortie déplacée entre deux mots": "outMoved",
  "parole continue : coupe dans un mot inévitable": "inWord",
};

export function reasonLabel(t: TFunction, reason: string): string {
  const key = REASONS[reason];
  return key ? t(`synthesis.reason.${key}`) : reason;
}

export function criterionLabel(t: TFunction, criterion: string): string {
  const usable = /^utilisable (\d+)$/.exec(criterion);
  if (usable) {
    return t("synthesis.criterion.usable", { score: Number(usable[1]) });
  }
  const key = CRITERIA[criterion];
  return key ? t(`synthesis.criterion.${key}`) : criterion;
}

/** « 1.4 s »; « 0.06 s » under a tenth (never « 0.0 s »), like the API's notes. */
function offset(value: number, locale: string): string {
  return formatNumber(value, locale, value >= 0.095 ? 1 : 2, "s");
}

/** The notes of a clip in the interface language; a J-cut or L-cut is written from its times. */
export function clipNotes(t: TFunction, clip: Clip, locale: string): string[] {
  return clip.notes.map((note) => {
    if (note.endsWith("(J-cut)") && clip.sound_in_s !== null) {
      return t("synthesis.note.jCut", {
        seconds: offset(clip.picture_in_s - clip.sound_in_s, locale),
      });
    }
    if (note.endsWith("(L-cut)") && clip.sound_out_s !== null) {
      return t("synthesis.note.lCut", {
        seconds: offset(clip.sound_out_s - clip.picture_out_s, locale),
      });
    }
    const key = NOTES[note];
    return key ? t(`synthesis.note.${key}`) : note;
  });
}

/** The sound range of a clip when it differs from the picture's (J-cut, L-cut), else null. */
export function soundRange(clip: Clip): [number, number] | null {
  if (clip.sound_in_s === null && clip.sound_out_s === null) {
    return null;
  }
  const start = clip.sound_in_s ?? clip.picture_in_s;
  const end = clip.sound_out_s ?? clip.picture_out_s;
  return start === clip.picture_in_s && end === clip.picture_out_s ? null : [start, end];
}

export type UsabilityLevel = "good" | "fair" | "poor";

/** Display bands of the 0–100 score (50 is where the API stops picking highlights). */
export function usabilityLevel(score: number): UsabilityLevel {
  return score >= 80 ? "good" : score >= 50 ? "fair" : "poor";
}

export type EditingRole = SynthesisSuggestion["role"];

export const EDITING_ROLES: readonly EditingRole[] = ["establishing", "b_roll", "avoid"];

/** The editing roles suggested for each shot (by shot index), once each, in a fixed order. */
export function rolesByShot(
  suggestions: readonly SynthesisSuggestion[],
): Map<number, EditingRole[]> {
  const byShot = new Map<number, Set<EditingRole>>();
  for (const suggestion of suggestions) {
    // A block of several shots is scored as a whole: « avoid » on a merged block would
    // contradict the good score of one of its shots, so only single-shot blocks get a chip.
    if (suggestion.shots.length !== 1) continue;
    for (const shot of suggestion.shots) {
      const roles = byShot.get(shot) ?? new Set<EditingRole>();
      roles.add(suggestion.role);
      byShot.set(shot, roles);
    }
  }
  return new Map(
    [...byShot].map(([shot, roles]) => [shot, EDITING_ROLES.filter((role) => roles.has(role))]),
  );
}
