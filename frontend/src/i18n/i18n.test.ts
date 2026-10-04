import en from "./en.json";
import fr from "./fr.json";

type Tree = Record<string, unknown>;

/** Every leaf key of a translation tree, dotted (« synthesis.role.avoid »). */
function keys(tree: Tree, prefix = ""): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    typeof value === "object" && value !== null
      ? keys(value as Tree, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

describe("translations", () => {
  it("have the same keys in French and English", () => {
    expect(keys(en).sort()).toEqual(keys(fr).sort());
  });

  it("cover the synthesis in both languages", () => {
    const french = keys(fr);
    for (const key of [
      "synthesis.title",
      "synthesis.regenerate",
      "synthesis.stale",
      "synthesis.footnote",
      "synthesis.chapters",
      "synthesis.highlights",
      "synthesis.note.jCut",
      "synthesis.note.lCut",
      "synthesis.usability.label",
      "synthesis.role.establishing",
      "synthesis.role.b_roll",
      "synthesis.role.avoid",
      "timeline.tracks.chapters",
      "timeline.tracks.highlights",
      "stage.synthesis",
    ]) {
      expect(french).toContain(key);
    }
  });
});
