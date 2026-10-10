import {
  formatBytes,
  formatClock,
  formatDuration,
  formatPercent,
  formatSpan,
  formatUtcOffset,
  splitAfterSeparators,
} from "./format";

describe("formatClock", () => {
  it.each([
    [0, false, "00:00"],
    [83.5, false, "01:23"],
    [83.5, true, "01:23.500"],
    [3725, false, "1:02:05"],
    [59.9996, true, "01:00.000"],
  ])("formats %s (millis=%s) as %s", (seconds, millis, expected) => {
    expect(formatClock(seconds, millis)).toBe(expected);
  });

  it("returns a dash for missing or invalid values", () => {
    expect(formatClock(null)).toBe("—");
    expect(formatClock(Number.NaN)).toBe("—");
    expect(formatClock(-1)).toBe("—");
  });
});

describe("formatBytes", () => {
  it("uses the units of the language", () => {
    expect(formatBytes(512)).toBe("512\u00a0o");
    expect(formatBytes(50_790_884)).toBe("48,4\u00a0Mo");
    expect(formatBytes(50_790_884, "en")).toBe("48.4\u00a0MB");
    expect(formatBytes(2 ** 30, "en")).toBe("1\u00a0GB");
  });
});

describe("formatDuration / formatPercent", () => {
  it("formats durations and clamps percentages", () => {
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(12_300)).toBe("12.3 s");
    expect(formatPercent(1.2)).toBe("100\u00a0%");
  });
});

describe("formatSpan", () => {
  it.each([
    [null, "—"],
    [44.6, "45 s"],
    [12 * 60 + 10, "12 min"],
    [2 * 3600 + 5 * 60, "2 h 05"],
    [3 * 3600, "3 h"],
  ])("formats %s seconds as %s", (seconds, text) => {
    expect(formatSpan(seconds)).toBe(text);
  });
});

describe("formatUtcOffset", () => {
  it("formats signed offsets", () => {
    expect(formatUtcOffset(120)).toBe("UTC+02:00");
    expect(formatUtcOffset(-210)).toBe("UTC\u221203:30");
    expect(formatUtcOffset(345)).toBe("UTC+05:45");
    expect(formatUtcOffset(0)).toBe("UTC+00:00");
  });
});

describe("splitAfterSeparators", () => {
  it("offers line breaks after underscores and hyphens only", () => {
    expect(splitAfterSeparators("20200726_113722.mp4")).toEqual(["20200726_", "113722.mp4"]);
    expect(splitAfterSeparators("AFM DEMO court.mp4")).toEqual(["AFM DEMO court.mp4"]);
    expect(splitAfterSeparators("a-b_c")).toEqual(["a-", "b_", "c"]);
  });
});
