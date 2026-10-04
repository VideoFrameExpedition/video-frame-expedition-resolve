import type { ReactNode } from "react";

/** ``[start, end)`` in code points (the API sends pairs). */
export type Ranges = readonly (readonly number[])[];

/**
 * A plain-text snippet with the words the search matched marked. Ranges are code points
 * (``Array.from``), as the API counts them; the text is never read as HTML.
 */
export function Highlighted({ text, ranges }: { text: string; ranges: Ranges }) {
  const chars = Array.from(text);
  const parts: ReactNode[] = [];
  let cursor = 0;
  const pairs = ranges.map(([start = 0, end = 0]) => [start, end] as const);
  for (const [start, end] of pairs.sort((a, b) => a[0] - b[0])) {
    if (start < cursor || end <= start || end > chars.length) {
      continue;
    }
    if (start > cursor) {
      parts.push(chars.slice(cursor, start).join(""));
    }
    parts.push(
      <mark key={start} className="bg-brand-teal/25 text-foreground rounded-sm px-0.5">
        {chars.slice(start, end).join("")}
      </mark>,
    );
    cursor = end;
  }
  if (cursor < chars.length) {
    parts.push(chars.slice(cursor).join(""));
  }
  return <>{parts}</>;
}
