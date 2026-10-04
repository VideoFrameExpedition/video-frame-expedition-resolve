import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { saveBlob } from "@/lib/download";

import { SelectionBar } from "./SelectionBar";
import { resetSelection, selectVideos, useSelectedVideos } from "./selection";

const mutate = vi.fn();
let isPending = false;
vi.mock("@/api/queries", () => ({
  useExportSidecars: () => ({ mutate: vi.fn(), isPending: false }),
  useExportCsv: () => ({ mutate, isPending }),
  useBuildResolveTimeline: () => ({ mutateAsync: vi.fn(), isPending: false }), // see TimelineBuildDialog.test
}));
vi.mock("@/lib/download", () => ({ saveBlob: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

type Options =
  | {
      onSuccess?: (data: { blob: Blob; filename: string }) => void;
      onError?: (error: unknown) => void;
    }
  | undefined;

function Toolbar({ ids }: { ids: string[] }) {
  return <SelectionBar ids={ids} selected={useSelectedVideos()} />;
}

beforeEach(() => {
  mutate.mockReset();
  isPending = false;
  vi.mocked(saveBlob).mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
  resetSelection();
});

describe("CsvButton", () => {
  it("downloads a table of the ticked videos from the selection toolbar", async () => {
    const user = userEvent.setup();
    render(<Toolbar ids={["a", "b", "c"]} />);
    expect(screen.queryByRole("button", { name: /tableau/ })).not.toBeInTheDocument();
    act(() => {
      selectVideos(["a", "c"], true);
    });
    const button = screen.getByRole("button", {
      name: "Télécharger un tableau des vidéos sélectionnées (CSV pour Excel, une ligne par vidéo)",
    });
    expect(button).toHaveTextContent("CSV");
    const blob = new Blob(["﻿fichier;dossier\r\n"], { type: "text/csv" });
    mutate.mockImplementation((_ids: string[], options: Options) => {
      options?.onSuccess?.({ blob, filename: "vfe-videos-20260928-1200.csv" });
    });
    await user.click(button);
    expect(mutate.mock.calls[0]?.[0]).toEqual(["a", "c"]);
    expect(saveBlob).toHaveBeenCalledWith(blob, "vfe-videos-20260928-1200.csv");
    expect(toast.success).toHaveBeenCalledWith("Tableau de 2 vidéos téléchargé");
  });

  it("reports a refusal, and waits for the download under way", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Toolbar ids={["a"]} />);
    act(() => {
      selectVideos(["a"], true);
    });
    mutate.mockImplementation((_ids: string[], options: Options) => {
      options?.onError?.(
        new ApiError(404, "not_found", "Aucune de ces vidéos n'est dans la bibliothèque."),
      );
    });
    await user.click(screen.getByRole("button", { name: /tableau/ }));
    expect(toast.error).toHaveBeenCalledWith("Aucune de ces vidéos n'est dans la bibliothèque.");
    expect(saveBlob).not.toHaveBeenCalled();

    isPending = true;
    rerender(<Toolbar ids={["a"]} />);
    expect(screen.getByRole("button", { name: /tableau/ })).toBeDisabled();
  });
});
