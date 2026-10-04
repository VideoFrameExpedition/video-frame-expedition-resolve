import { hasFilters, searchParams, validateSearchPage, withoutFilters } from "./searchState";

describe("validateSearchPage", () => {
  it("keeps the known filters and drops the rest", () => {
    expect(
      validateSearchPage({
        q: "chien",
        kind: "shot",
        weather: "sunny", // not a weather of the index
        light: "golden_hour",
        from: "2026-07-01",
        to: "hier",
        speech: "yes",
        rating: "4",
        usability: "150",
        favorite: "1",
        shot: "close_up",
        orientation: "vertical",
      }),
    ).toEqual({
      q: "chien",
      kind: "shot",
      light: "golden_hour",
      from: "2026-07-01",
      speech: "yes",
      rating: 4,
      favorite: true,
      shot: "close_up",
      orientation: "vertical",
    });
  });

  it("keeps a folder with its root only", () => {
    expect(validateSearchPage({ root: "r1", folder: "Sommets" })).toEqual({
      root: "r1",
      folder: "Sommets",
    });
    expect(validateSearchPage({ root: "r1" })).toEqual({ root: "r1", folder: "" });
    expect(validateSearchPage({ folder: "Sommets" })).toEqual({});
  });

  it("ignores blank text", () => {
    expect(validateSearchPage({ q: "  ", place: " ", device: 3 })).toEqual({});
  });
});

describe("searchParams", () => {
  it("asks nothing while there is nothing to search", () => {
    expect(searchParams({})).toBeNull();
  });

  it("turns the address into the API's filters", () => {
    expect(
      searchParams({
        q: "neige",
        weather: "snow",
        speech: "no",
        subject: "chien",
        root: "r1",
        folder: "",
        usability: 70,
      }),
    ).toMatchObject({
      q: "neige",
      weather: ["snow"],
      has_speech: false,
      subject: ["chien"],
      root_id: "r1",
      folder: "",
      min_usability: 70,
    });
    expect(searchParams({ light: "night" })).toMatchObject({ q: "", light_phase: ["night"] });
  });

  it("clears the filters and keeps the text", () => {
    expect(hasFilters({ q: "chat" })).toBe(false);
    expect(hasFilters({ q: "chat", favorite: true })).toBe(true);
    expect(withoutFilters({ q: "chat", favorite: true, place: "Hyères" })).toEqual({ q: "chat" });
    expect(withoutFilters({ place: "Hyères" })).toEqual({});
  });
});
