import { validateLibrarySearch } from "./router";

// The pages are not needed to read the URL.
vi.mock("./AppShell", () => ({ AppShell: () => null }));
vi.mock("@/features/jobs/JobsPage", () => ({ JobsPage: () => null }));
vi.mock("@/features/library/LibraryPage", () => ({ LibraryPage: () => null }));
vi.mock("@/features/search/SearchPage", () => ({ SearchPage: () => null }));
vi.mock("@/features/ask/AskPage", () => ({ AskPage: () => null }));
vi.mock("@/features/system/SystemPage", () => ({ SystemPage: () => null }));
vi.mock("@/features/video/VideoPage", () => ({ VideoPage: () => null }));

describe("validateLibrarySearch", () => {
  it("keeps a bin given as text: a root, and a folder of it", () => {
    expect(validateLibrarySearch({ root: "r1", folder: "with data/2020" })).toEqual({
      root: "r1",
      folder: "with data/2020",
    });
    expect(validateLibrarySearch({ root: "r1" })).toEqual({ root: "r1", folder: "" }); // its own bin
    expect(validateLibrarySearch({ root: "r1", folder: 2020 })).toEqual({ root: "r1", folder: "" });
  });

  it("drops a bin that is not text, and a folder without its root", () => {
    expect(validateLibrarySearch({ root: 42, folder: "apv" })).toEqual({});
    expect(validateLibrarySearch({ root: "", folder: "apv" })).toEqual({});
    expect(validateLibrarySearch({ folder: "apv" })).toEqual({});
    expect(validateLibrarySearch({ root: ["r1"] })).toEqual({});
  });

  it("keeps a Resolve timeline as the bin, never with a root", () => {
    expect(validateLibrarySearch({ timeline: "b1", sort: "name" })).toEqual({
      timeline: "b1",
      sort: "name",
    });
    expect(validateLibrarySearch({ timeline: "b1", root: "r1", folder: "apv" })).toEqual({
      timeline: "b1",
    });
    expect(validateLibrarySearch({ timeline: "", root: "r1" })).toEqual({ root: "r1", folder: "" });
    expect(validateLibrarySearch({ timeline: 7 })).toEqual({});
  });

  it("keeps the known filters next to the bin", () => {
    expect(
      validateLibrarySearch({ root: "r1", folder: "apv", sort: "name", status: "lost", q: " " }),
    ).toEqual({ root: "r1", folder: "apv", sort: "name" });
  });
});

// The router scrolls back to the top on every navigation, unless told otherwise.
const pages = import.meta.glob<string>("../features/**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("a page that rewrites its own address", () => {
  it("says each time whether it keeps the scroll", () => {
    const rewrites = Object.entries(pages).filter(([, source]) => source.includes("replace: true"));
    expect(rewrites.length).toBeGreaterThan(3);
    for (const [path, source] of rewrites) {
      expect(source.split("resetScroll: ").length, path).toBe(source.split("replace: true").length);
    }
  });

  it("keeps it on a video's tabs, where the user has already scrolled down", () => {
    expect(pages["../features/video/VideoPage.tsx"]).toContain("resetScroll: false");
  });
});
