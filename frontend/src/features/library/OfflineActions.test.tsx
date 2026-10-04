import { render, renderHook, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { OfflineActions } from "./OfflineActions";
import { selectVideos, useSelectedVideos } from "./selection";

interface Callbacks<T> {
  onSuccess: (value: T) => void;
  onError: (error: unknown) => void;
}
const pick = vi.fn();
const relink = vi.fn();
const forget = vi.fn();
vi.mock("@/api/queries", () => ({
  usePickFolder: () => ({ mutate: pick, isPending: false }),
  useRelinkVideos: () => ({ mutate: relink, isPending: false }),
  useForgetVideos: () => ({ mutate: forget, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  pick.mockReset();
  relink.mockReset();
  forget.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.info).mockReset();
});

describe("OfflineActions", () => {
  it("relinks to the folder chosen in the Windows dialog, not when it is closed", async () => {
    const user = userEvent.setup();
    render(<OfflineActions ids={["a", "b"]} />);
    await user.click(screen.getByRole("button", { name: "Relier les 2…" }));
    const [, picked] = pick.mock.calls[0] as [undefined, Callbacks<{ path: string | null }>];
    picked.onSuccess({ path: null });
    expect(relink).not.toHaveBeenCalled();

    picked.onSuccess({ path: "E:\\Rushs déplacés" });
    expect(relink).toHaveBeenCalledWith(
      { video_ids: ["a", "b"], folder: "E:\\Rushs déplacés" },
      expect.anything(),
    );
    const [, started] = relink.mock.calls[0] as [unknown, Callbacks<unknown>];
    started.onSuccess({});
    expect(toast.success).toHaveBeenCalledWith(
      "Recherche des 2 vidéos dans « E:\\Rushs déplacés » (tâche en cours).",
    );
  });

  it("takes the videos out after a confirmation and says what was left", async () => {
    const user = userEvent.setup();
    const onForgotten = vi.fn();
    selectVideos(["a", "b", "c", "d"], true);
    render(<OfflineActions ids={["a", "b", "c"]} onForgotten={onForgotten} />);
    await user.click(screen.getByRole("button", { name: "Retirer les 3 de la bibliothèque" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(
      dialog.getByRole("heading", { name: "Retirer les 3 vidéos hors ligne de la bibliothèque ?" }),
    ).toBeInTheDocument();
    expect(forget).not.toHaveBeenCalled();

    await user.click(dialog.getByRole("button", { name: "Retirer les 3" }));
    expect(forget).toHaveBeenCalledWith(["a", "b", "c"], expect.anything());
    const [, done] = forget.mock.calls[0] as [
      string[],
      Callbacks<{ forgotten: number; left: number }>,
    ];
    done.onSuccess({ forgotten: 2, left: 1 });
    expect(toast.success).toHaveBeenCalledWith("2 vidéos retirées de la bibliothèque");
    expect(toast.info).toHaveBeenCalledWith(
      "1 vidéo laissée : son fichier est là, ou elle est en cours d'analyse.",
    );
    expect(onForgotten).toHaveBeenCalledOnce();
    const ticked = renderHook(() => useSelectedVideos()).result.current;
    expect([...ticked]).toEqual(["d"]); // the others left the library
  });
});
