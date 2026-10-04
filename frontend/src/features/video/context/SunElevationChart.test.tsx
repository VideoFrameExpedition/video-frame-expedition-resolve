import { render, screen } from "@testing-library/react";

import type { Video } from "@/api/client";

import { ChipRow } from "./ContextChips";
import { SunElevationChart, type SunCurve } from "./SunElevationChart";
import { useContextChips } from "./useContextChips";

// A simple day: the sun rises at 06:00, peaks at 60° at 13:00 and sets at 20:00.
const curve: SunCurve = {
  start_utc: "2026-08-25T22:00:00Z",
  step_min: 60,
  elevations_deg: Array.from({ length: 25 }, (_, h) =>
    h < 6 || h > 20 ? -20 : Math.round(60 * Math.sin(((h - 6) / 14) * Math.PI) * 10) / 10,
  ),
};

describe("SunElevationChart", () => {
  it("states the highest sun and the capture instant", () => {
    render(<SunElevationChart curve={curve} capture={17 * 60 + 58} captureElevation={24.9} />);
    const chart = screen.getByRole("img");
    expect(chart).toHaveAccessibleName(/maximum 60,0° à 13:00/);
    expect(chart).toHaveAccessibleName(/tournage à 17:58, soleil à 24,9°/);
    expect(screen.getByText("Heure dorée (−4° à 6°)")).toBeInTheDocument();
  });
});

function Chips({ video }: { video: Video }) {
  return <ChipRow chips={useContextChips(video)} />;
}

describe("context chips", () => {
  it("show where, in what light and weather a video was shot", () => {
    const video = {
      place: "Giverny",
      place_approximate: false,
      light_phase: "golden_hour",
      weather_category: "overcast",
      temperature_c: 27.3,
    } as Video;
    render(<Chips video={video} />);
    expect(screen.getByText("Giverny")).toBeInTheDocument();
    expect(screen.getByText("Heure dorée")).toBeInTheDocument();
    expect(screen.getByText("27 °C · Couvert")).toBeInTheDocument();
  });

  it("say when a place is only approximate, and show nothing without context", () => {
    const near = { place: "Vernon", place_approximate: true, light_phase: null,
      weather_category: null, temperature_c: null } as unknown as Video; // prettier-ignore
    const { rerender, container } = render(<Chips video={near} />);
    expect(screen.getByText("près de Vernon")).toBeInTheDocument();
    rerender(<Chips video={{ ...near, place: null }} />);
    expect(container.textContent).toBe("");
  });
});
