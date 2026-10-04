import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Shot, ShotStory, ShotUsability, StoryFrame, SynthesisSuggestion } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";

import { PlayerContext, PlayheadStore, type PlayerApi } from "./player";
import { ShotsTab } from "./ShotsTab";

const MODEL = "qwen/qwen3-vl-4b";
const CUT_HINT =
  "Les images semblent montrer des sujets différents : une coupe a peut-être été manquée";

function frame(t: number, note: string | null = null): StoryFrame {
  return { t_s: t, keyframe_id: null, thumb_url: `/thumb/${t}.jpg`, note };
}

function story(overrides: Partial<ShotStory> & Pick<ShotStory, "start_s" | "end_s">): ShotStory {
  return {
    part: 1,
    parts: 1,
    summary: "",
    main_action: "",
    possible_cut: false,
    frames: [],
    model: MODEL,
    ...overrides,
  };
}

function shot(idx: number, start: number, end: number, stories: ShotStory[] = []): Shot {
  return {
    id: `s${idx}`,
    idx,
    start_s: start,
    end_s: end,
    boundary: idx === 0 ? "start" : "cut",
    motion: "pan_right",
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

const STORIED = [
  shot(0, 0, 8, [
    story({
      start_s: 0,
      end_s: 8,
      summary: "Un héron se pose sur la berge.",
      main_action: "se poser",
      frames: [frame(1.5), frame(6.2, "il replie ses ailes")],
    }),
  ]),
  shot(1, 8, 48, [
    story({
      part: 1,
      parts: 2,
      start_s: 8,
      end_s: 28,
      summary: "Le héron marche puis décolle.",
      possible_cut: true,
      frames: [frame(12.3, "il s'envole")],
    }),
    story({ part: 2, parts: 2, start_s: 28, end_s: 48, summary: "La rive reste vide." }),
  ]),
  shot(2, 48, 50),
];

function renderTab(
  shots: Shot[],
  synthesis: { usability?: ShotUsability[]; suggestions?: SynthesisSuggestion[] } = {},
) {
  const playhead = new PlayheadStore(0);
  const seek = vi.fn((t: number) => {
    playhead.set(t);
  });
  const api: PlayerApi = { playhead, duration: 50, seek };
  render(
    <TooltipProvider>
      <PlayerContext value={api}>
        <ShotsTab
          shots={shots}
          keyframes={[]}
          usability={synthesis.usability}
          suggestions={synthesis.suggestions}
        />
      </PlayerContext>
    </TooltipProvider>,
  );
  return { seek };
}

function suggestion(role: SynthesisSuggestion["role"], shots: number[]): SynthesisSuggestion {
  return {
    role,
    shots,
    clip: { picture_in_s: 0, picture_out_s: 4, sound_in_s: null, sound_out_s: null, notes: [] },
    usability: 90,
    reasons: [],
  };
}

describe("ShotsTab usability", () => {
  const USABILITY: ShotUsability[] = [
    { shot_idx: 0, score: 92, reasons: [] },
    { shot_idx: 1, score: 45, reasons: ["très court", "instable"] },
    { shot_idx: 2, score: 64, reasons: ["flou"] },
  ];
  const SUGGESTIONS = [
    suggestion("b_roll", [0]),
    suggestion("establishing", [0]),
    suggestion("b_roll", [0]), // a second block of the same shot
    suggestion("avoid", [1]),
  ];

  it("scores each shot with a number, a band in words and the reasons on demand", () => {
    renderTab(STORIED, { usability: USABILITY, suggestions: SUGGESTIONS });
    const good = screen.getByRole("button", {
      name: "Utilisabilité indicative 92 sur 100, bonne",
    });
    expect(good).toHaveTextContent("92");
    expect(good).toHaveAccessibleDescription("Indicatif : aucun défaut relevé");
    expect(
      screen.getByRole("button", { name: "Utilisabilité indicative 45 sur 100, faible" }),
    ).toHaveAccessibleDescription("Indicatif : très court, instable");
    expect(
      screen.getByRole("button", { name: "Utilisabilité indicative 64 sur 100, moyenne" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/suggestions indicatives de l'application/)).toBeVisible();
  });

  it("shows the reasons of a score on keyboard focus", async () => {
    renderTab(STORIED, { usability: USABILITY });
    act(() => {
      screen.getByRole("button", { name: /45 sur 100/ }).focus();
    });
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Indicatif : très court, instable",
    );
  });

  it("labels the editing roles suggested for a shot, each once", () => {
    renderTab(STORIED, { usability: USABILITY, suggestions: SUGGESTIONS });
    const first = screen.getAllByRole("row")[1];
    if (!first) throw new Error("shot rows expected");
    expect(within(first).getByText(/plan d'ensemble$/)).toHaveTextContent(
      "Suggestion : plan d'ensemble",
    );
    expect(within(first).getAllByText(/illustration$/)).toHaveLength(1);
    expect(screen.getAllByText(/à éviter$/)).toHaveLength(1);
    expect(within(first).queryByText(/à éviter$/)).not.toBeInTheDocument();
  });

  it("adds nothing before the synthesis has scored the shots", () => {
    renderTab(STORIED);
    expect(screen.queryByRole("button", { name: /Utilisabilité/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/suggestions indicatives/)).not.toBeInTheDocument();
  });
});

describe("ShotsTab stories", () => {
  it("tells what happens, with the main action when there is one", () => {
    renderTab(STORIED);
    expect(screen.getAllByText("Ce qui se passe")).toHaveLength(2);
    expect(screen.getByText("Un héron se pose sur la berge.")).toBeInTheDocument();
    expect(screen.getByText("se poser")).toBeInTheDocument();
    // The camera column still comes from the measured flow, never from the model.
    expect(screen.getAllByText("Panoramique vers la droite")).toHaveLength(3);
  });

  it("gives a long shot one sub-row per part, with its time range", () => {
    renderTab(STORIED);
    expect(screen.getByText("Partie 1/2 · 00:08 – 00:28")).toBeInTheDocument();
    expect(screen.getByText("Partie 2/2 · 00:28 – 00:48")).toBeInTheDocument();
    expect(screen.getByText("La rive reste vide.")).toBeInTheDocument();
    // A single-part shot has no part label.
    expect(screen.queryByText(/Partie 1\/1/)).not.toBeInTheDocument();
  });

  it("captions each frame with its real time and what changes there", () => {
    renderTab(STORIED);
    expect(screen.getByText("vu à 12,3 s : il s'envole")).toBeInTheDocument();
    expect(screen.getByText("vu à 6,2 s : il replie ses ailes")).toBeInTheDocument();
    expect(screen.getByText("vu à 1,5 s")).toBeInTheDocument();
    expect(screen.getByAltText("Image à 12,3 s")).toHaveAttribute("src", "/thumb/12.3.jpg");
  });

  it("flags a possible missed cut with words, and says why to screen readers", () => {
    renderTab(STORIED);
    const badge = screen.getByRole("button", { name: "coupe possible" });
    expect(badge).toHaveAccessibleDescription(CUT_HINT);
    expect(screen.getAllByText("coupe possible")).toHaveLength(1);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("shows why a cut is possible on a tap, with no hover", async () => {
    const user = userEvent.setup();
    renderTab(STORIED);
    const badge = screen.getByRole("button", { name: "coupe possible" });
    await user.pointer({ keys: "[TouchA]", target: badge });
    expect(await screen.findByRole("tooltip")).toHaveTextContent(CUT_HINT);
  });

  it("keeps the hint open when its badge is pressed again", async () => {
    const user = userEvent.setup();
    renderTab(STORIED);
    const badge = screen.getByRole("button", { name: "coupe possible" });
    await user.hover(badge);
    expect(await screen.findByRole("tooltip")).toBeInTheDocument();
    await user.pointer({ keys: "[MouseLeft>]", target: badge });
    expect(screen.getByRole("tooltip")).toHaveTextContent(CUT_HINT);
    await user.pointer({ keys: "[/MouseLeft]", target: badge });
    expect(screen.getByRole("tooltip")).toHaveTextContent(CUT_HINT);
  });

  it("shows why a cut is possible when the badge gets keyboard focus", async () => {
    renderTab(STORIED);
    act(() => {
      screen.getByRole("button", { name: "coupe possible" }).focus();
    });
    expect(await screen.findByRole("tooltip")).toHaveTextContent(CUT_HINT);
  });

  it("names the model once, in a footnote", () => {
    renderTab(STORIED);
    expect(
      screen.getByText(`Récits générés par ${MODEL} — peuvent contenir des erreurs`),
    ).toBeInTheDocument();
  });

  it("seeks the player to a frame's time when it is clicked", async () => {
    const user = userEvent.setup();
    const { seek } = renderTab(STORIED);
    // Accessible names are not whitespace-normalised: "12,3 s" holds a no-break space.
    await user.click(screen.getByRole("button", { name: /^Aller à 12,3\ss$/ }));
    expect(seek).toHaveBeenCalledWith(12.3);
  });

  it("seeks no earlier than the part, so the clicked shot stays the current one", async () => {
    const user = userEvent.setup();
    // A shot's first keyframe often sits one frame before its cut (8 s here).
    const { seek } = renderTab([
      shot(0, 0, 8),
      shot(1, 8, 16, [story({ start_s: 8, end_s: 16, frames: [frame(7.967), frame(12)] })]),
    ]);
    await user.click(screen.getByRole("button", { name: /^Aller à 8,0\ss$/ }));
    expect(seek).toHaveBeenCalledWith(8);
    expect(screen.getByRole("row", { current: true })).toHaveTextContent("Plan 2");
  });

  it("adds nothing when no shot has a story", () => {
    renderTab([shot(0, 0, 8), shot(1, 8, 12)]);
    expect(screen.queryByText("Ce qui se passe")).not.toBeInTheDocument();
    expect(screen.queryByText(/Récits générés/)).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    // Header row + one row per shot, no story rows.
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });
});
