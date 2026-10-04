import type { Schemas } from "@/api/client";

import { binsLeadingTo, hasBin } from "./bins";

const folder = (
  name: string,
  path: string,
  children: Schemas["FolderOut"][] = [],
): Schemas["FolderOut"] => ({ name, path, count: 1, total: 1, children });
const TREE: Schemas["RootFoldersOut"][] = [
  {
    root_id: "r1",
    label: "Sample IA",
    kind: "folder",
    tree: folder("Sample IA", "", [
      folder("apv", "apv"),
      folder("with data", "with data", [folder("2020", "with data/2020")]),
    ]),
  },
];

describe("hasBin", () => {
  it("finds a root's own bin and its folders at any depth", () => {
    expect(hasBin(TREE, "r1", "")).toBe(true);
    expect(hasBin(TREE, "r1", undefined)).toBe(true);
    expect(hasBin(TREE, "r1", "apv")).toBe(true);
    expect(hasBin(TREE, "r1", "with data/2020")).toBe(true);
  });

  it("misses a removed root, a folder gone since, or a mere prefix", () => {
    expect(hasBin(TREE, "r2", "")).toBe(false);
    expect(hasBin(TREE, "r1", "old")).toBe(false);
    expect(hasBin(TREE, "r1", "with data/2021")).toBe(false);
    expect(hasBin(TREE, "r1", "with")).toBe(false);
    expect(hasBin([], "r1", "")).toBe(false);
  });
});

describe("binsLeadingTo", () => {
  it("lists the bins from the root's own down, without the folder's", () => {
    expect(binsLeadingTo("r1", "")).toEqual([]);
    expect(binsLeadingTo("r1", "apv")).toEqual(["r1/"]);
    expect(binsLeadingTo("r1", "with data/2020")).toEqual(["r1/", "r1/with data"]);
  });
});
