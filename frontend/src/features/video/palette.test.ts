import { describe, expect, it } from "vitest";

import { mergeColorShares } from "./palette";

describe("mergeColorShares", () => {
  it("drops empty clusters and merges duplicate hex colours", () => {
    expect(
      mergeColorShares([
        { hex: "#112233", share: 0.3 },
        { hex: "#aabbcc", share: 0.25 },
        { hex: "#AABBCC", share: 0.125 },
        { hex: "#000000", share: 0 },
        { hex: "#ffffff", share: -0.1 },
        { hex: "#123456", share: Number.NaN },
      ]),
    ).toEqual([
      { hex: "#aabbcc", share: 0.375 },
      { hex: "#112233", share: 0.3 },
    ]);
  });

  it("keeps an already clean palette as is", () => {
    const colors = [
      { hex: "#102030", share: 0.6 },
      { hex: "#405060", share: 0.4 },
    ];
    expect(mergeColorShares(colors)).toEqual(colors);
    expect(mergeColorShares([])).toEqual([]);
  });
});
