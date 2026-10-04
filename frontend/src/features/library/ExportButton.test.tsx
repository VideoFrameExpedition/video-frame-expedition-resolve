import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import type { SidecarResult, SidecarStatus } from "@/api/client";
import { ApiError } from "@/api/client";

import { SelectionBar } from "./SelectionBar";
import { resetSelection, selectVideos, useSelectedVideos } from "./selection";

const mutate = vi.fn();
let isPending = false;
vi.mock("@/api/queries", () => ({
  useExportSidecars: () => ({ mutate, isPending }),
  useExportCsv: () => ({ mutate: vi.fn(), isPending: false }), // see CsvButton.test
  useBuildResolveTimeline: () => ({ mutateAsync: vi.fn(), isPending: false }), // see TimelineBuildDialog.test
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

const result = (
  video_id: string,
  status: SidecarStatus,
  detail: string | null = null,
): SidecarResult => ({
  video_id,
  status,
  file: status === "written" ? `${video_id}_FR.txt` : null,
  path: null,
  files: status === "written" ? [`${video_id}_FR.txt`, `${video_id}_EN.txt`] : [],
  detail,
});

type Options = { onSuccess?: (data: { results: SidecarResult[] }) => void } | undefined;
const answer = (results: SidecarResult[]): void => {
  mutate.mockImplementation((_ids: string[], options: Options) => {
    options?.onSuccess?.({ results });
  });
};

function Toolbar({ ids }: { ids: string[] }) {
  return <SelectionBar ids={ids} selected={useSelectedVideos()} />;
}

beforeEach(() => {
  mutate.mockReset();
  isPending = false;
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.info).mockReset();
  vi.mocked(toast.error).mockReset();
  resetSelection();
});

describe("ExportButton", () => {
  it("exports the ticked videos from the selection toolbar and sums it up", async () => {
    const user = userEvent.setup();
    render(<Toolbar ids={["a", "b", "c", "d"]} />);
    expect(screen.queryByRole("button", { name: "Exporter" })).not.toBeInTheDocument();

    act(() => {
      selectVideos(["a", "b", "c", "d", "gone"], true); // "gone" was removed since
    });
    const button = screen.getByRole("button", { name: "Exporter" });
    expect(button).toHaveAttribute(
      "title",
      "Écrire maintenant les fichiers d'analyse (<nom>_FR.txt, <nom>_EN.txt ; JSON, sans images) à côté de chaque vidéo sélectionnée",
    );
    answer([
      result("a", "written"),
      result("b", "conflict", "un fichier de ce nom, que l'application n'a pas écrit…"),
      result("c", "failed", "écriture refusée (dossier en lecture seule ou accès refusé)"),
      result("d", "not_analyzed"),
      result("gone", "unknown"),
    ]);
    await user.click(button);

    expect(mutate.mock.calls[0]?.[0]).toEqual(["a", "b", "c", "d", "gone"]);
    expect(toast.success).toHaveBeenCalledWith("Fichiers d'analyse écrits à côté de 1 vidéo", {
      description:
        "1 fichier du même nom, que l'application n'a pas écrit, est laissé tel quel. " +
        "1 fichier n'a pas pu être écrit : écriture refusée (dossier en lecture seule ou accès " +
        "refusé). 1 vidéo pas encore analysée : rien à écrire. " +
        "1 vidéo retirée de la bibliothèque a été désélectionnée.",
    });
    expect(screen.getByText(/4 vidéos sélectionnées/)).toBeVisible(); // "gone" was dropped
  });

  it("says so when nothing was written", async () => {
    const user = userEvent.setup();
    render(<Toolbar ids={["a", "b"]} />);
    act(() => {
      selectVideos(["a", "b"], true);
    });
    answer([result("a", "failed", "disque plein"), result("b", "failed", "disque plein")]);
    await user.click(screen.getByRole("button", { name: "Exporter" }));
    expect(toast.error).toHaveBeenCalledWith("Aucun fichier d'analyse écrit", {
      description: "2 fichiers n'ont pas pu être écrits (par exemple : disque plein).",
    });

    answer([result("a", "offline"), result("b", "offline")]);
    await user.click(screen.getByRole("button", { name: "Exporter" }));
    expect(toast.info).toHaveBeenCalledWith("Aucun fichier d'analyse écrit", {
      description: "2 vidéos hors ligne (fichiers inaccessibles).",
    });
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("reports a refused request, and waits for the one under way", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Toolbar ids={["a"]} />);
    act(() => {
      selectVideos(["a"], true);
    });
    mutate.mockImplementation(
      (_ids: string[], options?: { onError?: (error: unknown) => void }) => {
        options?.onError?.(
          new ApiError(403, "missing_client_header", "En-tête X-VFE-Client requis."),
        );
      },
    );
    await user.click(screen.getByRole("button", { name: "Exporter" }));
    expect(toast.error).toHaveBeenCalledWith("En-tête X-VFE-Client requis.");

    isPending = true;
    rerender(<Toolbar ids={["a"]} />);
    expect(screen.getByRole("button", { name: "Exporter" })).toBeDisabled();
  });
});
