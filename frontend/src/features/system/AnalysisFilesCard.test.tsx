import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AnalysisFilesCard } from "./AnalysisFilesCard";

const mutate = vi.fn();
let settings: { sidecar_files: boolean } | undefined;
vi.mock("@/api/queries", () => ({
  useAnalysisSettings: () => ({ data: settings }),
  usePatchAnalysisSettings: () => ({ mutate, isPending: false }),
}));

beforeEach(() => {
  mutate.mockReset();
  settings = { sidecar_files: true };
});

describe("AnalysisFilesCard", () => {
  it("writes the analysis files by default, and turns them off", async () => {
    const user = userEvent.setup();
    render(<AnalysisFilesCard />);
    const toggle = screen.getByRole("switch", {
      name: "Écrire le fichier d'analyse à côté des vidéos",
    });
    expect(toggle).toBeChecked();
    expect(screen.getByText(/partager le dossier les partage aussi/)).toBeVisible();
    await user.click(toggle);
    expect(mutate).toHaveBeenCalledWith({ sidecar_files: false });
  });

  it("waits for the settings", () => {
    settings = undefined;
    render(<AnalysisFilesCard />);
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
