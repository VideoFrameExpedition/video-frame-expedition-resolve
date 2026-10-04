/** Colour-temperature ranges, mirrored from the backend ``domain/color.py`` (i18n keys here). */
const KELVIN_RANGES: readonly (readonly [number, string])[] = [
  [2000, "candle"],
  [3000, "tungsten"],
  [4000, "golden"],
  [5000, "morning"],
  [6500, "daylight"],
  [8000, "overcast"],
  [10000, "shade"],
  [Number.POSITIVE_INFINITY, "blueSky"],
];

export function kelvinKey(kelvin: number): string {
  return KELVIN_RANGES.find(([limit]) => kelvin < limit)?.[1] ?? "blueSky";
}

export function median(values: readonly number[]): number | null {
  const sorted = values.filter(Number.isFinite).toSorted((a, b) => a - b);
  if (sorted.length === 0) {
    return null;
  }
  const mid = sorted.length >> 1;
  return sorted.length % 2
    ? (sorted[mid] ?? null)
    : ((sorted[mid - 1] ?? 0) + (sorted[mid] ?? 0)) / 2;
}
