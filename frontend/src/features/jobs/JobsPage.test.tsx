import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import type { Job, Schemas } from "@/api/client";
import type { JobListQuery } from "@/api/queries";

import { JobsPage } from "./JobsPage";

const cancel = vi.fn();
const retry = vi.fn();
let summary: Schemas["JobsSummaryOut"];
let lists: Record<string, Job[]>;
const asked: JobListQuery[] = [];

vi.mock("@/api/queries", () => ({
  useJobsSummary: () => ({ data: summary }),
  useJobList: (query: JobListQuery) => {
    asked.push(query);
    const key = query.status.join(",");
    return { data: (lists[key] ?? []).slice(0, query.limit), isPending: false };
  },
  useCancelJob: () => ({ mutate: cancel, isPending: false }),
  useRetryJob: () => ({ mutate: retry, isPending: false }),
}));
vi.mock("./StopAllButton", () => ({ StopAllButton: () => null }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a href="#target" className={className}>
      {children}
    </a>
  ),
}));

function job(id: string, status: Job["status"], extra: Partial<Job> = {}): Job {
  return {
    id,
    kind: "analyze_video",
    status,
    video_id: `video-${id}`,
    root_id: null,
    payload: {},
    priority: 100,
    progress: 0,
    message: null,
    error: null,
    attempts: 0,
    cancel_requested: false,
    created_at: "2026-09-28T13:00:00Z",
    started_at: null,
    finished_at: null,
    target: `${id}.mp4`,
    place: "CATS › hdr",
    ...extra,
  };
}

beforeEach(() => {
  cancel.mockReset();
  retry.mockReset();
  asked.length = 0;
  summary = {
    running: 1,
    queued: 12,
    finished: { succeeded: 5, partial: 1, failed: 2, cancelled: 3 },
    analysis_s: 300,
    eta_s: 2 * 3600 + 5 * 60,
    parallel: 2,
  };
  const queued = Array.from({ length: 12 }, (_, i) => job(`q${String(i + 1)}`, "queued"));
  lists = {
    running: [
      job("r1", "running", {
        progress: 0.4,
        message: "Image 58/84 décrite",
        started_at: new Date(Date.now() - 125_000).toISOString(),
      }),
    ],
    queued,
    "succeeded,partial,failed,cancelled": [
      job("f1", "failed", {
        error: "ffmpeg a échoué",
        started_at: "2026-09-28T12:00:00Z",
        finished_at: "2026-09-28T12:01:20Z",
      }),
      job("s1", "succeeded", { message: "Index de recherche" }),
    ],
    failed: [job("f1", "failed", { error: "ffmpeg a échoué" })],
  };
});

// Testing Library reads no-break spaces as spaces: the expected texts use plain ones.
describe("JobsPage", () => {
  it("sums up the jobs and estimates the time left", () => {
    render(<JobsPage />);
    const tiles = within(screen.getByRole("region", { name: "Résumé des tâches" }));
    expect(tiles.getByText("En attente")).toBeInTheDocument();
    expect(tiles.getByText("12")).toBeInTheDocument();
    expect(tiles.getByText("≈ 2 h 05 restantes")).toBeInTheDocument();
    expect(tiles.getByText("6")).toBeInTheDocument(); // finished today, partial included
    expect(tiles.getByText("dont 1 partielle")).toBeInTheDocument();
    expect(tiles.getByText("3 annulées")).toBeInTheDocument();
  });

  it("shows the running job first, with its step and how long it has run", () => {
    render(<JobsPage />);
    const running = within(screen.getByRole("region", { name: "En cours" }));
    expect(running.getByText("r1.mp4")).toBeInTheDocument();
    expect(running.getByText("Image 58/84 décrite")).toBeInTheDocument();
    expect(running.getByText("depuis 2 min")).toBeInTheDocument();
    expect(running.getByText("40 %")).toBeInTheDocument();
  });

  it("lists the queue in the worker's order, ten first", async () => {
    const user = userEvent.setup();
    render(<JobsPage />);
    expect(asked).toContainEqual({ status: ["queued"], order: "queue", limit: 10 });
    const queue = within(screen.getByRole("region", { name: "À venir · 12" }));
    expect(queue.getAllByRole("listitem")).toHaveLength(10);
    expect(queue.getByText("q1.mp4")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Afficher les 2 suivantes" }));
    expect(asked).toContainEqual({ status: ["queued"], order: "queue", limit: 500 });
    await user.click(queue.getByRole("button", { name: "Annuler « q1.mp4 »" }));
    expect(cancel).toHaveBeenCalledWith("q1", expect.anything());
  });

  it("filters the history and retries only what can be run again", async () => {
    const user = userEvent.setup();
    render(<JobsPage />);
    const history = within(screen.getByRole("region", { name: "Historique" }));
    expect(history.getByText("ffmpeg a échoué")).toBeInTheDocument();
    expect(history.getByText("1 min")).toBeInTheDocument(); // 80 s
    expect(history.getAllByRole("button", { name: "Relancer" })).toHaveLength(1); // not a success
    await user.click(history.getByRole("button", { name: "Relancer" }));
    expect(retry).toHaveBeenCalledWith("f1", expect.anything());

    await user.click(history.getByRole("button", { name: "Échecs" }));
    expect(history.getByRole("button", { name: "Échecs" })).toHaveAttribute("aria-pressed", "true");
    expect(asked).toContainEqual({ status: ["failed"], order: "finished", limit: 50 });
    expect(history.queryByText("s1.mp4")).not.toBeInTheDocument();
  });

  it("folds a run of tasks cancelled before they started into one row", async () => {
    const user = userEvent.setup();
    lists["succeeded,partial,failed,cancelled"] = [
      job("s1", "succeeded"),
      ...Array.from({ length: 4 }, (_, i) => job(`c${String(i)}`, "cancelled")),
      job("c9", "cancelled", { started_at: "2026-09-28T12:00:00Z" }), // it ran: shown alone
    ];
    render(<JobsPage />);
    const history = within(screen.getByRole("region", { name: "Historique" }));
    expect(history.getByText("4 tâches annulées avant de démarrer")).toBeInTheDocument();
    expect(history.queryByText("c0.mp4")).not.toBeInTheDocument();
    expect(history.getByText("c9.mp4")).toBeInTheDocument();
    await user.click(history.getByRole("button", { name: "Afficher" }));
    expect(history.getByText("c0.mp4")).toBeInTheDocument();
  });
});
