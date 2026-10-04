import type { CSSProperties } from "react";

import type { Schemas } from "@/api/client";

export type Subject = Schemas["SubjectOut"];

/** One colour per kind of being, bright enough on any photograph (a dark outline helps). */
export const CATEGORY_COLORS: Record<string, string> = {
  person: "#ff3366",
  body_part: "#ff8a65",
  mammal: "#4ecdc4",
  bird: "#4aa3e8",
  insect: "#f5c542",
  other_animal: "#b18cff",
  face: "#fff176",
};

export function colorOf(category: string): string {
  return CATEGORY_COLORS[category] ?? "#ffffff";
}

/** CSS position of a 0–1 box inside its image. */
export function boxStyle(box: number[]): CSSProperties {
  const [x1 = 0, y1 = 0, x2 = 0, y2 = 0] = box;
  return {
    left: `${(x1 * 100).toFixed(2)}%`,
    top: `${(y1 * 100).toFixed(2)}%`,
    width: `${((x2 - x1) * 100).toFixed(2)}%`,
    height: `${((y2 - y1) * 100).toFixed(2)}%`,
  };
}

/** Stable identity of a subject within its frame (for React keys and the hover highlight). */
export function subjectKey(subject: Subject): string {
  return `${subject.category}-${subject.box.join(",")}`;
}
