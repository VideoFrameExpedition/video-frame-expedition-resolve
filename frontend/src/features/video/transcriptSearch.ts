/** Accent- and case-insensitive text for searching a transcript ("poele" finds "poêle"). */
export function foldText(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/œ/g, "oe")
    .replace(/æ/g, "ae");
}

interface Span {
  start_s: number;
  end_s: number;
}

/**
 * Segment being spoken at ``t``: the last one started, unless it already ended inside a longer
 * one still being spoken (a short second-pass segment laid over a first-pass one).
 */
export function activeSegmentAt(spans: readonly Span[], starts: readonly number[], t: number) {
  let index = -1;
  for (let lo = 0, hi = starts.length - 1; lo <= hi;) {
    const mid = (lo + hi) >> 1;
    if ((starts[mid] ?? Infinity) <= t) {
      index = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  const current = spans[index];
  if (!current || current.end_s >= t) return index;
  for (let j = index - 1; j >= Math.max(0, index - 8); j--) {
    if ((spans[j]?.end_s ?? -Infinity) > t) return j;
  }
  return index;
}
