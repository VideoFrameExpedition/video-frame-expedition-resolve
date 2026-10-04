import { describe, expect, it } from "vitest";

import { kelvinKey, median } from "./kelvin";

describe("kelvin", () => {
  it("mirrors the backend ranges", () => {
    expect(kelvinKey(1800)).toBe("candle");
    expect(kelvinKey(3200)).toBe("golden");
    expect(kelvinKey(5600)).toBe("daylight");
    expect(kelvinKey(6500)).toBe("overcast");
    expect(kelvinKey(12000)).toBe("blueSky");
  });

  it("computes medians", () => {
    expect(median([5, 1, 3])).toBe(3);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([])).toBeNull();
  });
});
