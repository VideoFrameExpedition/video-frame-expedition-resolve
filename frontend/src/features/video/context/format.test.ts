import { describe, expect, it } from "vitest";

import { compassKey, localClock, localMinutes, phaseSegments, toDms } from "./format";

const PARIS = { timeZone: "Europe/Paris", offsetMin: null };

describe("localClock", () => {
  it("uses the place's zone, never the browser's", () => {
    expect(localClock("2026-08-26T18:20:42+00:00", PARIS, "fr")).toBe("20:20");
  });
  it("falls back to the device offset, then UTC", () => {
    expect(localClock("2026-08-26T18:20:42Z", { timeZone: null, offsetMin: 120 }, "fr")).toBe(
      "20:20",
    );
    expect(localClock("2026-08-26T18:20:42Z", { timeZone: null, offsetMin: null }, "fr")).toBe(
      "18:20",
    );
    expect(localClock(null, PARIS, "fr")).toBe("—");
  });
});

describe("compass and DMS", () => {
  it("rounds to 16 points", () => {
    expect(compassKey(260.4)).toBe("W");
    expect(compassKey(355)).toBe("N");
    expect(compassKey(55)).toBe("NE");
    expect(compassKey(-10)).toBe("N");
  });
  it("formats degrees, minutes, seconds", () => {
    expect(toDms(48.8584, "lat")).toBe("48°51′30.2″ N");
    expect(toDms(-33.8688, "lat")).toBe("33°52′07.7″ S");
    expect(toDms(2.2945, "lon")).toBe("2°17′40.2″ E");
    expect(toDms(-0.99999999, "lon")).toBe("1°00′00.0″ W");
  });
});

describe("phaseSegments", () => {
  const events = {
    rising: {
      astronomical: "2026-08-26T03:08:05Z",
      nautical: "2026-08-26T03:46:30Z",
      civil: "2026-08-26T04:22:35Z",
      blue_golden: "2026-08-26T04:34:14Z",
      sun: "2026-08-26T04:52:26Z",
      golden_day: "2026-08-26T05:30:53Z",
    },
    setting: {
      golden_day: "2026-08-26T17:42:24Z",
      blue_golden: "2026-08-26T18:38:50Z",
      sun: "2026-08-26T18:20:43Z",
      civil: "2026-08-26T18:50:28Z",
      nautical: "2026-08-26T19:26:21Z",
      astronomical: "2026-08-26T20:04:34Z",
    },
  };

  it("covers the local day without gaps, in the right order", () => {
    const segments = phaseSegments(events, PARIS, "day");
    expect(segments.map((s) => s.phase)).toEqual([
      "night",
      "astronomical_twilight",
      "nautical_twilight",
      "blue_hour",
      "golden_hour",
      "day",
      "golden_hour",
      "blue_hour",
      "nautical_twilight",
      "astronomical_twilight",
      "night",
    ]);
    expect(segments[0]?.start).toBe(0);
    expect(segments.at(-1)?.end).toBe(1440);
    for (let i = 1; i < segments.length; i++) {
      expect(segments[i]?.start).toBe(segments[i - 1]?.end);
    }
    expect(localMinutes("2026-08-26T17:42:24Z", PARIS)).toBe(19 * 60 + 42);
  });

  it("starts in daylight when the sun never sets far enough (white nights)", () => {
    const summer = {
      rising: { golden_day: "2026-06-21T03:00:00Z" },
      setting: { golden_day: "2026-06-21T20:00:00Z" },
    };
    const segments = phaseSegments(summer, { timeZone: "UTC", offsetMin: null }, "day");
    expect(segments.map((s) => s.phase)).toEqual(["golden_hour", "day", "golden_hour"]);
  });

  it("uses the capture phase for a polar day", () => {
    expect(phaseSegments({ rising: {}, setting: {} }, PARIS, "golden_hour")).toEqual([
      { phase: "golden_hour", start: 0, end: 1440 },
    ]);
  });
});
