import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import type { Root } from "@/api/client";

import { RootsPanel } from "./RootsPanel";

interface Callbacks {
  onSuccess?: () => void;
  onError?: (error: unknown) => void;
}
const patch = vi.fn();
vi.mock("@/api/queries", () => ({
  usePatchRoot: () => ({ mutate: patch, isPending: false }),
  useRemoveRoot: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScanRoot: () => ({ mutate: vi.fn(), isPending: false }),
  useAnalyzeRoot: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const root = (autoAnalyze: boolean): Root =>
  ({
    id: "r1",
    label: "Sample IA",
    path: "D:\\Sample IA",
    video_count: 6,
    ready_count: 2,
    offline_count: 0,
    auto_analyze: autoAnalyze,
    last_scan_at: null,
    kind: "folder",
    files_count: null,
  }) as unknown as Root;

beforeEach(() => {
  patch.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("RootsPanel", () => {
  it("turns the automatic update on from the folder's menu", async () => {
    const user = userEvent.setup();
    patch.mockImplementation((_vars: unknown, options?: Callbacks) => {
      options?.onSuccess?.();
    });
    render(<RootsPanel roots={[root(false)]} />);
    await user.click(screen.getByRole("button", { name: "Sample IA" }));
    const item = screen.getByRole("menuitemcheckbox", { name: "Mise à jour automatique" });
    expect(item).toHaveAttribute("aria-checked", "false");
    await user.click(item);
    expect(patch.mock.calls[0]?.[0]).toEqual({ rootId: "r1", body: { auto_analyze: true } });
    expect(toast.success).toHaveBeenCalledWith(
      "Mise à jour automatique activée : le dossier est surveillé toutes les 2 minutes et les " +
        "nouvelles vidéos sont analysées.",
    );
  });

  it("says why the automatic update could not be turned off", async () => {
    const user = userEvent.setup();
    patch.mockImplementation((_vars: unknown, options?: Callbacks) => {
      options?.onError?.(new Error("Dossier introuvable"));
    });
    render(<RootsPanel roots={[root(true)]} />);
    expect(screen.getByText("Mise à jour automatique")).toBeVisible(); // the card's badge
    await user.click(screen.getByRole("button", { name: "Sample IA" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Mise à jour automatique" }));
    expect(patch.mock.calls[0]?.[0]).toEqual({ rootId: "r1", body: { auto_analyze: false } });
    expect(toast.error).toHaveBeenCalledWith("Dossier introuvable");
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows a folder of chosen files with how many a timeline brought in", () => {
    const files = {
      ...root(false),
      id: "r2",
      kind: "files",
      files_count: 12,
      label: "hdr",
    } as Root;
    render(<RootsPanel roots={[root(false), files]} />);
    expect(screen.getByText("12 fichiers choisis")).toHaveAttribute(
      "title",
      "Fichiers ajoutés depuis une timeline (seules ces vidéos)",
    );
    expect(screen.getAllByText(/fichiers? choisis?/)).toHaveLength(1); // not on a plain folder
  });
});
