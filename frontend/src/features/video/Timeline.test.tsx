import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Keyframe, Shot, ShotStory, SynthesisChapter, SynthesisHighlight } from "@/api/client";

import { motionFamily, motionGlyph } from "./motion";
import { PlayerContext, PlayheadStore, type PlayerApi } from "./player";
import { CHAPTERS, HIGHLIGHTS } from "./synthesis/fixtures";
import { Timeline } from "./Timeline";

function story(part: number, parts: number, start: number, end: number, summary: string) {
  return {
    part,
    parts,
    start_s: start,
    end_s: end,
    summary,
    main_action: "",
    possible_cut: false,
    frames: [],
    model: "qwen/qwen3-vl-4b",
  } satisfies ShotStory;
}

function shot(
  idx: number,
  start: number,
  end: number,
  motion: string,
  stories: ShotStory[] = [],
): Shot {
  return {
    id: `s${idx}`,
    idx,
    start_s: start,
    end_s: end,
    boundary: idx === 0 ? "start" : "cut",
    motion,
    motion_score: 1,
    stability: 0.8,
    metrics: {
      luma: 0.5,
      contrast: 0.2,
      saturation: 0.3,
      sharpness: 100,
      black_ratio: 0,
      frozen_ratio: 0,
      cct_k: 5600,
    },
    stories,
  };
}

function keyframe(idx: number, t: number): Keyframe {
  return {
    id: `k${idx}`,
    idx,
    t_s: t,
    width: 1280,
    height: 720,
    selection_reason: "scene",
    sharpness: 10,
    shot_id: null,
    metrics: null,
    image_url: "",
    thumb_url: "",
    analysis: null,
  };
}

const SHOTS = [
  shot(0, 0, 4, "static"),
  shot(1, 4, 8, "pan_right", [
    story(1, 2, 4, 6, "Un héron se pose sur la berge."),
    story(2, 2, 6, 8, "Le héron s'envole au-dessus du lac."),
  ]),
  shot(2, 8, 10, "zoom_in"),
];
const FRAMES = [keyframe(0, 0), keyframe(1, 5)];

function renderTimeline(
  onSelectKeyframe = vi.fn(),
  synthesis: { chapters?: SynthesisChapter[]; highlights?: SynthesisHighlight[] } = {},
) {
  const playhead = new PlayheadStore(0);
  const seek = vi.fn((t: number) => {
    playhead.set(t);
  });
  const api: PlayerApi = { playhead, duration: 10, seek };
  render(
    <PlayerContext value={api}>
      <Timeline
        duration={10}
        shots={SHOTS}
        keyframes={FRAMES}
        audio={{ hz: 2, t: [0, 0.5, 1], lufs: [-20, -18, -70] }}
        silences={[[9, 10]]}
        hasAudio
        selectedKeyframeId={undefined}
        onSelectKeyframe={onSelectKeyframe}
        chapters={synthesis.chapters}
        highlights={synthesis.highlights}
      />
    </PlayerContext>,
  );
  return { seek, playhead };
}

function timelineSvg(): SVGSVGElement {
  const svg = document.querySelector("svg");
  if (!svg) {
    throw new Error("timeline not rendered");
  }
  return svg;
}

describe("motion families", () => {
  it("groups directions and keeps a glyph for each", () => {
    expect(motionFamily("pan_left")).toBe("camera");
    expect(motionFamily("zoom_out")).toBe("zoom");
    expect(motionFamily("handheld")).toBe("free");
    expect(motionFamily("unexpected")).toBe("free");
    expect(motionGlyph("tilt_up")).toBe("↑");
  });
});

describe("Timeline", () => {
  it("shows a legend for the families present only", () => {
    renderTimeline();
    expect(screen.getByText("Fixe")).toBeInTheDocument();
    expect(screen.getByText("Panoramique / bascule")).toBeInTheDocument();
    expect(screen.getByText("Zoom")).toBeInTheDocument();
    expect(screen.queryByText("Libre / à la main")).not.toBeInTheDocument();
    expect(screen.getByText("Silence")).toBeInTheDocument();
  });

  it("seeks where the shots track is clicked (720 px wide in tests)", () => {
    const { seek } = renderTimeline();
    fireEvent.click(timelineSvg(), { clientX: 360, clientY: 10 });
    expect(seek).toHaveBeenCalledWith(5);
  });

  it("selects the keyframe under the pointer on the keyframes track", () => {
    const onSelect = vi.fn();
    const { seek } = renderTimeline(onSelect);
    fireEvent.click(timelineSvg(), {
      clientX: 360 + 4,
      clientY: 40,
    });
    expect(onSelect).toHaveBeenCalledWith(FRAMES[1]);
    expect(seek).not.toHaveBeenCalled();
  });

  it("describes the shot and loudness under the pointer", () => {
    renderTimeline();
    fireEvent.pointerMove(timelineSvg(), {
      clientX: 360,
      clientY: 10,
    });
    expect(screen.getByText(/Plan 2 · → Panoramique vers la droite/)).toBeInTheDocument();
  });

  it("tells what happens in the part of the shot under the pointer", () => {
    renderTimeline();
    // 720 px for 10 s: x = 360 is t = 5 s (part 1), x = 504 is t = 7 s (part 2).
    fireEvent.pointerMove(timelineSvg(), { clientX: 360, clientY: 10 });
    expect(screen.getByText("Un héron se pose sur la berge.")).toBeInTheDocument();
    fireEvent.pointerMove(timelineSvg(), { clientX: 504, clientY: 10 });
    expect(screen.getByText("Le héron s'envole au-dessus du lac.")).toBeInTheDocument();
    expect(screen.queryByText("Un héron se pose sur la berge.")).not.toBeInTheDocument();
    // Over the sound track, the tooltip stays short; a shot without a story adds nothing.
    fireEvent.pointerMove(timelineSvg(), { clientX: 504, clientY: 70 });
    expect(screen.queryByText("Le héron s'envole au-dessus du lac.")).not.toBeInTheDocument();
    fireEvent.pointerMove(timelineSvg(), { clientX: 100, clientY: 10 });
    expect(screen.getByText(/Plan 1 · • Fixe/)).toBeInTheDocument();
    expect(screen.queryByText(/héron/)).not.toBeInTheDocument();
  });

  it("keeps the tooltip inside the track, whatever its width", () => {
    renderTimeline();
    // x = 560 is t = 7.8 s: the long story of part 2, 160 px from the right edge (720 px).
    fireEvent.pointerMove(timelineSvg(), { clientX: 560, clientY: 10 });
    const tooltipOf = (text: string | RegExp) =>
      screen.getByText(text).closest("[role=presentation]");
    const tooltip = tooltipOf("Le héron s'envole au-dessus du lac.");
    // Centred on the pointer, slid left by what its own width puts past the edge; never wider
    // than the track.
    expect(tooltip).toHaveStyle({
      left: "560px",
      translate: "max(-560px, min(-50%, 160px - 100%))",
    });
    expect(tooltip).toHaveClass("max-w-[min(20rem,100%)]");
    fireEvent.pointerMove(timelineSvg(), { clientX: 10, clientY: 10 });
    expect(tooltipOf(/Plan 1 · • Fixe/)).toHaveStyle({
      left: "10px",
      translate: "max(-10px, min(-50%, 710px - 100%))",
    });
  });

  it("is keyboard operable", async () => {
    const user = userEvent.setup();
    const { seek } = renderTimeline();
    const slider = screen.getByRole("slider", { name: /Frise de la vidéo/ });
    slider.focus();
    await user.keyboard("{ArrowRight}");
    expect(seek).toHaveBeenLastCalledWith(1);
    await user.keyboard("{PageDown}");
    expect(seek).toHaveBeenLastCalledWith(4);
    await user.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(seek).toHaveBeenLastCalledWith(10); // clamped to the duration
    await user.keyboard("{PageUp}");
    expect(seek).toHaveBeenLastCalledWith(8); // start of the current shot
  });
});

describe("Timeline synthesis tracks", () => {
  // With both tracks (720 px for 10 s, 72 px a second): chapters y 0–20, shots 26–54,
  // keyframes 60–76, highlights 82–96, sound 102–146.
  const SYNTHESIS = { chapters: CHAPTERS, highlights: HIGHLIGHTS };
  const HIGHLIGHTS_Y = 89;

  it("draws the chapters and the highlights only when the synthesis has some", () => {
    renderTimeline();
    expect(screen.queryByText("Chapitres")).not.toBeInTheDocument();
    expect(screen.queryByText("Moments forts")).not.toBeInTheDocument();
    expect(screen.queryByText("Moment fort (image)")).not.toBeInTheDocument();
  });

  it("labels each chapter and gives the highlights a legend", () => {
    renderTimeline(vi.fn(), SYNTHESIS);
    expect(screen.getByText("Chapitres")).toBeInTheDocument();
    expect(screen.getByText("Moments forts")).toBeInTheDocument();
    expect(screen.getByText("1 Arrivée du héron")).toBeInTheDocument();
    expect(screen.getByText("2 Envol")).toBeInTheDocument();
    expect(screen.getByText("Moment fort (image)")).toBeInTheDocument();
    expect(screen.getByText("Son prolongé (J/L-cut)")).toBeInTheDocument();
    // The legend of the sound only when a highlight has one.
    cleanup();
    renderTimeline(vi.fn(), { highlights: HIGHLIGHTS.slice(1) });
    expect(screen.getByText("Moment fort (image)")).toBeInTheDocument();
    expect(screen.queryByText("Son prolongé (J/L-cut)")).not.toBeInTheDocument();
  });

  it("keeps each chapter's label inside its segment", () => {
    const chapter = (index: number, start: number, end: number, title: string) => ({
      index,
      start_s: start,
      end_s: end,
      title,
      summary: "",
    });
    renderTimeline(vi.fn(), {
      chapters: [
        chapter(1, 0, 1.5, "Arrivée du héron"),
        chapter(2, 1.5, 9.6, "Envol"),
        chapter(3, 9.6, 10, "Dernier regard"),
      ],
    });
    // 108 px: the title is cut; 29 px: the number alone, never text running into the next.
    expect(screen.getByText("1 Arrivée du h…")).toBeInTheDocument();
    expect(screen.getByText("2 Envol")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.queryByText(/Dernier/)).not.toBeInTheDocument();
  });

  it("tells the chapter under the pointer, and plays it from its start", () => {
    const { seek } = renderTimeline(vi.fn(), SYNTHESIS);
    fireEvent.pointerMove(timelineSvg(), { clientX: 500, clientY: 10 });
    const tooltip = screen.getByText("Chapitre 2 · Envol").closest("[role=presentation]");
    expect(tooltip).toHaveTextContent("00:06.000 → 00:10.000");
    expect(tooltip).toHaveTextContent("Le héron s'envole au-dessus du lac.");
    expect(tooltip).not.toHaveTextContent("Plan 3"); // only what the track shows
    fireEvent.click(timelineSvg(), { clientX: 500, clientY: 10 });
    expect(seek).toHaveBeenCalledWith(6);
  });

  it("shows a highlight's picture, the sound of its J/L-cut and why it was picked", () => {
    renderTimeline(vi.fn(), SYNTHESIS);
    fireEvent.pointerMove(timelineSvg(), { clientX: 200, clientY: HIGHLIGHTS_Y });
    const tooltip = screen.getByText("Moment fort 1 · suggestion").closest("[role=presentation]");
    expect(tooltip).toHaveTextContent("Image 00:02.000 → 00:04.000");
    expect(tooltip).toHaveTextContent("Son 00:01.500 → 00:04.800");
    expect(tooltip).toHaveTextContent("Le héron se pose en déployant ses ailes.");
    // Highlight 2: the sound follows the picture.
    fireEvent.pointerMove(timelineSvg(), { clientX: 560, clientY: HIGHLIGHTS_Y });
    const second = screen.getByText("Moment fort 2 · suggestion").closest("[role=presentation]");
    expect(second).toHaveTextContent("Image 00:07.000 → 00:09.000");
    expect(second).not.toHaveTextContent("Son");
  });

  it("plays a highlight from its picture's in point, from anywhere on its marks", () => {
    const { seek } = renderTimeline(vi.fn(), SYNTHESIS);
    // x = 112 is 1.56 s: the sound before the picture (J-cut) of highlight 1.
    fireEvent.click(timelineSvg(), { clientX: 112, clientY: HIGHLIGHTS_Y });
    expect(seek).toHaveBeenLastCalledWith(2);
    // A few pixels past the mark of highlight 2 (7–9 s: 504–648 px) still hits it.
    fireEvent.click(timelineSvg(), { clientX: 652, clientY: HIGHLIGHTS_Y });
    expect(seek).toHaveBeenLastCalledWith(7);
    // Between two highlights: the time under the pointer, like the other tracks.
    fireEvent.click(timelineSvg(), { clientX: 432, clientY: HIGHLIGHTS_Y });
    expect(seek).toHaveBeenLastCalledWith(6);
  });

  it("keeps the other tracks working below the chapters", () => {
    const onSelect = vi.fn();
    const { seek } = renderTimeline(onSelect, SYNTHESIS);
    fireEvent.click(timelineSvg(), { clientX: 360, clientY: 40 }); // shots
    expect(seek).toHaveBeenLastCalledWith(5);
    fireEvent.click(timelineSvg(), { clientX: 364, clientY: 66 }); // keyframes
    expect(onSelect).toHaveBeenCalledWith(FRAMES[1]);
    fireEvent.pointerMove(timelineSvg(), { clientX: 360, clientY: 40 });
    expect(screen.getByText("Un héron se pose sur la berge.")).toBeInTheDocument();
    expect(screen.queryByText(/Chapitre \d+ ·/)).not.toBeInTheDocument(); // not the chapter's
  });
});
