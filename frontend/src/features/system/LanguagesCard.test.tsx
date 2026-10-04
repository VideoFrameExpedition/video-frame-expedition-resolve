import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { LanguagesCard } from "./LanguagesCard";

interface Callbacks {
  onSuccess?: (result: { queued: number }) => void;
  onError?: (error: unknown) => void;
}
const patch = vi.fn();
const translate = vi.fn();
let settings: { language: string } | undefined;
vi.mock("@/api/queries", () => ({
  useAnalysisSettings: () => ({ data: settings }),
  usePatchAnalysisSettings: () => ({ mutate: patch, isPending: false }),
  useTranslateLibrary: () => ({ mutate: translate, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  patch.mockReset();
  translate.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.info).mockReset();
  settings = { language: "fr" };
});

describe("LanguagesCard", () => {
  it("shows the language the analyses are written in", () => {
    render(<LanguagesCard />);
    expect(screen.getByRole("combobox", { name: "Langue de rédaction" })).toHaveTextContent(
      "Français",
    );
    expect(screen.getByText(/Changer de langue ne refait rien/)).toBeVisible();
  });

  it("translates the analyses already made", async () => {
    translate.mockImplementation((_body: unknown, options?: Callbacks) => {
      options?.onSuccess?.({ queued: 12 });
    });
    const user = userEvent.setup();
    render(<LanguagesCard />);
    await user.click(screen.getByRole("button", { name: "Traduire les analyses existantes" }));
    expect(translate).toHaveBeenCalledOnce();
    expect(toast.success).toHaveBeenCalledWith("Traduction demandée pour 12 vidéos (page Tâches).");
  });

  it("says when every analysis is translated already", async () => {
    translate.mockImplementation((_body: unknown, options?: Callbacks) => {
      options?.onSuccess?.({ queued: 0 });
    });
    const user = userEvent.setup();
    render(<LanguagesCard />);
    await user.click(screen.getByRole("button", { name: "Traduire les analyses existantes" }));
    expect(toast.info).toHaveBeenCalledWith("Toutes les analyses sont déjà traduites.");
  });

  it("waits for the settings", () => {
    settings = undefined;
    render(<LanguagesCard />);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
