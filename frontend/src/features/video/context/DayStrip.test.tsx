import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DayStrip } from "./DayStrip";

const SEGMENTS = [
  { phase: "night", start: 0, end: 300 },
  { phase: "golden_hour", start: 300, end: 400 },
  { phase: "day", start: 400, end: 1440 },
] as const;

describe("DayStrip", () => {
  it("describes the bands and the capture in words, and lists only the phases present", () => {
    render(
      <DayStrip segments={[...SEGMENTS]} capture={1078} windowMinutes={30} takeMinutes={null} />,
    );
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toMatch(/05:00–06:40/);
    expect(label).toMatch(/17:58/); // the capture instant is announced, not only drawn
    expect(label).toMatch(/17:28.*18:28/);
    const legend = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(legend).toHaveLength(4); // three phases + the capture mark
    expect(legend.join(" ")).not.toMatch(/bleue|blue/i);
  });

  it("clamps a window crossing midnight to the day instead of wrapping", () => {
    render(
      <DayStrip segments={[...SEGMENTS]} capture={1430} windowMinutes={30} takeMinutes={45} />,
    );
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toMatch(/23:20.*23:59/);
    expect(label).toMatch(/23:59/);
  });
});
