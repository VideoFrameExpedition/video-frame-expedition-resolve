import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AudioTab } from "./AudioTab";
import { PlayerContext, PlayheadStore, type PlayerApi } from "./player";

const audio = vi.fn<() => unknown>();
vi.mock("@/api/queries", () => ({
  useAudio: () => audio(),
}));

const base = {
  status: "ready",
  run_status: "ready",
  note: null,
  model: "yamnet@x",
  speech_s: 2,
  music_s: 0,
  dominant: "nature",
  presence: [{ category: "nature", share: 0.8 }],
  instruments: [{ label: "Flute", name: "Flûte", seconds: 3, max_score: 0.8 }],
  top_labels: [],
  environment: null,
  issues: [],
  curves: null,
};

const heard = [
  {
    label: "Frog",
    name: "Grenouille",
    category: "nature",
    seconds: 75,
    score: 0.93,
    spans: [
      [0, 9],
      [11, 16],
    ],
    moments: 2,
    sources: ["ced"],
  },
];

function renderTab() {
  const seek = vi.fn();
  const api: PlayerApi = { playhead: new PlayheadStore(0), duration: 20, seek };
  render(
    <PlayerContext value={api}>
      <AudioTab videoId="v1" />
    </PlayerContext>,
  );
  return seek;
}

function loaded(data: object) {
  audio.mockReturnValue({ isPending: false, isError: false, data });
}

beforeEach(() => {
  audio.mockReset();
});

describe("AudioTab", () => {
  it("lists the sounds heard in French with the moments to listen to", async () => {
    loaded({
      ...base,
      taggers: ["yamnet", "ced-small"],
      heard,
      segments: [
        {
          kind: "event",
          category: "nature",
          label: "Frog",
          name: "Grenouille",
          start_s: 1,
          end_s: 2,
          score: 0.6,
        },
      ],
      shots: [
        {
          shot_idx: 0,
          start_s: 0,
          end_s: 8,
          labels: [],
          heard: [
            { label: "Frog", name: "Grenouille" },
            { label: "Owl", name: "Chouette, hibou" },
          ],
        },
        {
          shot_idx: 1,
          start_s: 8,
          end_s: 16,
          labels: [{ label: "Wind", name: "Vent", score: 0.3 }],
          heard: [],
        },
      ],
    });
    const user = userEvent.setup();
    const seek = renderTab();

    const list = screen.getByRole("list", { name: "Sons entendus" });
    expect(within(list).getByText("Grenouille")).toBeVisible();
    expect(within(list).getByText("1 min 15 s")).toBeVisible(); // a length, not a moment
    await user.click(screen.getByRole("button", { name: "Écouter « Grenouille » à 00:11" }));
    expect(seek).toHaveBeenCalledWith(11);
    expect(screen.getByText("Flûte")).toBeVisible();
    expect(screen.queryByText("Sons remarquables")).toBeNull(); // the heard list replaces them
    expect(screen.getByText("Vent")).toBeVisible(); // per shot
    expect(screen.getByText("Grenouille · Chouette, hibou")).toBeVisible();
    expect(screen.getByText(/YAMNet \+ CED-small/)).toBeVisible();
    expect(screen.queryByText(/vfe models sounds/)).toBeNull();
  });

  it("says when nothing specific is heard and how to get the second opinion", () => {
    const event = {
      kind: "event",
      category: "vehicles",
      label: "Train horn",
      name: "Klaxon de train",
      start_s: 4,
      end_s: 5,
      score: 0.7,
    };
    loaded({
      ...base,
      taggers: ["yamnet"],
      ced_available: false,
      heard: [],
      segments: [event],
      shots: [],
    });
    renderTab();
    expect(
      screen.getByText("Aucun son précis reconnu en dehors de la parole et de la musique."),
    ).toBeVisible();
    expect(screen.queryByText("Sons remarquables")).toBeNull(); // the new rules rejected it
    expect(screen.getByText(/vfe models sounds/)).toBeVisible();
  });

  it("asks for an update when CED is installed but not used yet", () => {
    loaded({
      ...base,
      taggers: ["yamnet"],
      ced_available: true,
      heard,
      segments: [],
      shots: [],
    });
    renderTab();
    expect(screen.getByText(/« Mettre à jour » l'ajoute/)).toBeVisible();
    expect(screen.queryByText(/vfe models sounds/)).toBeNull();
  });

  it("tells older analyses apart from « nothing heard »", () => {
    const event = {
      kind: "event",
      category: "nature",
      label: "Dog",
      name: "Chien",
      start_s: 4,
      end_s: 5,
      score: 0.8,
    };
    loaded({
      ...base,
      taggers: ["yamnet"],
      ced_available: true,
      heard: null,
      segments: [event],
      shots: [],
    });
    renderTab();
    expect(screen.getByText(/pas encore calculés/)).toBeVisible();
    expect(screen.queryByText(/Aucun son précis/)).toBeNull();
    expect(screen.getByText("Sons remarquables")).toBeVisible();
    expect(screen.getByText("Chien")).toBeVisible();
  });
});
