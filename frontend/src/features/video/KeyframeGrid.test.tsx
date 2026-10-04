import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Keyframe } from "@/api/client";

import { KeyframeGrid } from "./KeyframeGrid";

function frame(idx: number, caption?: string): Keyframe {
  return {
    id: `kf${idx}`,
    idx,
    t_s: idx * 2,
    width: 1280,
    height: 720,
    selection_reason: idx === 0 ? "first" : "scene",
    sharpness: 10,
    shot_id: null,
    metrics: null,
    image_url: `/img/${idx}.jpg`,
    thumb_url: `/thumb/${idx}.jpg`,
    analysis: caption
      ? {
          model: "qwen/qwen3-vl-8b",
          prompt_version: "frame_analysis.v2",
          language: "fr",
          focus: null,
          created_at: "2026-09-26T12:00:00Z",
          data: {
            caption,
            description: "desc",
            shot_type: "macro",
            camera_angle: "eye_level",
            setting: "outdoor",
            place_type: "jardin",
            time_of_day: "day",
            weather: "sky_not_visible",
            lighting: "natural_soft",
            subjects: [],
            people_count: 0,
            actions: [],
            mood: "calme",
            dominant_colors: [],
            visible_text: "",
            quality_issues: [],
            editing_value: [],
            tags: [],
          },
        }
      : null,
  };
}

describe("KeyframeGrid", () => {
  it("shows captions, timecodes and a placeholder for undescribed frames", () => {
    render(
      <KeyframeGrid
        frames={[frame(0, "Un papillon sur la lavande"), frame(1)]}
        selectedId={undefined}
        currentId="kf0"
        onSelect={() => undefined}
      />,
    );
    expect(screen.getByText("Un papillon sur la lavande")).toBeInTheDocument();
    expect(screen.getByText("00:02.000")).toBeInTheDocument();
    expect(screen.getByText("Pas encore décrite")).toBeInTheDocument();
  });

  it("selects a frame on click", async () => {
    const onSelect = vi.fn();
    render(
      <KeyframeGrid
        frames={[frame(0, "A"), frame(1, "B")]}
        selectedId="kf0"
        currentId={undefined}
        onSelect={onSelect}
      />,
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    const second = buttons[1];
    if (!second) throw new Error("second frame missing");
    await userEvent.click(second);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "kf1" }));
  });
});
