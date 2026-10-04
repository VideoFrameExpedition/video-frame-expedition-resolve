import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { ApiError } from "@/api/client";

import { SKIPPED, timelineBin } from "./timelineFixtures";
import { TimelineBar } from "./TimelineBar";

interface Callbacks {
  onSuccess?: () => void;
  onError?: (error: unknown) => void;
}
const sync = vi.fn();
const update = vi.fn();
const remove = vi.fn();
const ITEMS = [
  {
    position: 0,
    path: "D:\\cats 2026\\hdr\\a.mp4",
    state: "in_library",
    note: null,
    video_id: "v1",
  },
  { position: 1, path: "D:\\cats 2026\\hdr\\b.mp4", state: "missing", note: null, video_id: null },
  {
    position: 2,
    path: "C:\\Users\\me\\OneDrive\\c.mp4",
    state: "error",
    note: "fichier OneDrive non présent sur ce PC",
    video_id: null,
  },
];
vi.mock("@/api/queries", () => ({
  useSyncTimeline: () => ({ mutate: sync, isPending: false }),
  useUpdateTimelineBin: () => ({ mutate: update, isPending: false }),
  useDeleteTimelineBin: () => ({ mutateAsync: remove, isPending: false }),
  useTimelineItems: () => ({ data: ITEMS, isPending: false, isError: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const STATES = {
  in_library: 95,
  adding: 0,
  not_processed: 0,
  removed: 0,
  missing: 1,
  outside: 0,
  error: 1,
};

beforeEach(() => {
  sync.mockReset();
  update.mockReset();
  remove.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("TimelineBar", () => {
  it("says when the timeline was read in Resolve and where its files stand", () => {
    render(
      <TimelineBar
        bin={timelineBin({ states: STATES, report: { errors: 0, skipped: SKIPPED } })}
      />,
    );
    const bar = within(screen.getByRole("region", { name: "Montage" }));
    expect(bar.getByText(/^Lue dans Resolve le /)).toBeVisible();
    expect(
      bar.getByText("95 vidéos dans la bibliothèque · 1 introuvable · 1 en erreur"),
    ).toBeVisible();
    expect(
      bar.getByText("3 clips composés ou multicam : leurs vidéos ne sont pas lues"),
    ).toBeVisible();
  });

  it("lists the files calling for a look, with what the update found", async () => {
    const user = userEvent.setup();
    render(<TimelineBar bin={timelineBin({ states: STATES })} />);
    const details = screen.getByRole("button", { name: "Détails" });
    expect(details).toHaveAttribute("aria-expanded", "false");
    await user.click(details);
    expect(details).toHaveAttribute("aria-expanded", "true");
    const list = within(screen.getByRole("list"));
    expect(list.getAllByRole("listitem")).toHaveLength(2); // not the one in the library
    expect(list.getByText("D:\\cats 2026\\hdr\\b.mp4").parentElement).toHaveTextContent(
      "introuvable",
    );
    expect(list.getByText("fichier OneDrive non présent sur ce PC")).toBeVisible();
  });

  it("has no details to show when every file is in the library", () => {
    render(<TimelineBar bin={timelineBin()} />);
    expect(screen.getByText("95 vidéos dans la bibliothèque")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Détails" })).not.toBeInTheDocument();
  });

  it("follows an update as it adds the files, and allows no second one meanwhile", () => {
    const job = { id: "j1", status: "running" as const, progress: 0.1225, message: null };
    render(<TimelineBar bin={timelineBin({ sync_job: job, states: { ...STATES, adding: 86 } })} />);
    expect(screen.getByText(/ajout en cours \(12\/98\)/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Mettre à jour depuis Resolve" })).toBeDisabled();
  });

  it("updates from Resolve, and says why it could not", async () => {
    const user = userEvent.setup();
    render(<TimelineBar bin={timelineBin()} />);
    const button = screen.getByRole("button", { name: "Mettre à jour depuis Resolve" });

    sync.mockImplementation((_id: string, options?: Callbacks) => {
      options?.onSuccess?.();
    });
    await user.click(button);
    expect(sync.mock.calls[0]?.[0]).toBe("b1");
    expect(toast.success).toHaveBeenCalledWith(
      "Timeline relue dans Resolve : la mise à jour est en cours.",
    );

    const detail =
      "Ouvrez le projet « cats 2026 » dans DaVinci Resolve pour mettre cette timeline à jour.";
    sync.mockImplementation((_id: string, options?: Callbacks) => {
      options?.onError?.(new ApiError(409, "conflict", detail));
    });
    await user.click(button);
    expect(toast.error).toHaveBeenCalledWith(detail);
  });

  it("removes the timeline after saying that nothing else goes", async () => {
    const user = userEvent.setup();
    remove.mockResolvedValue(undefined);
    render(<TimelineBar bin={timelineBin()} />);
    await user.click(screen.getByRole("button", { name: "Retirer de la bibliothèque" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(
      dialog.getByRole("heading", { name: "Retirer « Montage » de la bibliothèque ?" }),
    ).toBeVisible();
    expect(
      dialog.getByText(
        "La timeline est retirée de la bibliothèque. Rien n'est modifié dans Resolve ; aucune " +
          "vidéo ni analyse n'est supprimée.",
      ),
    ).toBeVisible();
    expect(remove).not.toHaveBeenCalled(); // nothing before the confirmation

    await user.click(dialog.getByRole("button", { name: "Retirer de la bibliothèque" }));
    expect(remove).toHaveBeenCalledWith("b1");
    expect(toast.success).toHaveBeenCalledWith("Timeline « Montage » retirée de la bibliothèque.");
  });

  it("turns the automatic analysis off", async () => {
    const user = userEvent.setup();
    update.mockImplementation((_vars: unknown, options?: Callbacks) => {
      options?.onSuccess?.();
    });
    render(<TimelineBar bin={timelineBin()} />);
    const toggle = screen.getByRole("switch", { name: "Analyser automatiquement" });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    expect(update.mock.calls[0]?.[0]).toEqual({ binId: "b1", body: { auto_analyze: false } });
    expect(toast.success).toHaveBeenCalledWith("Analyse automatique désactivée.");
  });
});
