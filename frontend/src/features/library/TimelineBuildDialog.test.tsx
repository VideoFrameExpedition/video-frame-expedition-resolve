import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { ApiError, type Schemas } from "@/api/client";
import type { TimelineBuild } from "@/api/queries";
import { saveBlob } from "@/lib/download";

import { setTimelineBuildOpen } from "./selection";
import { TimelineBuildDialog } from "./TimelineBuildDialog";
import { PROJECT } from "./timelineFixtures";

type Plan = Schemas["TimelinePlanOut"];

interface Read<T> {
  data?: T;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
  refetch: () => Promise<unknown>;
}
const reading = <T,>(data?: T, error?: unknown): Read<T> => ({
  data,
  isError: error !== undefined,
  error: error ?? null,
  isFetching: false,
  refetch: vi.fn(() => Promise.resolve()),
});

const PLAN: Plan = {
  name: "VFE 2026-09-15",
  suggested_name: "VFE 2026-09-15",
  file_name: "VFE 2026-09-15.zip",
  frame_rate: "29.97",
  fps: 29.97,
  width: 3840,
  height: 2160,
  suggested_frame_rate: "29.97",
  suggested_width: 3840,
  suggested_height: 2160,
  videos: 5,
  files: ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4"],
  duration_s: 750,
  rates: [
    { frame_rate: "29.97", fps: 29.97, videos: 4 },
    { frame_rate: "120", fps: 120, videos: 1 },
  ],
  sizes: [
    { width: 3840, height: 2160, videos: 3 },
    { width: 2160, height: 3840, videos: 2 },
  ],
  skipped: [
    { video_id: "x", filename: "gone.mp4", reason: "offline" },
    { video_id: "y", filename: "late.mp4", reason: "offline" },
    { video_id: "z", filename: "new.mp4", reason: "not_examined" },
  ],
  resolve_host: null,
  chapters: 8,
  chaptered: 2,
  suggestions: { highlights: 1, establishing: 2, b_roll: 0, avoid: 1 },
  speech_subtitles: 40,
  shot_subtitles: 12,
};
const NOTHING: Partial<Plan> = {
  chapters: 0,
  chaptered: 0,
  suggestions: { highlights: 0, establishing: 0, b_roll: 0, avoid: 0 },
  speech_subtitles: 0,
  shot_subtitles: 0,
};
/** What the preview asks: everything counted, whatever is ticked. */
const COUNTED = { transcript: true, suggestions: true, chapters: true, shots: true };

let plan: Read<Plan>;
let project: Read<Schemas["ResolveProjectOut"]>;
const planned: TimelineBuild[] = []; // what the dialog asked the server for, in order
const exportFile = vi.fn();
const build = vi.fn();
vi.mock("@/api/queries", () => ({
  useTimelinePlan: (request: TimelineBuild) => {
    planned.push(request);
    return plan;
  },
  useResolveProject: () => project,
  useExportTimeline: () => ({ mutate: exportFile, isPending: false }),
  useBuildResolveTimeline: () => ({ mutateAsync: build, isPending: false }),
}));
vi.mock("@/lib/download", () => ({ saveBlob: vi.fn() }));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

// Radix Select asks the pointer and scrolls its list: jsdom has neither.
Element.prototype.hasPointerCapture = () => false;
Element.prototype.releasePointerCapture = () => undefined;
Element.prototype.scrollIntoView = () => undefined;

const openDialog = async (user: ReturnType<typeof userEvent.setup>) => {
  render(<TimelineBuildDialog ids={["v1", "v2"]} />);
  await user.click(screen.getByRole("button", { name: "Créer une timeline" }));
  return within(screen.getByRole("dialog"));
};

beforeEach(() => {
  setTimelineBuildOpen(false);
  window.localStorage.removeItem("vfe.timelineBuild.parts");
  plan = reading(PLAN);
  project = reading(PROJECT);
  planned.length = 0;
  exportFile.mockReset();
  build.mockReset();
  vi.mocked(saveBlob).mockReset();
  for (const toastOf of [toast.success, toast.error, toast.warning, toast.info]) {
    vi.mocked(toastOf).mockReset();
  }
});

describe("TimelineBuildDialog", () => {
  it("shows the timeline the ticked videos make, and what is left out", async () => {
    const user = userEvent.setup();
    const dialog = await openDialog(user);
    expect(planned.at(-1)).toEqual({
      video_ids: ["v1", "v2"],
      order: "capture",
      frame_rate: null,
      width: null,
      height: null,
      ...COUNTED,
    });
    expect(dialog.getByText("5 vidéos").closest("p")).toHaveTextContent(
      "5 vidéos · 12:30 · 3840 × 2160 · 29,97 i/s",
    );
    expect(dialog.getByText(/Dans l'ordre/)).toHaveTextContent(
      "Dans l'ordre : a.mp4, b.mp4, c.mp4 et 2 autres",
    );
    expect(dialog.getByText("2 vidéos hors ligne (fichiers introuvables)")).toHaveAttribute(
      "title",
      "gone.mp4\nlate.mp4",
    );
    expect(dialog.getByText(/1 vidéo pas encore examinée/)).toBeVisible();
    expect(dialog.getByLabelText("Nom de la timeline")).toHaveAttribute(
      "placeholder",
      "VFE 2026-09-15",
    );
    expect(dialog.getByRole("combobox", { name: "Ordre" })).toHaveTextContent("Date de tournage");
    expect(dialog.getByRole("combobox", { name: "Cadence" })).toHaveTextContent("29,97 i/s");
    expect(dialog.getByRole("combobox", { name: "Format d'image" })).toHaveTextContent(
      "3840 × 2160",
    );
  });

  it("asks again when the order, the rate or the size is changed", async () => {
    const user = userEvent.setup();
    const dialog = await openDialog(user);
    await user.click(dialog.getByRole("combobox", { name: "Ordre" }));
    await user.click(screen.getByRole("option", { name: "Nom du fichier" }));
    await user.click(dialog.getByRole("combobox", { name: "Cadence" }));
    const rates = screen.getAllByRole("option").map((option) => option.textContent);
    // The videos' own rates with their count, among the usual ones, slowest first.
    expect(rates).toContain("29,97 i/s · 4 vidéos");
    expect(rates).toContain("120 i/s · 1 vidéo");
    expect(rates.indexOf("25 i/s")).toBeLessThan(rates.indexOf("29,97 i/s · 4 vidéos"));
    await user.click(screen.getByRole("option", { name: "25 i/s" }));
    await user.click(dialog.getByRole("combobox", { name: "Format d'image" }));
    await user.click(screen.getByRole("option", { name: "2160 × 3840 · 2 vidéos" }));
    expect(planned.at(-1)).toEqual({
      video_ids: ["v1", "v2"],
      order: "name",
      frame_rate: "25",
      width: 2160,
      height: 3840,
      ...COUNTED,
    });
  });

  it("adds the timeline to the project open in Resolve", async () => {
    const user = userEvent.setup();
    build.mockResolvedValue({
      project: { id: "p1", name: "cats 2026" },
      timeline_name: "Été (2)",
      clips: 5,
      missing: ["D:\\cats 2026\\e.mp4"],
      fps: 25,
      markers: 7,
      markers_missed: 1,
      subtitles: ["Été - transcription", "Été - plans"],
      subtitles_laid: ["Transcription", "Plans"],
      subtitle_files: [
        {
          video_id: "v1",
          part: "transcript",
          file: "e.srt",
          folder: "D:\\cats 2026",
          status: "written",
          detail: null,
        },
        {
          video_id: "v1",
          part: "shots",
          file: "e_SHOTS.srt",
          folder: "D:\\cats 2026",
          status: "conflict",
          detail: "laissé tel quel",
        },
      ],
    });
    const dialog = await openDialog(user);
    expect(
      dialog.getByText(
        "Ouvert, projet « cats 2026 » : la timeline peut y être ajoutée directement.",
      ),
    ).toBeVisible();
    expect(dialog.getByText(/Les marqueurs sont posés sur les clips/)).toHaveTextContent(
      /Les sous-titres sont posés sur la timeline, une piste par sorte/,
    );
    await user.type(dialog.getByLabelText("Nom de la timeline"), "Été");
    await user.click(dialog.getByRole("button", { name: "Ajouter au projet en cours" }));
    expect(build).toHaveBeenCalledWith({
      video_ids: ["v1", "v2"],
      order: "capture",
      frame_rate: null,
      width: null,
      height: null,
      name: "Été",
      transcript: true,
      suggestions: true,
      chapters: true,
      shots: false,
    });
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith(
        "Timeline « Été (2) » créée dans « cats 2026 » : 5 clips",
        { description: "7 marqueurs" },
      );
    });
    expect(toast.warning).toHaveBeenCalledWith(
      "1 marqueur n'a pas pu être posé (après la fin du clip, ou aucune image libre à côté)",
    );
    expect(toast.info).toHaveBeenCalledWith(
      "Sous-titres posés sur la timeline : Transcription, Plans",
      expect.objectContaining({
        description: expect.stringContaining("Une piste par sorte.") as unknown,
      }),
    );
    expect(toast.warning).toHaveBeenCalledWith(
      "Un fichier de sous-titres n'a pas été écrit",
      expect.objectContaining({ description: "e_SHOTS.srt (D:\\cats 2026) : laissé tel quel" }),
    );
    expect(toast.warning).toHaveBeenCalledWith("DaVinci Resolve n'a pas pu ouvrir 1 fichier", {
      description: "D:\\cats 2026\\e.mp4",
    });
    expect(toast.info).toHaveBeenCalledWith(
      "Resolve a gardé la cadence du projet pour cette timeline : 25 i/s.",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lays the tracks from next to the first video for Resolve on another computer, else leaves the files in the bin", async () => {
    const user = userEvent.setup();
    plan = reading({ ...PLAN, resolve_host: "mac-studio" });
    build.mockResolvedValue({
      project: { id: "p1", name: "cats 2026" },
      timeline_name: "Été",
      clips: 2,
      missing: [],
      fps: null,
      markers: 0,
      markers_missed: 0,
      subtitles: ["e", "e_SHOTS"],
      subtitles_laid: [],
      subtitle_files: [],
    });
    const dialog = await openDialog(user);
    await user.click(
      dialog.getByRole("checkbox", { name: "Description des plans en sous-titres" }),
    );
    expect(dialog.getByText(/Les marqueurs sont posés sur les clips/)).toHaveTextContent(
      /écrits à côté de sa première vidéo \(<timeline>_TIMELINE_FR\.srt\), que l'ordinateur de DaVinci Resolve lit/,
    );
    // No folder of the videos took the tracks: their own files are in the bin.
    await user.click(dialog.getByRole("button", { name: "Ajouter au projet en cours" }));
    await waitFor(() => {
      expect(toast.info).toHaveBeenCalledWith(
        "2 fichiers de sous-titres, écrits à côté de leurs vidéos, dans le chutier « Video Frame Expedition »",
        expect.objectContaining({
          description: expect.stringContaining("(e, e_SHOTS) au tout début de sa vidéo") as unknown,
        }),
      );
    });
  });

  it("says when Resolve did not lay the subtitles", async () => {
    const user = userEvent.setup();
    build.mockResolvedValue({
      project: { id: "p1", name: "cats 2026" },
      timeline_name: "Été",
      clips: 2,
      missing: [],
      fps: null,
      markers: 0,
      markers_missed: 0,
      subtitles: ["Été - transcription"],
      subtitles_laid: [],
      subtitle_files: [],
    });
    const dialog = await openDialog(user);
    await user.click(dialog.getByRole("button", { name: "Ajouter au projet en cours" }));
    await waitFor(() => {
      expect(toast.info).toHaveBeenCalledWith(
        "DaVinci Resolve n'a pas posé les sous-titres : un fichier dans le chutier « Video Frame Expedition »",
        expect.objectContaining({
          description: expect.stringContaining("(Été - transcription) au tout début") as unknown,
        }),
      );
    });
  });

  it("offers the file when Resolve is not open", async () => {
    const user = userEvent.setup();
    project = reading<Schemas["ResolveProjectOut"]>(
      undefined,
      new ApiError(503, "resolve_unavailable", "DaVinci Resolve n'est pas lancé."),
    );
    type Options = { onSuccess?: (file: { blob: Blob; filename: string }) => void } | undefined;
    const blob = new Blob(["PK"], { type: "application/zip" });
    exportFile.mockImplementation((_request: TimelineBuild, options: Options) => {
      options?.onSuccess?.({ blob, filename: "VFE 2026-09-15.zip" });
    });
    const dialog = await openDialog(user);
    expect(dialog.getByText("DaVinci Resolve n'est pas lancé.")).toBeVisible();
    expect(dialog.queryByRole("button", { name: /Ajouter au projet/ })).not.toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "Télécharger les fichiers" }));
    expect(exportFile.mock.calls[0]?.[0]).toMatchObject({ name: null, order: "capture" });
    expect(saveBlob).toHaveBeenCalledWith(blob, "VFE 2026-09-15.zip");
    expect(toast.success).toHaveBeenCalledWith("Fichier « VFE 2026-09-15.zip » téléchargé", {
      description: expect.stringContaining("Fichier › Importer › Timeline") as unknown,
    });
  });

  it("offers four things to go with the timeline, and remembers the choice", async () => {
    const user = userEvent.setup();
    const first = await openDialog(user);
    const box = (name: string) => first.getByRole("checkbox", { name });
    expect(box("Transcription en sous-titres")).toBeChecked();
    expect(box("Transcription en sous-titres")).toHaveAccessibleDescription(
      "40 sous-titres, sur leur propre piste",
    );
    expect(box("Suggestions en marqueurs de durée")).toBeChecked();
    expect(box("Suggestions en marqueurs de durée")).toHaveAccessibleDescription(
      "4 marqueurs de durée : 1 moment fort (vert), 2 plans d'ensemble (cyan), 1 plan à éviter (rouge)",
    );
    expect(box("Chapitres en marqueurs")).toBeChecked();
    expect(box("Chapitres en marqueurs")).toHaveAccessibleDescription(
      "8 marqueurs, un au début de chaque chapitre, son titre pour nom (2 vidéos)",
    );
    expect(box("Description des plans en sous-titres")).not.toBeChecked();
    await user.click(box("Transcription en sous-titres"));
    await user.click(box("Chapitres en marqueurs"));
    await user.click(box("Description des plans en sous-titres"));
    const asked = planned.length;
    await user.click(first.getByRole("button", { name: "Télécharger les fichiers" }));
    expect(exportFile.mock.calls[0]?.[0]).toMatchObject({
      transcript: false,
      suggestions: true,
      chapters: false,
      shots: true,
    });
    expect(planned.slice(asked).every((r) => r.transcript && r.shots)).toBe(true);
    // Opened again later: the same choice.
    await user.keyboard("{Escape}");
    setTimelineBuildOpen(true);
    const again = within(await screen.findByRole("dialog"));
    expect(again.getByRole("checkbox", { name: "Transcription en sous-titres" })).not.toBeChecked();
    expect(
      again.getByRole("checkbox", { name: "Description des plans en sous-titres" }),
    ).toBeChecked();
  });

  it("says what the videos lack", async () => {
    const user = userEvent.setup();
    plan = reading({ ...PLAN, ...NOTHING });
    const dialog = await openDialog(user);
    const hints = {
      "Transcription en sous-titres": "Aucune parole transcrite dans ces vidéos.",
      "Suggestions en marqueurs de durée":
        "Aucune suggestion : elles viennent de la synthèse de l'analyse.",
      "Chapitres en marqueurs":
        "Aucune de ces vidéos n'a encore de chapitres : ils viennent de la synthèse de l'analyse.",
      "Description des plans en sous-titres": "Aucun plan décrit dans ces vidéos.",
    };
    for (const [name, hint] of Object.entries(hints)) {
      const box = dialog.getByRole("checkbox", { name });
      expect(box).toBeDisabled();
      expect(box).not.toBeChecked();
      expect(box).toHaveAccessibleDescription(hint);
    }
    expect(dialog.queryByText(/Les marqueurs sont posés/)).not.toBeInTheDocument();
    expect(dialog.queryByText(/Les sous-titres sont importés/)).not.toBeInTheDocument();
  });

  it("offers nothing when no ticked video can go in a timeline", async () => {
    const user = userEvent.setup();
    plan = reading({ ...PLAN, videos: 0, files: [] });
    const dialog = await openDialog(user);
    expect(
      dialog.getByText("Aucune des vidéos cochées ne peut entrer dans une timeline."),
    ).toBeVisible();
    expect(dialog.getByRole("button", { name: "Télécharger les fichiers" })).toBeDisabled();
    expect(dialog.getByRole("button", { name: "Ajouter au projet en cours" })).toBeDisabled();
  });
});
