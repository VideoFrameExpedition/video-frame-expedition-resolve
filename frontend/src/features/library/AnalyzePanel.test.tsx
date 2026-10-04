import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import type { BatchAnalysis, StageInfo } from "@/api/client";

import { AnalyzePanel } from "./AnalyzePanel";
import { resetSelection, selectStages, selectVideos } from "./selection";

const mutate = vi.fn();
const stage = (name: string, family: StageInfo["family"], requires: string[] = []): StageInfo => ({
  name,
  family,
  requires,
  after: [],
  resource: "cpu",
  optional: false,
});
const STAGES = [
  stage("probe", "file"),
  stage("keyframes", "image", ["probe"]),
  stage("vision_frames", "vision", ["keyframes"]),
  stage("transcript", "sound", ["probe"]),
];

let stages: { data?: StageInfo[]; isPending: boolean; isError: boolean; error?: unknown };
vi.mock("@/api/queries", () => ({
  useStages: () => stages,
  useAnalyzeVideos: () => ({ mutate, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));
vi.mock("@/features/jobs/StopAllButton", () => ({ StopAllButton: () => null })); // own tests

const answer = (result: BatchAnalysis): void => {
  mutate.mockImplementation(
    (_body: unknown, options?: { onSuccess?: (result: BatchAnalysis) => void }) => {
      options?.onSuccess?.(result);
    },
  );
};

const openStages = async (user: ReturnType<typeof userEvent.setup>): Promise<void> => {
  const toggle = screen.getByRole("button", { name: /Étapes à exécuter/ });
  if (toggle.getAttribute("aria-expanded") === "false") await user.click(toggle);
};

beforeEach(() => {
  stages = { data: STAGES, isPending: false, isError: false };
  mutate.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.info).mockReset();
  resetSelection();
});

describe("AnalyzePanel", () => {
  it("starts with the stage list folded, to leave room for the bins", async () => {
    const user = userEvent.setup();
    render(<AnalyzePanel />);
    const toggle = screen.getByRole("button", { name: "Étapes à exécuter (4/4)" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("checkbox", { name: "Fichier" })).not.toBeInTheDocument();
    await user.click(toggle);
    expect(screen.getByRole("checkbox", { name: "Fichier" })).toBeVisible();
  });

  it("analyses the ticked videos, every stage by default", async () => {
    const user = userEvent.setup();
    render(<AnalyzePanel />);
    await openStages(user);
    expect(screen.getByRole("button", { name: "Analyser" })).toBeDisabled();
    expect(
      screen.getByText("Cochez des vidéos dans la bibliothèque pour les analyser."),
    ).toBeVisible();

    act(() => {
      selectVideos(["a", "b", "gone"], true); // "gone" was removed from the library since
    });
    expect(screen.getByText(/3 vidéos sélectionnées/)).toBeVisible();
    answer({ queued: 1, up_to_date: 1, offline: 0, unknown: ["gone"] });
    await user.click(screen.getByRole("button", { name: "Analyser 3 vidéos" }));
    expect(screen.getByText("Toutes les étapes.")).toBeVisible();
    expect(screen.getByRole("radio", { name: /Compléter \(recommandé\)/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Compléter" }));

    expect(mutate.mock.calls[0]?.[0]).toEqual({
      video_ids: ["a", "b", "gone"],
      stages: null, // every stage: the folder rule (never analysed videos count too)
      mode: "complete",
    });
    expect(toast.success).toHaveBeenCalledWith("1 vidéo mise en file d'analyse", {
      description:
        "1 vidéo déjà à jour : choisissez « Mettre à jour » ou « Tout refaire » pour la relancer. " +
        "1 vidéo retirée de la bibliothèque a été désélectionnée.",
    });
    expect(screen.getByText(/2 vidéos sélectionnées/)).toBeVisible(); // "gone" was dropped
  });

  it("sends the ticked stages only, and says which ones they need", async () => {
    const user = userEvent.setup();
    render(<AnalyzePanel />);
    await openStages(user);
    act(() => {
      selectVideos(["a"], true);
    });
    await user.click(screen.getByRole("checkbox", { name: "Fichier" }));
    await user.click(screen.getByRole("checkbox", { name: "Image" }));

    expect(screen.getByRole("checkbox", { name: /Analyse du conteneur/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Analyse du conteneur/ })).toHaveAccessibleName(
      "Analyse du conteneur · requise",
    );
    expect(screen.getByRole("checkbox", { name: /Description des images/ })).toBeChecked();
    expect(window.localStorage.getItem("vfe.analyze.skippedStages")).toBe('["probe","keyframes"]'); // remembered for the next visit

    answer({ queued: 0, up_to_date: 0, offline: 1, unknown: [] });
    await user.click(screen.getByRole("button", { name: "Analyser 1 vidéo" }));
    expect(
      screen.getByText("Étapes cochées : Description des images, Transcription."),
    ).toBeVisible();
    expect(
      screen.getByText("Faites d'abord si elles manquent : Analyse du conteneur, Images clés."),
    ).toBeVisible();
    expect(screen.queryByText(/Refaites aussi/)).not.toBeInTheDocument(); // « Complete »
    await user.click(screen.getByRole("radio", { name: /Refaire les étapes cochées/ }));
    expect(screen.queryByText(/Refaites aussi/)).not.toBeInTheDocument(); // nothing needs them
    await user.click(screen.getByRole("button", { name: "Refaire" }));

    expect(mutate.mock.calls[0]?.[0]).toEqual({
      video_ids: ["a"],
      stages: ["vision_frames", "transcript"],
      mode: "full",
    });
    expect(toast.info).toHaveBeenCalledWith("Aucune vidéo mise en file", {
      description: "1 vidéo hors ligne (fichier inaccessible) n'a pas été analysée.",
    });
  });

  it("needs at least one stage", async () => {
    const user = userEvent.setup();
    render(<AnalyzePanel />);
    await openStages(user);
    act(() => {
      selectVideos(["a"], true);
    });
    await user.click(screen.getByRole("button", { name: "Aucune" }));
    expect(screen.getByRole("button", { name: "Analyser 1 vidéo" })).toBeDisabled();
    expect(screen.getByText("Cochez au moins une étape.")).toBeVisible();

    await user.click(screen.getByRole("checkbox", { name: /Transcription/ }));
    const sound = screen.getByRole("checkbox", { name: "Son et parole" });
    expect(sound).toBeChecked(); // its only stage
    expect(screen.getByRole("checkbox", { name: "Fichier" })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Toutes" }));
    for (const box of screen.getAllByRole("checkbox")) expect(box).toBeChecked();
    expect(screen.getByRole("button", { name: "Toutes" })).toBeDisabled();
  });

  it("says why no stage can be chosen, with the stage list folded", async () => {
    const user = userEvent.setup();
    stages = { data: undefined, isPending: false, isError: true, error: new Error("Hors ligne") };
    render(<AnalyzePanel />);
    act(() => {
      selectVideos(["a"], true);
    });
    const toggle = screen.getByRole("button", { name: /Étapes à exécuter/ });
    if (toggle.getAttribute("aria-expanded") === "true") await user.click(toggle); // kept open
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Hors ligne")).toBeVisible();
    expect(screen.queryByText("Cochez au moins une étape.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyser 1 vidéo" })).toBeDisabled();
  });

  it("forgets stages that no longer exist", async () => {
    const user = userEvent.setup();
    act(() => {
      selectStages(["stage_of_an_older_version"], false);
    });
    render(<AnalyzePanel />);
    await openStages(user);
    expect(screen.getByRole("button", { name: "Toutes" })).toBeDisabled(); // all shown are ticked
    await user.click(screen.getByRole("button", { name: "Aucune" }));
    await user.click(screen.getByRole("button", { name: "Toutes" }));
    expect(window.localStorage.getItem("vfe.analyze.skippedStages")).toBeNull();
  });

  it("says which stages a redo takes along", async () => {
    const user = userEvent.setup();
    render(<AnalyzePanel />);
    await openStages(user);
    act(() => {
      selectVideos(["a"], true);
    });
    await user.click(screen.getByRole("checkbox", { name: "Modèle de vision" }));
    await user.click(screen.getByRole("checkbox", { name: "Son et parole" }));
    await user.click(screen.getByRole("checkbox", { name: "Fichier" })); // only keyframes left
    await user.click(screen.getByRole("button", { name: "Analyser 1 vidéo" }));
    await user.click(screen.getByRole("radio", { name: /Refaire les étapes cochées/ }));
    expect(
      screen.getByText(
        "Refaites aussi quand elles ont déjà un résultat, car elles ont besoin des étapes " +
          "cochées : Description des images.",
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("radio", { name: /Mettre à jour/ }));
    expect(screen.getByText(/Si une étape cochée est refaite/)).toBeVisible();
  });
});
