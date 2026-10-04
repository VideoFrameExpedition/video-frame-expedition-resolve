import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { VideoFilters } from "@/api/queries";
import type { LibrarySearch } from "@/app/router";

import { lastLibrarySearch } from "./lastSearch";
import { LibraryPage } from "./LibraryPage";
import { timelineBin } from "./timelineFixtures";

const navigate = vi.fn();
let search: LibrarySearch = {};
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));
vi.mock("@/app/router", () => ({
  LIGHTS: ["golden_hour", "day", "night"],
  libraryRoute: { useSearch: () => search },
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
const clips = (count: number) =>
  Array.from({ length: count }, (_, i) => ({ id: `v${i}`, filename: `clip${i}.mp4` }));
let folders: unknown;
let roots: unknown[] | undefined;
let timelines: unknown[] | undefined;
let videos: { data?: { items: unknown[]; total: number }; isPending: boolean };
const asked: VideoFilters[] = []; // what the page asked the videos of
vi.mock("@/api/queries", () => ({
  useRoots: () => ({ data: roots }),
  useFolders: () => ({ data: folders }),
  useTimelineBins: () => ({ data: timelines }),
  useVideos: (filters: VideoFilters) => {
    asked.push(filters);
    return videos;
  },
}));
vi.mock("./AddRootDialog", () => ({ AddRootDialog: () => <button>Ajouter un dossier</button> }));
vi.mock("./AddTimelineDialog", () => ({
  AddTimelineDialog: () => <button>Importer depuis Resolve</button>,
}));
vi.mock("./TimelineBar", () => ({
  TimelineBar: ({ bin }: { bin: { label: string } }) => <p>Barre de {bin.label}</p>,
}));
vi.mock("./VideoCard", () => ({
  VideoCard: ({ video }: { video: { filename: string } }) => <p>{video.filename}</p>,
}));

const lastAsked = (): VideoFilters | undefined => asked.at(-1);

beforeEach(() => {
  navigate.mockReset();
  search = {};
  folders = TREE;
  roots = undefined;
  timelines = [];
  videos = { data: { items: clips(3), total: 3 }, isPending: false };
  asked.length = 0;
});

describe("LibraryPage", () => {
  it("shows every video when the open bin is gone from the tree", () => {
    search = { root: "gone", folder: "", sort: "name" }; // its folder removed from the library
    render(<LibraryPage />);
    const update = navigate.mock.calls[0]?.[0] as {
      search: (prev: LibrarySearch) => LibrarySearch;
      replace: boolean;
    };
    expect(update.replace).toBe(true);
    expect(update.search(search)).toEqual({ sort: "name", root: undefined, folder: undefined });
  });

  it("keeps a bin of the tree, and waits for the tree before judging", () => {
    search = { root: "r1", folder: "with data/2020" };
    const { rerender } = render(<LibraryPage />);
    folders = undefined;
    search = { root: "r2", folder: "" }; // a root just added: the tree is still loading
    rerender(<LibraryPage />);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("says how many videos are shown, and shows more on demand", async () => {
    const user = userEvent.setup();
    search = { root: "r1", folder: "" };
    videos = { data: { items: clips(120), total: 180 }, isPending: false };
    const { rerender } = render(<LibraryPage />);
    expect(lastAsked()?.limit).toBe(120);
    expect(screen.getByText("120 vidéos affichées sur 180")).toBeVisible();
    expect(screen.getByText("Sélectionner les 120 vidéos affichées")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Charger plus" }));
    expect(lastAsked()?.limit).toBe(240);
    videos = { data: { items: clips(180), total: 180 }, isPending: false };
    rerender(<LibraryPage />);
    expect(screen.queryByRole("button", { name: "Charger plus" })).not.toBeInTheDocument();
    expect(screen.queryByText(/affichées sur/)).not.toBeInTheDocument();

    search = { root: "r1", folder: "apv" }; // another bin starts over
    rerender(<LibraryPage />);
    expect(lastAsked()).toMatchObject({ root_id: "r1", folder: "apv", limit: 120 });
  });

  it("remembers the bin and the filters for the way back from a video", () => {
    search = { root: "r1", folder: "apv", status: "ready", sort: "name" };
    render(<LibraryPage />);
    expect(lastLibrarySearch()).toEqual(search);
  });

  it("shows a Resolve timeline's videos in its order, under its bar", () => {
    timelines = [timelineBin()];
    search = { timeline: "b1" };
    render(<LibraryPage />);
    expect(lastAsked()).toMatchObject({
      timeline_bin_id: "b1",
      root_id: undefined,
      sort: undefined,
    });
    expect(screen.getByText("Timeline « Montage » · projet cats 2026")).toBeVisible();
    expect(screen.getByText("Barre de Montage")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Tri" })).toHaveTextContent("Ordre de la timeline");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows every video when the open timeline was removed, once the list is known", () => {
    search = { timeline: "gone", sort: "name" };
    timelines = undefined; // still loading: not judged yet
    const { rerender } = render(<LibraryPage />);
    expect(navigate).not.toHaveBeenCalled();
    timelines = [timelineBin()];
    rerender(<LibraryPage />);
    const update = navigate.mock.calls[0]?.[0] as {
      search: (prev: LibrarySearch) => LibrarySearch;
      replace: boolean;
    };
    expect(update.replace).toBe(true);
    expect(update.search(search)).toEqual({ sort: "name" });
  });

  it("offers a folder or a Resolve timeline to start the library", () => {
    roots = [];
    render(<LibraryPage />);
    expect(screen.getByText("Aucun dossier pour l'instant")).toBeVisible();
    expect(screen.getByRole("button", { name: "Ajouter un dossier" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Importer depuis Resolve" })).toBeVisible();
  });
});
