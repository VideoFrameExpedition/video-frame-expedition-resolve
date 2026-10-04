import { render, screen, within } from "@testing-library/react";

import type { ExportOption } from "@/api/client";
import { ApiError } from "@/api/client";

import { ExportsTab } from "./ExportsTab";

const exports = vi.fn<() => unknown>();
vi.mock("@/api/queries", () => ({
  useExports: () => exports(),
}));

const option = (
  format: ExportOption["format"],
  filename: string,
  reason: string | null = null,
): ExportOption => ({
  format,
  filename,
  available: reason === null,
  reason,
  url: `/api/v1/videos/v1/exports/${format}`,
});

beforeEach(() => {
  exports.mockReset();
});

describe("ExportsTab", () => {
  it("offers one download per format and says why one is not possible", () => {
    exports.mockReturnValue({
      isError: false,
      data: {
        video_id: "v1",
        formats: [
          option(
            "srt",
            "vacances.srt",
            "Aucune parole transcrite (transcription pas encore faite, ou aucune parole fiable).",
          ),
          option("csv", "vacances.plans.csv"),
          option("edl", "vacances.marqueurs.edl"),
          option("resolve", "vacances.resolve.py"),
        ],
      },
    });
    render(<ExportsTab videoId="v1" />);
    expect(screen.getByRole("heading", { name: "Exports" })).toBeVisible();
    expect(screen.getByText(/Rien n'est écrit à côté de la vidéo/)).toBeVisible();

    const srt = screen.getByRole("button", { name: "Télécharger : Sous-titres SRT" });
    expect(srt).toBeDisabled();
    expect(srt).toHaveAccessibleDescription(/Aucune parole transcrite/);

    const csv = screen.getByRole("link", { name: "Télécharger : Plans (CSV)" });
    expect(csv).toHaveAttribute("href", "/api/v1/videos/v1/exports/csv");
    expect(csv).toHaveAttribute("download", "vacances.plans.csv");
    expect(csv).toHaveAccessibleDescription(/S'ouvre dans Excel/);

    const edl = screen.getByRole("link", { name: "Télécharger : Marqueurs (EDL)" });
    const row = edl.closest("li");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText(/Timeline Markers from EDL/)).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Télécharger : Script DaVinci Resolve" }),
    ).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("waits for the list, and tells a failure", () => {
    exports.mockReturnValue({ isError: false, data: undefined });
    const { rerender } = render(<ExportsTab videoId="v1" />);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    exports.mockReturnValue({
      isError: true,
      error: new ApiError(404, "not_found", "Vidéo introuvable : v1"),
    });
    rerender(<ExportsTab videoId="v1" />);
    expect(screen.getByText("Vidéo introuvable : v1")).toBeVisible();
  });
});
