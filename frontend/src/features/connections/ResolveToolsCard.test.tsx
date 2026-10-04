import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ResolveToolsCard } from "./ResolveToolsCard";

const mutate = vi.fn();
let settings: { mcp_resolve_tools: boolean } | undefined;
vi.mock("@/api/queries", () => ({
  useAnalysisSettings: () => ({ data: settings }),
  usePatchAnalysisSettings: () => ({ mutate, isPending: false }),
}));

beforeEach(() => {
  mutate.mockReset();
  settings = { mcp_resolve_tools: false };
});

describe("ResolveToolsCard", () => {
  it("is off by default and switches the assistant's Resolve tools on", async () => {
    const user = userEvent.setup();
    render(<ResolveToolsCard />);
    const toggle = screen.getByRole("switch", {
      name: "Outils DaVinci Resolve améliorés pour l'assistant",
    });
    expect(toggle).not.toBeChecked();
    expect(toggle).toHaveAccessibleDescription(/build_timeline/);
    expect(screen.getByText(/jamais une timeline existante/)).toBeVisible();
    await user.click(toggle);
    expect(mutate).toHaveBeenCalledWith({ mcp_resolve_tools: true });
  });

  it("waits for the settings", () => {
    settings = undefined;
    render(<ResolveToolsCard />);
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
