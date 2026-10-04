import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { McpAccessCard } from "./McpAccessCard";

const mutate = vi.fn();
let settings: { mcp_add_folders: boolean } | undefined;
vi.mock("@/api/queries", () => ({
  useAnalysisSettings: () => ({ data: settings }),
  usePatchAnalysisSettings: () => ({ mutate, isPending: false }),
}));

beforeEach(() => {
  mutate.mockReset();
  settings = { mcp_add_folders: false };
});

describe("McpAccessCard", () => {
  it("keeps Claude from adding folders by default, and allows it on demand", async () => {
    const user = userEvent.setup();
    render(<McpAccessCard />);
    const toggle = screen.getByRole("switch", {
      name: "Autoriser Claude à ajouter des dossiers à la bibliothèque",
    });
    expect(toggle).not.toBeChecked();
    expect(toggle).toHaveAccessibleDescription(/analyze_folder/);
    expect(screen.getByText(/Désactivé par défaut/)).toBeVisible();
    await user.click(toggle);
    expect(mutate).toHaveBeenCalledWith({ mcp_add_folders: true });
  });

  it("waits for the settings", () => {
    settings = undefined;
    render(<McpAccessCard />);
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
