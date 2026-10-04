import { lastLibrarySearch, rememberLibrarySearch } from "./lastSearch";

describe("lastLibrarySearch", () => {
  it("gives back the library's last bin and filters", () => {
    expect(lastLibrarySearch()).toEqual({}); // the whole library at first
    rememberLibrarySearch({ root: "r1", folder: "with data", status: "ready", sort: "name" });
    expect(lastLibrarySearch()).toEqual({
      root: "r1",
      folder: "with data",
      status: "ready",
      sort: "name",
    });
    rememberLibrarySearch({});
    expect(lastLibrarySearch()).toEqual({});
  });
});
