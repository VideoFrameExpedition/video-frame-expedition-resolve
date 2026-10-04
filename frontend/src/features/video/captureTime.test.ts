import { describe, expect, it } from "vitest";

import { formatCapture, sourceKey, type CaptureZone } from "./captureTime";

const ISO = "2024-06-01T14:30:00Z";
const UNKNOWN = "heure locale, fuseau inconnu";

function zone(overrides: Partial<CaptureZone>): CaptureZone {
  return {
    timeZone: null,
    offsetMin: null,
    source: "container",
    unknownZone: UNKNOWN,
    ...overrides,
  };
}

describe("formatCapture", () => {
  it("uses the place's time zone when known", () => {
    const text = formatCapture(
      ISO,
      "fr",
      zone({ timeZone: "Europe/Paris", offsetMin: 120 }),
      false,
    );
    expect(text).toContain("16:30");
    expect(text).not.toContain("(");
  });

  it("shifts by the known offset and shows it", () => {
    const text = formatCapture(ISO, "fr", zone({ offsetMin: -210 }), false);
    expect(text).toContain("11:00");
    expect(text).toMatch(/\(UTC−03:30\)$/);
  });

  it("labels true UTC instants as UTC", () => {
    for (const source of ["container", "containerStart", "gps", "withOffset"]) {
      const text = formatCapture(ISO, "fr", zone({ source }), false);
      expect(text).toContain("14:30");
      expect(text).toMatch(/\(UTC\)$/);
    }
  });

  it("shows wall-clock readings as stored, with an unknown zone", () => {
    for (const source of ["cameraLocal", "nameTime", "sidecar", "fileSystem", "other"]) {
      const text = formatCapture(ISO, "fr", zone({ source }), false);
      expect(text).toContain("14:30");
      expect(text).toMatch(/\(heure locale, fuseau inconnu\)$/);
    }
  });

  it("never adds a suffix to a date without time", () => {
    const text = formatCapture("2024-06-01T00:00:00Z", "fr", zone({ source: "nameDate" }), true);
    expect(text).toContain("2024");
    expect(text).not.toContain("(");
  });

  it("returns a dash for an invalid date", () => {
    expect(formatCapture("not a date", "fr", zone({}), false)).toBe("—");
  });
});

describe("sourceKey", () => {
  it("classifies capture-time sources", () => {
    expect(sourceKey("filename:datetime+Keys:AndroidTimeZone")).toBe("nameTimeOffset");
    expect(sourceKey("QuickTime:CreateDate-duration")).toBe("containerStart");
    expect(sourceKey("export_date")).toBe("export");
    expect(sourceKey(null)).toBe("none");
    expect(
      sourceKey("QuickTime:CreateDate", [{ source: "QuickTime:CreateDate", kind: "CAMERA_LOCAL" }]),
    ).toBe("cameraLocal");
  });

  it("keeps cards short", () => {
    const text = formatCapture(
      "2026-08-21T15:12:19Z",
      "fr",
      {
        timeZone: null,
        offsetMin: 120,
        source: "nameTimeInferred",
        unknownZone: "?",
        compact: true,
      },
      false,
    );
    expect(text).toContain("17:12");
    expect(text).not.toContain("UTC");
  });
});
