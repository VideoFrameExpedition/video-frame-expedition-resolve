import i18n, { applyPlatform } from "@/i18n";

import en from "./en.json";
import fr from "./fr.json";

type Tree = Record<string, unknown>;

/** Every leaf key of a translation tree, dotted (« addRoot.pathHelp »). */
function keys(tree: Tree, prefix = ""): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    typeof value === "object" && value !== null
      ? keys(value as Tree, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

describe("texts of the application computer's system", () => {
  it("only replace texts that exist, in both languages", () => {
    for (const tree of [fr, en] as Tree[]) {
      const base = keys(tree);
      const mac = keys((tree.platform as Tree).macos as Tree);
      expect(mac.length).toBeGreaterThan(0);
      for (const key of mac) {
        expect(base).toContain(key);
      }
    }
  });

  it("keep the Windows texts until a Mac is said, then name its paths and shortcuts", async () => {
    await i18n.changeLanguage("fr");
    expect(i18n.t("connections.resolveTools.note")).toContain("Ctrl+S");
    applyPlatform("linux");
    expect(i18n.t("addRoot.pathPlaceholder")).toBe("D:\\Vidéos\\Tournage");
    applyPlatform("macos");
    expect(i18n.t("connections.resolveTools.note")).toContain("Cmd+S");
    expect(i18n.t("addRoot.pathPlaceholder")).toBe("/Volumes/Rushs/Tournage");
    await i18n.changeLanguage("en");
    expect(i18n.t("connections.resolveLink.therePlaceholder")).toBe("D:\\cats 2026");
    await i18n.changeLanguage("fr");
  });
});
