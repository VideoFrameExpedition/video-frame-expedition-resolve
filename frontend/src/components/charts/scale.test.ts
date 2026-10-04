import { describe, expect, it } from "vitest";

import {
  areaPath,
  breakGaps,
  clampTo,
  linearScale,
  linePath,
  logTicks,
  medianStep,
  nearestIndex,
  niceTicks,
  spanIndexAt,
  timeTicks,
} from "./scale";

describe("logTicks", () => {
  it("covers the values with 1, 2 and 5 of every power of ten", () => {
    expect(logTicks(2.8, 100)).toEqual([2, 5, 10, 20, 50, 100]);
    expect(logTicks(0.4, 3)).toEqual([0.2, 0.5, 1, 2, 5]);
    expect(logTicks(5, 5)).toEqual([5]);
    expect(logTicks(0, 10)).toEqual([]);
  });
});

describe("linearScale", () => {
  it("maps and inverts", () => {
    const x = linearScale([0, 10], [0, 200]);
    expect(x(5)).toBe(100);
    expect(x.invert(50)).toBe(2.5);
  });

  it("handles inverted ranges (SVG y axis)", () => {
    const y = linearScale([-60, 0], [100, 0]);
    expect(y(-60)).toBe(100);
    expect(y(0)).toBe(0);
    expect(y.invert(50)).toBe(-30);
  });
});

describe("ticks", () => {
  it("rounds to 1-2-5 steps", () => {
    expect(niceTicks(0, 1, 4)).toEqual([0, 0.5, 1]); // steps round up: few, recessive gridlines
    expect(niceTicks(3200, 6100, 3)).toEqual([3000, 4000, 5000, 6000, 7000]);
    expect(niceTicks(-58, -3, 3)).toEqual([-60, -40, -20, 0]);
    expect(niceTicks(0.8, 3.52, 2)).toEqual([0, 2, 4]); // covers the maximum
  });

  it("widens a flat series", () => {
    const ticks = niceTicks(5, 5);
    expect(ticks[0]).toBeLessThan(5);
    expect(ticks[ticks.length - 1]).toBeGreaterThan(5);
  });

  it("chooses clock steps that fit the width", () => {
    expect(timeTicks(20, 800)).toEqual([0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20]);
    expect(timeTicks(137.7, 600)).toEqual([0, 30, 60, 90, 120]);
    expect(timeTicks(0, 600)).toEqual([]);
  });
});

describe("lookups", () => {
  it("finds the nearest sample", () => {
    const xs = [0, 0.5, 1, 1.5];
    expect(nearestIndex(xs, 0.7)).toBe(1);
    expect(nearestIndex(xs, 0.76)).toBe(2);
    expect(nearestIndex(xs, 9)).toBe(3);
    expect(nearestIndex([], 1)).toBe(-1);
  });

  it("finds the span containing a time", () => {
    const starts = [0, 2, 5];
    expect(spanIndexAt(starts, 0)).toBe(0);
    expect(spanIndexAt(starts, 4.99)).toBe(1);
    expect(spanIndexAt(starts, 5)).toBe(2);
    expect(spanIndexAt(starts, -1)).toBe(-1);
  });

  it("measures the typical sample step, even for a series that starts late", () => {
    expect(medianStep([30, 31, 32, 33])).toBe(1); // t_last / (n - 1) would say 11
    expect(medianStep([0, 0.5, 1, 5, 5.5])).toBe(0.5); // robust to a hole
    expect(medianStep([4])).toBe(1);
    expect(medianStep([], 2)).toBe(2);
  });

  it("clamps values into a range", () => {
    expect(clampTo(-74, [-60, 0])).toBe(-60);
    expect(clampTo(3, [-60, 0])).toBe(0);
    expect(clampTo(-12, [0, -60])).toBe(-12);
  });
});

describe("gaps", () => {
  it("breaks the line across missing measurements", () => {
    const series = breakGaps([0, 1, 2, 6, 7], [5600, 5700, 5800, 3200, 3300], 1.5);
    expect(series.t).toEqual([0, 1, 2, 4, 6, 7]);
    expect(series.values).toEqual([5600, 5700, 5800, null, 3200, 3300]);
    const same = (v: number) => v;
    expect(linePath(series.t, series.values, same, same)).toBe(
      "M0.0,5600.0L1.0,5700.0L2.0,5800.0M6.0,3200.0L7.0,3300.0",
    );
  });

  it("leaves regular series untouched", () => {
    expect(breakGaps([0, 1, 2], [1, 2, 3], 1.5)).toEqual({ t: [0, 1, 2], values: [1, 2, 3] });
    expect(breakGaps([], [], 1.5)).toEqual({ t: [], values: [] });
  });
});

describe("paths", () => {
  const same = (v: number) => v;

  it("breaks lines on gaps", () => {
    expect(linePath([0, 1, 2, 3], [1, null, 2, 3], same, same)).toBe("M0.0,1.0M2.0,2.0L3.0,3.0");
  });

  it("closes areas on the baseline", () => {
    expect(areaPath([0, 1], [2, 3], same, same, 10)).toBe("M0.0,10.0L0.0,2.0L1.0,3.0L1.0,10.0Z");
  });
});
