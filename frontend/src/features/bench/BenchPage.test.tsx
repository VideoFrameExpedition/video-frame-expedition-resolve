import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { BenchModelRun, BenchOverview, BenchRun, BenchScores } from "@/api/client";

import { BenchPage } from "./BenchPage";
import {
  best,
  blindOrder,
  historyLanguages,
  MEASURES,
  mixedImages,
  placeLabels,
  ratingOf,
  ratingProgress,
  rowsOfHistory,
  rowsOfRun,
} from "./benchFormat";
import { ordinal, radarPoints, rank } from "./benchRanking";

const start = vi.fn();
const rate = vi.fn();
const remove = vi.fn();
const cancel = vi.fn();
const annotate = vi.fn();
let overview: BenchOverview | undefined;
let runs: BenchRun[];
let run: BenchRun | undefined;
let jobs: unknown[];
vi.mock("@/api/queries", () => ({
  useBenchOverview: () => ({ data: overview, isError: false }),
  useBenchRuns: () => ({ data: runs }),
  // The run asked for: the one under test, or the one of the history with that id.
  useBenchRun: (id?: string) => ({
    data: id ? (run?.id === id ? run : runs.find((listed) => listed.id === id)) : undefined,
  }),
  useStartBench: () => ({ mutate: start, isPending: false }),
  useDeleteBenchRun: () => ({ mutate: remove, isPending: false }),
  useRateBench: () => ({ mutate: rate }),
  useAnnotateBench: () => ({ mutate: annotate, isPending: false }),
  useActiveJobs: () => ({ data: jobs }),
  useCancelJob: () => ({ mutate: cancel, isPending: false }),
}));

const SMALL = "qwen/qwen3-vl-4b";
const LARGE = "qwen/qwen3.5-9b";
const HUGE = "qwen/qwen3.6-27b";

function model(key: string, name: string, size: number, fit: "ok" | "tight" | "too_big") {
  return {
    key,
    display_name: name,
    publisher: "qwen",
    params: null,
    quantization: "Q4_K_M",
    architecture: "qwen3vl",
    size_bytes: size,
    loaded: key === SMALL,
    reasoning: false,
    fit,
  };
}

function anOverview(patch: Partial<BenchOverview> = {}): BenchOverview {
  return {
    lmstudio_error: null,
    models: [
      model(SMALL, "Qwen3 VL 4B", 3_333_641_502, "ok"),
      model(LARGE, "Qwen3.5 9B", 6_548_927_711, "tight"),
      model(HUGE, "Qwen3.6 27B", 17_478_732_949, "too_big"),
    ],
    gpu: { used_mib: 1800, free_mib: 10488, total_mib: 12288 },
    frames_available: 3276,
    language: "fr",
    image_choices: [12, 24, 48],
    default_images: 24,
    min_images: 4,
    max_models: 12,
    context_length: 12288,
    parallel: 4,
    loaded: ["Qwen3 VL 4B"],
    running_jobs: 0,
    active_run_id: null,
    ...patch,
  };
}

const scores = (patch: Partial<BenchScores>): BenchScores => ({
  vram_mib: null,
  vram_free_mib: null,
  vram_total_mib: 12288,
  vram_full: false,
  seconds_per_image: null,
  seconds_per_position: null,
  tokens_per_s: null,
  requests: 0,
  valid: 0,
  repaired: 0,
  truncated: 0,
  reasoning_tokens: 0,
  language_checked: 0,
  wrong_language: 0,
  foreign_script: 0,
  text_frames: 0,
  text_recall: null,
  blank_frames: 0,
  unconfirmed_text: 0,
  position_frames: 0,
  position_beings: 0,
  position_recall: null,
  position_iou: null,
  positions_enabled: null,
  calibration_iou: null,
  rated: 0,
  rating: null,
  ...patch,
});

function tested(
  key: string,
  name: string,
  patch: Partial<BenchScores>,
  size = 3_333_641_502,
): BenchModelRun {
  return {
    key,
    display_name: name,
    publisher: "qwen",
    params: "4B",
    quantization: "Q4_K_M",
    size_bytes: size,
    status: "done",
    error: null,
    load_s: 3.1,
    context_length: 12288,
    parallel: 4,
    calibration: null,
    scores: scores(patch),
  };
}

const answer = (caption: string) => ({
  ok: true,
  error: null,
  caption,
  description: `${caption} Une description plus longue.`,
  subjects: ["chat — roulé en boule"],
  visible_text: "",
  beings: [],
});

function aRun(patch: Partial<BenchRun> = {}): BenchRun {
  return {
    id: "run1",
    status: "succeeded",
    created_at: "2026-10-01T15:23:00Z",
    started_at: "2026-10-01T15:23:01Z",
    finished_at: "2026-10-01T15:26:04Z",
    job_id: "job1",
    images: 2,
    image_set: "a1b2c3d4e5f6",
    language: "fr",
    models: ["Qwen3 VL 4B", "Qwen3.5 9B", "Gemma 4 12B"],
    note: null,
    error: null,
    context_length: 12288,
    parallel: 4,
    progress: 1,
    message: null,
    ratings: {},
    previous: [
      {
        key: SMALL,
        instance_id: SMALL,
        display_name: "Qwen3 VL 4B",
        context_length: 16384,
        parallel: 4,
        restored: true,
        error: null,
      },
    ],
    model_runs: [
      tested(SMALL, "Qwen3 VL 4B", {
        vram_mib: 5454,
        vram_free_mib: 5653,
        seconds_per_image: 2.68,
        seconds_per_position: 2.06,
        requests: 48,
        valid: 48,
        language_checked: 24,
        wrong_language: 6,
        text_frames: 3,
        text_recall: 0.25,
        blank_frames: 9,
        unconfirmed_text: 1,
        position_beings: 6,
        position_recall: 1,
        position_iou: 0.94,
        positions_enabled: true,
        calibration_iou: 0.9,
      }),
      tested(
        LARGE,
        "Qwen3.5 9B",
        {
          vram_mib: 11800,
          vram_free_mib: 200,
          vram_full: true,
          seconds_per_image: 5.37,
          requests: 48,
          valid: 47,
          truncated: 1,
          language_checked: 24,
          wrong_language: 0,
          text_frames: 3,
          text_recall: 0.83,
          positions_enabled: false,
        },
        6_548_927_711,
      ),
      {
        ...tested("google/gemma-4-12b", "Gemma 4 12B", {}),
        status: "load_failed",
        error: "Not enough memory to load the model.",
      },
    ],
    frames: [
      {
        keyframe_id: "k1",
        video_id: "v1",
        filename: "chat.mp4",
        t_s: 12,
        image_path: "v1/k1.jpg",
        thumb_path: "v1/t1.jpg",
        image_url: "/api/v1/media/v1/k1.jpg",
        thumb_url: "/api/v1/media/v1/t1.jpg",
        text: [],
        beings: [],
        answers: { [SMALL]: answer("Un chat dort."), [LARGE]: answer("Un chien court.") },
      },
      {
        keyframe_id: "k2",
        video_id: "v1",
        filename: "chat.mp4",
        t_s: 20,
        image_path: "v1/k2.jpg",
        thumb_path: "v1/t2.jpg",
        image_url: "/api/v1/media/v1/k2.jpg",
        thumb_url: "/api/v1/media/v1/t2.jpg",
        text: null,
        beings: null,
        answers: { [SMALL]: answer("Un lac."), [LARGE]: { ...answer(""), ok: false } },
      },
    ],
    ...patch,
  };
}

/** An earlier run, on other frames: the small model again (slower then), and Gemma, tested. */
function anEarlierRun(): BenchRun {
  return aRun({
    id: "run0",
    created_at: "2026-09-12T09:00:00Z",
    images: 32,
    image_set: "ffffffffffff",
    note: "Comparaison manuelle",
    models: ["Qwen3 VL 4B", "Gemma 4 12B"],
    ratings: {},
    frames: [],
    model_runs: [
      tested(SMALL, "Qwen3 VL 4B", { vram_mib: 5437, seconds_per_image: 3.4, rated: 0 }),
      tested(
        "google/gemma-4-12b",
        "Gemma 4 12B",
        { vram_mib: 8789, vram_free_mib: 1495, seconds_per_image: 6.45, rated: 32, rating: 2.47 },
        7_677_000_000,
      ),
    ],
  });
}

beforeEach(() => {
  for (const mock of [start, rate, remove, cancel, annotate]) {
    mock.mockReset();
  }
  window.localStorage.clear();
  overview = anOverview();
  runs = [];
  run = undefined;
  jobs = [];
});

describe("BenchPage", () => {
  it("lists the vision models and starts a test on the ticked ones", async () => {
    const user = userEvent.setup();
    render(<BenchPage />);
    expect(screen.getByRole("button", { name: "Lancer le test" })).toBeDisabled();
    expect(screen.getByText(/Risque de ne pas tenir entièrement sur la carte/)).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /Qwen3\.6 27B/ })).toBeDisabled();
    expect(screen.getByText(/1 modèle plus gros que la mémoire de la carte/)).toBeVisible();
    expect(screen.getByText(/« Qwen3 VL 4B » est chargé : il sera déchargé/)).toBeVisible();
    expect(screen.getByText(/Aucun test pour l'instant/)).toBeVisible();

    await user.click(screen.getByRole("checkbox", { name: /Qwen3 VL 4B/ }));
    await user.click(screen.getByRole("checkbox", { name: /Qwen3\.5 9B/ }));
    await user.click(screen.getByRole("button", { name: "Lancer le test (2 modèles)" }));
    expect(start).toHaveBeenCalledOnce();
    expect(start.mock.calls[0]?.[0]).toEqual({ models: [SMALL, LARGE], images: 24 });
  });

  it("remembers the ticked models, and never ticks one LM Studio no longer holds", () => {
    window.localStorage.setItem("vfe.bench.models", JSON.stringify([LARGE, "gone/model"]));
    render(<BenchPage />);
    expect(screen.getByRole("checkbox", { name: /Qwen3\.5 9B/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "Lancer le test (1 modèle)" })).toBeEnabled();
  });

  it("says why no test can start", () => {
    overview = anOverview({ lmstudio_error: "LM Studio ne répond pas", models: [] });
    const { unmount } = render(<BenchPage />);
    expect(screen.getByText(/LM Studio est injoignable/)).toBeVisible();
    unmount();

    window.localStorage.setItem("vfe.bench.models", JSON.stringify([SMALL]));
    overview = anOverview({ frames_available: 0 });
    render(<BenchPage />);
    expect(screen.getByText(/analysez d'abord une vidéo/)).toBeVisible();
    expect(screen.getByRole("button", { name: /Lancer le test/ })).toBeDisabled();
  });

  it("shows what each model measured", () => {
    runs = [aRun()];
    run = aRun();
    render(<BenchPage />);
    const table = screen.getByRole("table", { name: "Résultats" });
    const small = within(within(table).getByRole("row", { name: /Qwen3 VL 4B/ }));
    expect(small.getByText("5,3 Go")).toHaveClass("text-brand-teal"); // the lightest
    expect(small.getByText("reste 5,5 Go")).toBeVisible();
    expect(small.getByText("2,7 s par image")).toHaveClass("text-brand-teal");
    expect(small.getByText("48 sur 48")).toBeVisible();
    expect(small.getByText("18 sur 24")).toBeVisible(); // six descriptions in another language
    expect(small.getByText("25 % des mots")).toBeVisible();
    expect(small.getByText(/1 texte non confirmé sur 9 images sans texte/)).toBeVisible();
    expect(small.getByText("100 % des sujets")).toBeVisible();
    expect(small.getByText(/6 sujets · recouvrement 0,94 · calibrage 0,90/)).toBeVisible();
    expect(small.getByText("à noter ci-dessous")).toBeVisible();

    const large = within(within(table).getByRole("row", { name: /Qwen3\.5 9B/ }));
    expect(large.getByText("carte pleine")).toBeVisible();
    expect(large.getByText("47 sur 48")).toBeVisible();
    expect(large.getByText("1 coupée")).toBeVisible();
    expect(large.getByText("24 sur 24")).toHaveClass("text-brand-teal");
    expect(large.getByText("83 % des mots")).toHaveClass("text-brand-teal");
    expect(large.getByText("désactivées")).toBeVisible();

    const refused = within(within(table).getByRole("row", { name: /Gemma 4 12B/ }));
    expect(refused.getByText("chargement refusé")).toBeVisible();
    expect(refused.getByText("Not enough memory to load the model.")).toBeVisible();
    expect(screen.getByText(/« Qwen3 VL 4B » a été rechargé/)).toBeVisible();
  });

  it("draws what each model measured", () => {
    runs = [aRun()];
    run = aRun({ ratings: { k1: { [SMALL]: 3, [LARGE]: 1 } } });
    render(<BenchPage />);
    // Memory against speed: one point per tested model, named, with its figures.
    const scatter = screen.getByRole("img", { name: /Mémoire occupée et temps par image/ });
    expect(scatter).toHaveAccessibleName(/Qwen3 VL 4B : 5,3\sGo, 2,7 s par image, qualité 3,00/);
    expect(scatter).toHaveAccessibleName(/Qwen3\.5 9B : 11,5\sGo, 5,4 s par image, carte pleine/);
    expect(scatter).not.toHaveAccessibleName(/Gemma/); // it could not be loaded
    expect(within(scatter).getByText("Carte : 12 Go")).toBeInTheDocument();
    expect(within(scatter).getByText("Secondes par image")).toBeInTheDocument();
    // One bar chart per measure, the best value standing out.
    const charts = within(screen.getByRole("region", { name: "Graphiques" }));
    const memory = within(charts.getByRole("figure", { name: /Mémoire occupée/ }));
    expect(memory.getByText("le moins est le mieux")).toBeVisible();
    expect(memory.getByText("5,3 Go")).toHaveClass("text-brand-teal");
    expect(memory.getByText("11,5 Go")).not.toHaveClass("text-brand-teal");
    const positions = within(charts.getByRole("figure", { name: /Sujets retrouvés/ }));
    expect(positions.getByText("100 %")).toBeVisible();
    expect(positions.getByText("désactivées")).toBeVisible();
    const quality = within(charts.getByRole("figure", { name: /Qualité/ }));
    expect(quality.getByText("3,00")).toHaveClass("text-brand-teal");
    // Nothing stands out where every model did the same.
    const twice = aRun();
    for (const model of twice.model_runs) {
      model.scores.seconds_per_image = 4;
    }
    expect(best(rowsOfRun(twice), MEASURES.speed.value, "min")).toBeNull();
  });

  it("keeps the fast models apart when one is far slower", () => {
    const slow = aRun();
    const large = slow.model_runs[1];
    if (large) {
      large.scores.seconds_per_image = 100; // it spilled into the system memory
    }
    runs = [slow];
    run = slow;
    render(<BenchPage />);
    const scatter = within(screen.getByRole("img", { name: /Mémoire occupée et temps par image/ }));
    expect(scatter.getByText(/Secondes par image \(échelle logarithmique/)).toBeInTheDocument();
    for (const tick of ["5", "20", "50", "100"]) {
      // (2 and 10 are also on the memory axis)
      expect(scatter.getByText(tick)).toBeInTheDocument();
    }
  });

  it("ranks the tested models, in total and measure by measure", () => {
    runs = [aRun()];
    run = aRun();
    const { unmount } = render(<BenchPage />);
    const total = within(screen.getByRole("list", { name: "Classement total" }));
    const [first, second] = total.getAllByRole("listitem");
    expect(first).toHaveTextContent(/1er.*Qwen3 VL 4B.*71,2 points/);
    expect(second).toHaveTextContent(/2e.*Qwen3\.5 9B.*47,4 points/);
    expect(total.queryByText(/Gemma/)).not.toBeInTheDocument(); // it could not be loaded
    expect(
      screen.getByText(/Sur 100 points.*\(Mémoire, Vitesse, Langue, Texte lu, Positions\)/),
    ).toBeVisible();
    // Measure by measure: the place, and the points it brings.
    const places = within(screen.getByRole("table", { name: "Classement par mesure" }));
    expect(places.queryByRole("columnheader", { name: "Qualité" })).not.toBeInTheDocument();
    const large = within(places.getByRole("row", { name: /Qwen3\.5 9B/ }));
    expect(large.getAllByText("2e")).toHaveLength(4); // total, memory, speed, positions
    expect(large.getAllByText("1er")).toHaveLength(2); // language, text read
    expect(large.getByText("0 pts")).toBeVisible(); // its positions are disabled
    // A profile per model: one branch per measure.
    expect(
      screen.getByRole("img", {
        name: "Profil de Qwen3 VL 4B. Mémoire 56, Vitesse 100, Langue 75, Texte lu 25, Positions 100",
      }),
    ).toBeInTheDocument();
    unmount();

    // What matters most is remembered, and weighs three times.
    window.localStorage.setItem("vfe.bench.priority", "vram");
    render(<BenchPage />);
    expect(screen.getByText(/Mémoire compte triple\./)).toBeVisible();
    expect(screen.getByRole("list", { name: "Classement total" })).toHaveTextContent(
      /66,9 points.*35,0 points/,
    );
  });

  it("keeps every test in a history", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    runs = [aRun(), anEarlierRun()];
    render(<BenchPage />);
    const history = within(screen.getByRole("table", { name: "Historique" }));
    expect(history.getAllByRole("row")).toHaveLength(3);
    const earlier = within(history.getByRole("row", { name: /Comparaison manuelle/ }));
    expect(earlier.getByText("Qwen3 VL 4B · Gemma 4 12B")).toBeVisible();
    expect(earlier.getByText("32")).toBeVisible();
    // The latest run is shown first; a click shows another one.
    const results = () => within(screen.getByRole("table", { name: "Résultats" }));
    expect(results().getByRole("row", { name: /Qwen3\.5 9B/ })).toBeVisible();
    await user.click(earlier.getByRole("button", { name: /Afficher le test du/ }));
    expect(results().queryByRole("row", { name: /Qwen3\.5 9B/ })).not.toBeInTheDocument();
    expect(results().getByText("6,5 s par image")).toBeVisible();
    expect(earlier.getByRole("button", { name: /Afficher le test du/ })).toHaveAttribute(
      "aria-current",
      "true",
    );

    await user.click(earlier.getByRole("button", { name: /Supprimer le test du/ }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(remove.mock.calls[0]?.[0]).toBe("run0");
    confirm.mockRestore();
  });

  it("puts the latest result of every model side by side", async () => {
    const user = userEvent.setup();
    runs = [aRun(), anEarlierRun()];
    render(<BenchPage />);
    await user.click(screen.getByRole("button", { name: "Classement général" }));
    expect(screen.getByText(/Le dernier résultat de chacun des 3 modèles/)).toBeVisible();
    expect(screen.getByText(/n'ont pas tous porté sur les mêmes images/)).toBeVisible();
    const table = within(screen.getByRole("table", { name: "Résultats" }));
    // The small model was tested twice: its latest measure stands, the earlier one is history.
    const small = within(table.getByRole("row", { name: /Qwen3 VL 4B/ }));
    expect(small.getByText("2,7 s par image")).toBeVisible();
    expect(table.queryByText("3,4 s par image")).not.toBeInTheDocument();
    // Gemma was only tested in the earlier run, where it had been rated.
    const gemma = within(table.getByRole("row", { name: /Gemma 4 12B/ }));
    expect(gemma.getByText("2,47 sur 3")).toBeVisible();
    expect(gemma.getByText("32 notes")).toBeVisible();
    expect(small.getByText("pas encore noté")).toBeVisible();
    expect(table.queryByText("chargement refusé")).not.toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Mémoire occupée et temps par image/ }),
    ).toHaveAccessibleName(/Gemma 4 12B : 8,6\sGo, 6,5 s par image/);
    expect(screen.queryByText("Juger à l'aveugle")).not.toBeInTheDocument();
    // The general ranking: every model, on the measures they all have.
    const total = within(screen.getByRole("list", { name: "Classement total" }));
    expect(total.getAllByRole("listitem")).toHaveLength(3);
    // The earlier test only measured memory and speed for Gemma: the rest is not counted.
    expect(
      screen.getByText(/Langue, Texte lu, Positions, Qualité n'entrent pas dans le total/),
    ).toBeVisible();

    // Each measure leads back to the test it comes from.
    await user.click(gemma.getByRole("button", { name: /test du .* · 32 images/ }));
    expect(screen.getByText("Comparaison manuelle", { selector: "p span" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Classement général" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("writes a note on a test", async () => {
    const user = userEvent.setup();
    runs = [aRun()];
    render(<BenchPage />);
    await user.click(screen.getByRole("button", { name: "Ajouter une note" }));
    await user.type(screen.getByLabelText("Note sur ce test"), "  avant la mise à jour ");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(annotate.mock.calls[0]?.[0]).toEqual({ runId: "run1", note: "avant la mise à jour" });
  });

  it("rates the descriptions blind", async () => {
    const user = userEvent.setup();
    runs = [aRun()];
    run = aRun({ ratings: { k1: { [SMALL]: 3 } } });
    render(<BenchPage />);
    expect(screen.getByText("1 sur 3 descriptions notées")).toBeVisible();
    expect(screen.getByRole("img", { name: "Image 1 du test" })).toHaveAttribute(
      "src",
      "/api/v1/media/v1/k1.jpg",
    );
    // A letter per description, in an order that depends on the frame, and no model name.
    const [first, second] = blindOrder("run1", "k1", [SMALL, LARGE]);
    const groupA = within(screen.getByRole("group", { name: "Note de la description A" }));
    const item = screen.getByRole("group", { name: "Note de la description A" }).closest("li");
    expect(item).toHaveTextContent(first === SMALL ? "Un chat dort." : "Un chien court.");
    expect(item).not.toHaveTextContent(/Qwen/);

    await user.click(groupA.getByRole("button", { name: "Presque juste" }));
    expect(rate.mock.calls[0]?.[0]).toEqual({ keyframe_id: "k1", model: first, rating: 2 });
    // The mark already given is shown, and pressing it again takes it back.
    const letter = second === SMALL ? "B" : "A";
    const given = within(screen.getByRole("group", { name: `Note de la description ${letter}` }));
    expect(given.getByRole("button", { name: "Juste" })).toHaveAttribute("aria-pressed", "true");
    await user.click(given.getByRole("button", { name: "Juste" }));
    expect(rate.mock.calls[1]?.[0]).toEqual({ keyframe_id: "k1", model: SMALL, rating: null });

    await user.click(screen.getByRole("button", { name: "Suivante" }));
    expect(screen.getByText("Image 2 sur 2")).toBeVisible();
    // Only the model that answered is rated on this frame.
    expect(screen.getAllByRole("group", { name: /Note de la description/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Suivante" })).toBeDisabled();
  });

  it("follows a running test and stops it", async () => {
    const user = userEvent.setup();
    overview = anOverview({ active_run_id: "run1" });
    const running = aRun({ status: "running", progress: 0.1, message: null, frames: [] });
    running.model_runs = [
      { ...tested(SMALL, "Qwen3 VL 4B", {}), status: "running" },
      { ...tested(LARGE, "Qwen3.5 9B", {}), status: "pending" },
    ];
    runs = [running];
    run = running;
    jobs = [
      {
        id: "job1",
        progress: 0.42,
        message: "Qwen3 VL 4B : descriptions (9/24)",
        cancel_requested: false,
      },
    ];
    render(<BenchPage />);
    expect(screen.getByText("Qwen3 VL 4B : descriptions (9/24)")).toBeVisible();
    expect(screen.getByText("42 %")).toBeVisible();
    expect(screen.getByText("Qwen3.5 9B · à venir")).toBeVisible();
    expect(screen.getByRole("button", { name: "Un test est en cours" })).toBeDisabled();
    expect(screen.queryByText("Juger à l'aveugle")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Arrêter le test" }));
    expect(cancel.mock.calls[0]?.[0]).toBe("job1");
  });
});

describe("benchFormat", () => {
  it("gives a frame its own order of models, the same at every visit", () => {
    const keys = ["a", "b", "c", "d", "e"];
    const orders = ["k1", "k2", "k3", "k4", "k5", "k6"].map((frame) =>
      blindOrder("run", frame, keys).join(""),
    );
    expect(new Set(orders).size).toBeGreaterThan(1);
    expect(blindOrder("run", "k1", [...keys].reverse()).join("")).toBe(orders[0]);
    expect((orders[0] ?? "").split("").sort().join("")).toBe("abcde");
  });

  it("averages the ratings and counts what is left to rate", () => {
    const rated = aRun({ ratings: { k1: { [SMALL]: 3, [LARGE]: 0 }, k2: { [SMALL]: 2 } } });
    expect(ratingOf(rated, SMALL)).toEqual({ count: 2, mean: 2.5 });
    expect(ratingOf(rated, LARGE)).toEqual({ count: 1, mean: 0 });
    expect(ratingOf(rated, "other")).toEqual({ count: 0, mean: null });
    expect(ratingProgress(rated)).toEqual({ rated: 3, total: 3 });
  });

  it("finds the best value only when there is something to compare", () => {
    const rows = rowsOfRun(aRun());
    expect(best(rows, (row) => row.model.scores.vram_mib, "min")).toBe(5454);
    expect(best(rows, MEASURES.text.value, "max")).toBe(0.83);
    expect(best(rows.slice(0, 1), MEASURES.vram.value, "min")).toBeNull();
    expect(best(rows, MEASURES.positions.value, "max")).toBeNull(); // one model only
  });

  it("keeps the latest measure of each model, language by language", () => {
    const english = { ...anEarlierRun(), id: "run-en", language: "en" };
    const history = [aRun(), anEarlierRun(), english];
    expect(historyLanguages(history)).toEqual(["fr", "en"]);
    const rows = rowsOfHistory(history, "fr");
    expect(rows.map((row) => [row.label, row.origin?.id])).toEqual([
      ["Qwen3 VL 4B", "run1"],
      ["Qwen3.5 9B", "run1"],
      ["Gemma 4 12B", "run0"],
    ]);
    expect(mixedImages(rows)).toBe(true);
    expect(mixedImages(rowsOfHistory(history, "en"))).toBe(false);
    expect(rowsOfHistory(history, "en").map((row) => row.origin?.id)).toEqual(["run-en", "run-en"]);
    // A run still going has no result yet; a model under two quantizations is two models.
    const running = { ...aRun(), id: "run2", status: "running" as const };
    expect(rowsOfHistory([running], "fr")).toEqual([]);
    const heavier = anEarlierRun();
    heavier.model_runs = [{ ...tested(SMALL, "Qwen3 VL 4B", {}), quantization: "Q8_0" }];
    expect(rowsOfHistory([aRun(), heavier], "fr").map((row) => row.label)).toEqual([
      "Qwen3 VL 4B Q4_K_M",
      "Qwen3 VL 4B Q8_0",
      "Qwen3.5 9B",
    ]);
  });

  it("ranks on the measures every model has", () => {
    const rows = rowsOfRun(aRun());
    const { ranked, counted, left } = rank(rows);
    expect(counted).toEqual(["vram", "speed", "language", "text", "positions"]);
    expect(left).toEqual([]);
    const [small, large] = ranked;
    expect(ranked).toHaveLength(2); // the model that could not be loaded is not ranked
    expect(small?.points).toEqual({ vram: 56, speed: 100, language: 75, text: 25, positions: 100 });
    expect(large?.points).toEqual({ vram: 4, speed: 50, language: 100, text: 83, positions: 0 });
    expect([small?.total, small?.place]).toEqual([71.2, 1]);
    expect([large?.total, large?.place]).toEqual([47.4, 2]);
    expect(large?.places).toEqual({ vram: 2, speed: 2, language: 1, text: 1, positions: 2 });
    expect(rank(rows, "vram").ranked.map((model) => model.total)).toEqual([66.9, 35]);

    // Rated by one model only, quality is shown but not counted; rated by all, it is.
    const partly = rowsOfRun(aRun({ ratings: { k1: { [SMALL]: 3 } } }));
    expect(rank(partly).left).toEqual(["quality"]);
    expect(rank(partly).ranked[0]?.total).toBe(71.2);
    const rated = rank(rowsOfRun(aRun({ ratings: { k1: { [SMALL]: 3, [LARGE]: 1 } } })));
    expect(rated.counted).toContain("quality");
    expect(rated.ranked.map((model) => [model.points.quality, model.total])).toEqual([
      [100, 76],
      [33, 45],
    ]);

    // Equal points share a place, and the next one is skipped.
    const twins = aRun();
    twins.model_runs = [
      tested("a", "A", { seconds_per_image: 2 }),
      tested("b", "B", { seconds_per_image: 2 }),
      tested("c", "C", { seconds_per_image: 8 }),
    ];
    expect(rank(rowsOfRun(twins)).ranked.map((model) => model.places.speed)).toEqual([1, 1, 3]);
    expect(rank([]).ranked).toEqual([]);
  });

  it("writes places and draws profiles", () => {
    expect([1, 2, 3].map((place) => ordinal(place, "fr"))).toEqual(["1er", "2e", "3e"]);
    expect([1, 2, 3, 4, 11, 21].map((place) => ordinal(place, "en"))).toEqual([
      "1st",
      "2nd",
      "3rd",
      "4th",
      "11th",
      "21st",
    ]);
    const corners = radarPoints([100, 50, 0, 100], { x: 100, y: 100 }, 60);
    const rounded = corners.map((corner) => [Math.round(corner.x), Math.round(corner.y)]);
    expect(rounded).toEqual([
      [100, 40], // up
      [130, 100], // right, half way
      [100, 100], // nothing: the centre
      [40, 100], // left
    ]);
  });

  it("writes the names of close points without covering them", () => {
    const bounds = { left: 0, right: 300, top: 0, bottom: 200 };
    const [first, second, third] = placeLabels(
      [
        { x: 100, y: 100, width: 60 },
        { x: 104, y: 102, width: 60 }, // nearly on the first one
        { x: 280, y: 50, width: 60 }, // no room on its right
      ],
      bounds,
    );
    expect(first).toMatchObject({ anchor: "end", x: 91 }); // its right is the second point
    expect(second).toMatchObject({ anchor: "start", x: 113 });
    expect(third).toMatchObject({ anchor: "end", x: 271 });
  });
});
