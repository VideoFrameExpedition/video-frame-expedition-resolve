import type { Schemas } from "@/api/client";

/**
 * The library's folders as bins: a bin is a root, and a folder of it ("" for the
 * root itself). Which bins show their sub-folders is kept while the app is open.
 */

type RootFolders = Schemas["RootFoldersOut"];

/** Whether the bin is in the tree (a removed root, or a folder gone since, is not). */
export function hasBin(
  folders: readonly RootFolders[],
  rootId: string,
  folder: string | undefined,
): boolean {
  const path = folder ?? "";
  let node = folders.find((item) => item.root_id === rootId)?.tree;
  while (node && node.path !== path) {
    node = node.children.find((child) => child.path === path || path.startsWith(`${child.path}/`));
  }
  return node !== undefined;
}

export function binKey(rootId: string, path: string): string {
  return `${rootId}/${path}`;
}

/** The bins leading to a folder, from the root's own bin down (the folder's bin excluded). */
export function binsLeadingTo(rootId: string, folder: string): string[] {
  const parts = folder.split("/").filter(Boolean);
  return parts.map((_, depth) => binKey(rootId, parts.slice(0, depth).join("/")));
}

// Bins opened or folded by hand.
const choices = new Map<string, boolean>();

/** As chosen by hand, else ``byDefault`` (a root's bin open, deeper bins folded, as in Resolve). */
export function isBinOpen(key: string, byDefault: boolean): boolean {
  return choices.get(key) ?? byDefault;
}

export function setBinOpen(key: string, open: boolean): void {
  choices.set(key, open);
}

/** The bins shown open by default stay open, unless folded by hand. */
export function keepBinsOpen(keys: Iterable<string>): void {
  for (const key of keys) {
    if (!choices.has(key)) choices.set(key, true);
  }
}

/** Forget the bins opened or folded by hand (tests). */
export function resetBins(): void {
  choices.clear();
}
