/** Dominant colours of a frame as returned by the backend (k-means clusters in Lab space). */

export interface ColorShare {
  hex: string;
  share: number;
}

/**
 * Colours ready to draw: empty clusters (share of 0) dropped, clusters that round to the same
 * hex merged (shares summed, case-insensitive), largest share first.
 */
export function mergeColorShares(colors: readonly ColorShare[]): ColorShare[] {
  const byHex = new Map<string, ColorShare>();
  for (const { hex, share } of colors) {
    if (!(share > 0)) {
      continue; // also skips NaN
    }
    const key = hex.toLowerCase();
    const seen = byHex.get(key);
    byHex.set(key, { hex: seen?.hex ?? hex, share: (seen?.share ?? 0) + share });
  }
  return [...byHex.values()].toSorted((a, b) => b.share - a.share);
}
