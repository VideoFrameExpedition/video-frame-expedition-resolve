import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { resetBins } from "./bins";
import { BinsTree } from "./BinsTree";
import { timelineBin } from "./timelineFixtures";

const navigate = vi.fn();
let search: Record<string, string | undefined> = {};
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => search,
}));

const folder = (name: string, path: string, count: number, children: unknown[] = []) => ({
  name,
  path,
  count,
  total: count,
  children,
});
const TREE = [
  {
    root_id: "r1",
    label: "Sample IA",
    tree: folder("Sample IA", "", 6, [
      folder("apv", "apv", 3),
      folder("with data", "with data", 10, [folder("2020", "with data/2020", 1)]),
    ]),
  },
];
let folders: { data?: unknown; isError?: boolean; error?: unknown } = { data: TREE };
let timelines: { data?: unknown } = { data: [] };
vi.mock("@/api/queries", () => ({
  useFolders: () => folders,
  useTimelineBins: () => timelines,
}));

interface Update {
  to: string;
  search: (prev: Record<string, string>) => Record<string, string | undefined>;
}
const lastUpdate = (): Update => navigate.mock.calls.at(-1)?.[0] as Update;

beforeEach(() => {
  navigate.mockReset();
  search = {};
  folders = { data: TREE };
  timelines = { data: [] };
  resetBins();
});

describe("BinsTree", () => {
  it("lists each folder as a bin and opens the one clicked", async () => {
    const user = userEvent.setup();
    render(<BinsTree />);
    const bins = screen.getByRole("navigation", { name: "Dossiers" });
    expect(within(bins).getByRole("button", { name: "Toutes les vidéos" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(bins).queryByRole("button", { name: /^2020/ })).not.toBeInTheDocument(); // folded
    await user.click(within(bins).getByRole("button", { name: /^with data/ }));
    const update = navigate.mock.calls[0]?.[0] as {
      to: string;
      search: (prev: Record<string, string>) => Record<string, string | undefined>;
    };
    expect(update.to).toBe("/library");
    expect(update.search({ sort: "name" })).toEqual({
      sort: "name",
      root: "r1",
      folder: "with data",
    });
  });

  it("marks the open bin and folds a branch", async () => {
    const user = userEvent.setup();
    search = { root: "r1", folder: "with data/2020" };
    render(<BinsTree />);
    expect(screen.getByRole("button", { name: /^2020/ })).toHaveAttribute("aria-current", "page");
    await user.click(screen.getByRole("button", { name: "Replier with data" }));
    expect(screen.queryByRole("button", { name: /^2020/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Déplier with data" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("folds a deep bin until opened, and keeps it open while the app is open", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<BinsTree />);
    expect(screen.getByRole("button", { name: "Replier Sample IA" })).toHaveAttribute(
      "aria-expanded",
      "true",
    ); // a root's bin starts open
    expect(screen.queryByRole("button", { name: /^2020/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Déplier with data" }));
    expect(screen.getByRole("button", { name: /^2020/ })).toBeVisible();
    unmount(); // a video page, then back to the library
    render(<BinsTree />);
    expect(screen.getByRole("button", { name: /^2020/ })).toBeVisible();
  });

  it("opens the bins leading to the open one, and they stay open", async () => {
    const user = userEvent.setup();
    search = { root: "r1", folder: "with data/2020" }; // a link, or the page reloaded
    const { unmount } = render(<BinsTree />);
    expect(screen.getByRole("button", { name: /^2020/ })).toHaveAttribute("aria-current", "page");
    await user.click(screen.getByRole("button", { name: /^apv/ }));
    unmount();
    search = { root: "r1", folder: "apv" };
    render(<BinsTree />);
    expect(screen.getByRole("button", { name: /^apv/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: /^2020/ })).toBeVisible();
  });

  it("says why the bins are missing", () => {
    folders = { data: undefined, isError: true, error: new Error("Serveur injoignable") };
    render(<BinsTree />);
    expect(screen.getByText("Serveur injoignable")).toBeVisible();
  });

  it("lists the Resolve timelines only when there are some", () => {
    render(<BinsTree />);
    expect(screen.queryByRole("navigation", { name: "Timelines Resolve" })).not.toBeInTheDocument();
  });

  it("opens a Resolve timeline instead of a folder, and a folder instead of it", async () => {
    const user = userEvent.setup();
    timelines = { data: [timelineBin()] };
    render(<BinsTree />);
    const section = within(screen.getByRole("navigation", { name: "Timelines Resolve" }));
    const row = section.getByRole("button", { name: /^Montage/ });
    expect(row).toHaveAttribute("title", "cats 2026 › Timeline 1");
    expect(row).toHaveTextContent("95"); // its videos in the library
    await user.click(row);
    expect(lastUpdate().search({ root: "r1", folder: "apv", sort: "name" })).toEqual({
      sort: "name",
      timeline: "b1",
    });

    await user.click(screen.getByRole("button", { name: /^apv/ }));
    const next = lastUpdate().search({ timeline: "b1", sort: "name" });
    expect(next).toEqual({ sort: "name", root: "r1", folder: "apv" });
    expect(next.timeline).toBeUndefined();
  });

  it("marks the open timeline, and « Toutes les vidéos » leaves it", async () => {
    const user = userEvent.setup();
    timelines = { data: [timelineBin()] };
    search = { timeline: "b1" };
    render(<BinsTree />);
    expect(screen.getByRole("button", { name: /^Montage/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const all = screen.getByRole("button", { name: "Toutes les vidéos" });
    expect(all).not.toHaveAttribute("aria-current");
    await user.click(all);
    expect(lastUpdate().search({ timeline: "b1" }).timeline).toBeUndefined();
  });

  it("tells apart two timelines of the same name by their project", () => {
    timelines = {
      data: [
        timelineBin({ id: "b1", label: "Timeline 1" }),
        timelineBin({ id: "b2", label: "Timeline 1", project: { id: "p2", name: "Vacances" } }),
      ],
    };
    render(<BinsTree />);
    expect(screen.getByRole("button", { name: /^Timeline 1 · cats 2026/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /^Timeline 1 · Vacances/ })).toBeVisible();
  });

  it("says a folder of chosen files holds only what a timeline brought in", () => {
    folders = {
      data: [...TREE, { root_id: "r2", label: "hdr", kind: "files", tree: folder("hdr", "", 3) }],
    };
    render(<BinsTree />);
    const files = screen.getByRole("button", { name: /^hdr/ });
    expect(files).toHaveAccessibleDescription(
      "Fichiers ajoutés depuis une timeline (seules ces vidéos)",
    );
    expect(screen.getByRole("button", { name: /^Sample IA/ })).not.toHaveAccessibleDescription(
      /timeline/,
    );
  });
});
