import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PlayerContext, PlayheadStore, type PlayerApi } from "./player";
import { TranscriptTab } from "./TranscriptTab";
import { activeSegmentAt } from "./transcriptSearch";

const transcript = vi.fn<() => unknown>();
const patch = vi.fn();
vi.mock("@/api/queries", () => ({
  useTranscript: () => transcript(),
  usePatchVideo: () => ({ mutate: patch, isPending: false }),
}));

const base = {
  run_status: "ready",
  note: null,
  source: "asr",
  model: "whisper/large-v3-turbo",
  language: "fr",
  language_probability: 0.99,
  duration_s: 10,
  speech_s: 6,
  transcript_mode: null,
  transcript_language: null,
  can_force: false,
  srt_url: "/api/v1/videos/v1/transcript.srt?v=1",
  vtt_url: "/api/v1/videos/v1/transcript.vtt?v=1",
};

const segments = [
  {
    idx: 0,
    start_s: 0.2,
    end_s: 2,
    text: "J'ai fait cuire le riz.",
    language: "fr",
    suspect: false,
    words: [
      [0.2, 0.5, " J'ai", 0.9],
      [0.5, 0.8, " fait", 0.9],
      [0.8, 1.2, " cuire", 0.4],
      [1.2, 1.5, " le", 0.9],
      [1.5, 2.0, " riz.", 0.9],
    ],
  },
  {
    idx: 1,
    start_s: 3,
    end_s: 4,
    text: "Sous-titres réalisés par Amara.org",
    language: "fr",
    suspect: true,
    words: [],
  },
];

function renderTab() {
  const seek = vi.fn();
  const api: PlayerApi = { playhead: new PlayheadStore(0), duration: 10, seek };
  render(
    <PlayerContext value={api}>
      <TranscriptTab videoId="v1" />
    </PlayerContext>,
  );
  return seek;
}

function loaded(data: object) {
  transcript.mockReturnValue({ isPending: false, isError: false, data });
}

beforeEach(() => {
  transcript.mockReset();
  patch.mockReset();
});

describe("TranscriptTab", () => {
  it("keeps the spaces between words and seeks on a word", async () => {
    loaded({ ...base, status: "ready", segments });
    const user = userEvent.setup();
    const seek = renderTab();

    // A button drops its own leading space when rendered: the space must sit outside it.
    const word = screen.getByRole("button", { name: "cuire" });
    expect(word.textContent).toBe("cuire");
    expect(word.closest("p")?.textContent).toBe("J'ai fait cuire le riz.");
    expect(word).toHaveAttribute("tabindex", "-1"); // the timestamp is the keyboard control
    expect(
      screen.getByTitle("Segment peu fiable (hallucination probable du modèle)"),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "riz." }));
    expect(seek).toHaveBeenCalledWith(1.5);
    expect(screen.getByRole("link", { name: /SRT/ })).toHaveAttribute("href", base.srt_url);
  });

  it("keeps a stored transcript visible while it is redone", () => {
    loaded({ ...base, status: "ready", run_status: "running", segments });
    renderTab();
    expect(screen.getByText("Analyse en cours…")).toBeVisible();
    expect(screen.getByRole("button", { name: "cuire" })).toBeVisible();
  });

  it("offers to transcribe anyway when no speech was heard", async () => {
    loaded({
      ...base,
      status: "no_speech",
      can_force: true,
      segments: [],
      srt_url: null,
      vtt_url: null,
    });
    const user = userEvent.setup();
    renderTab();

    expect(screen.getByText("Aucune parole détectée dans cette vidéo.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Transcrire quand même" }));
    // The server redoes the transcription when the choice changes.
    expect(patch.mock.calls[0]?.[0]).toEqual({ transcript_mode: "always" });
  });

  it("explains a failure without offering a pointless retry", () => {
    loaded({
      ...base,
      status: "failed",
      run_status: "failed",
      note: "moteur arrêté",
      segments: [],
      srt_url: null,
      vtt_url: null,
    });
    renderTab();
    expect(screen.getByText("L'analyse a échoué : moteur arrêté")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Transcrire quand même" })).toBeNull();
  });
});

describe("activeSegmentAt", () => {
  const spans = [
    { start_s: 0, end_s: 10 }, // a long first-pass segment
    { start_s: 3, end_s: 4 }, // a short second-pass one inside it
    { start_s: 12, end_s: 14 },
  ];
  const starts = spans.map((s) => s.start_s);

  it("follows the segment being spoken", () => {
    expect(activeSegmentAt(spans, starts, -1)).toBe(-1);
    expect(activeSegmentAt(spans, starts, 1)).toBe(0);
    expect(activeSegmentAt(spans, starts, 3.5)).toBe(1);
    expect(activeSegmentAt(spans, starts, 6)).toBe(0); // back to the enclosing one
    expect(activeSegmentAt(spans, starts, 11)).toBe(1); // a pause: the last one started stays
    expect(activeSegmentAt(spans, starts, 13)).toBe(2);
  });
});
