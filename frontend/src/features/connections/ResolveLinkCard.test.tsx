import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ResolveLinkCard } from "./ResolveLinkCard";

const patch = vi.fn();
const refetch = vi.fn();
let probe: { data?: unknown; isError: boolean; isFetching: boolean; error?: unknown } = {
  isError: false,
  isFetching: false,
};
const probed: boolean[] = [];
vi.mock("@/api/queries", () => ({
  useAnalysisSettings: () => ({
    data: {
      resolve_host: null,
      resolve_folders: [{ here: "D:\\cats 2026", there: "/Volumes/cats" }],
    },
  }),
  usePatchAnalysisSettings: () => ({ mutate: patch, isPending: false }),
  useResolveProject: (enabled: boolean) => {
    probed.push(enabled);
    return { ...probe, refetch };
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  patch.mockReset();
  probed.length = 0;
  probe = { isError: false, isFetching: false };
});

describe("ResolveLinkCard", () => {
  it("saves another computer's address and the folders seen from there", async () => {
    const user = userEvent.setup();
    render(<ResolveLinkCard />);
    expect(screen.getByRole("radio", { name: "sur cet ordinateur" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "Dossier 1 vu par Resolve" })).toHaveValue(
      "/Volumes/cats",
    );

    await user.click(screen.getByRole("radio", { name: "sur un autre ordinateur" }));
    await user.type(
      screen.getByRole("textbox", { name: "Adresse de cet ordinateur" }),
      "mac-studio",
    );
    await user.clear(screen.getByRole("textbox", { name: "Dossier 1 vu par Resolve" }));
    await user.type(
      screen.getByRole("textbox", { name: "Dossier 1 vu par Resolve" }),
      "/Volumes/cats 2026",
    );
    await user.click(screen.getByRole("button", { name: "Ajouter une correspondance" }));
    await user.type(screen.getByRole("textbox", { name: "Dossier 2 sur cet ordinateur" }), "E:\\x");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(patch).toHaveBeenCalledWith(
      {
        resolve_host: "mac-studio",
        resolve_folders: [{ here: "D:\\cats 2026", there: "/Volumes/cats 2026" }], // half pair left out
      },
      expect.anything(),
    );
  });

  it("reads Resolve only when asked, and says what it found", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<ResolveLinkCard />);
    expect(probed.every((enabled) => !enabled)).toBe(true);
    await user.click(screen.getByRole("button", { name: "Tester la connexion" }));
    expect(probed.at(-1)).toBe(true);
    probe = {
      isError: false,
      isFetching: false,
      data: { product: "DaVinci Resolve Studio", version: "21.1", project: { name: "cats 2026" } },
    };
    rerender(<ResolveLinkCard />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Connecté : DaVinci Resolve Studio 21.1, projet « cats 2026 ».",
    );
  });
});
