import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { StopAllButton } from "./StopAllButton";

let active: { id: string }[] = [];
const mutate = vi.fn();
vi.mock("@/api/queries", () => ({
  ACTIVE_JOBS_LIMIT: 500,
  useActiveJobs: () => ({ data: active }),
  useCancelAllJobs: () => ({ mutate, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  mutate.mockReset();
  vi.mocked(toast.success).mockReset();
});

describe("StopAllButton", () => {
  it("is not shown while nothing runs", () => {
    active = [];
    render(<StopAllButton withCount />);
    expect(screen.queryByRole("button", { name: "Tout arrêter" })).not.toBeInTheDocument();
  });

  it("says how many analyses are active and stops them after a confirmation", async () => {
    const user = userEvent.setup();
    active = Array.from({ length: 112 }, (_, i) => ({ id: String(i) }));
    render(<StopAllButton withCount />);
    expect(screen.getByText("112 analyses en cours ou en attente")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Tout arrêter" }));
    expect(screen.getByRole("heading", { name: "Arrêter les 112 analyses ?" })).toBeInTheDocument();
    expect(screen.getByText(/Ce qui est déjà analysé est conservé/)).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled(); // nothing stops before the confirmation

    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByRole("button", { name: "Tout arrêter" }));
    expect(mutate).toHaveBeenCalledOnce();
    const [, options] = mutate.mock.calls[0] as [
      undefined,
      { onSuccess: (done: { cancelled: number; stopping: number }) => void },
    ];
    options.onSuccess({ cancelled: 110, stopping: 2 });
    expect(toast.success).toHaveBeenCalledWith("112 tâches arrêtées");
  });

  it("counts no further than the limit", () => {
    active = Array.from({ length: 500 }, (_, i) => ({ id: String(i) }));
    render(<StopAllButton withCount />);
    expect(screen.getByText("500+ analyses en cours ou en attente")).toBeInTheDocument();
  });
});
