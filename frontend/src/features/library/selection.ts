import { useSyncExternalStore } from "react";

/**
 * What the library's "Analyse" panel acts on: the videos ticked on the cards, and the stages
 * ticked in the side panel. Both outlive a visit to a video page; the stages left out are also
 * remembered in this browser, so a stage added by a new version starts ticked.
 */

type Listener = () => void;

function store<T>(initial: T) {
  let value = initial;
  const listeners = new Set<Listener>();
  return {
    get: (): T => value,
    set: (next: T): void => {
      value = next;
      for (const listener of listeners) listener();
    },
    subscribe: (listener: Listener): (() => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

const SKIPPED_KEY = "vfe.analyze.skippedStages";

function readSkipped(): ReadonlySet<string> {
  try {
    const raw = window.localStorage.getItem(SKIPPED_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(
      Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [],
    );
  } catch {
    return new Set();
  }
}

const videos = store<ReadonlySet<string>>(new Set());
const skipped = store<ReadonlySet<string>>(readSkipped());
const building = store(false);

/** Whether « Create a timeline » (the ticked videos, for Resolve) is open: another dialog can
 * hand over to it. */
export function useTimelineBuildOpen(): boolean {
  return useSyncExternalStore(building.subscribe, building.get);
}

export function setTimelineBuildOpen(open: boolean): void {
  building.set(open);
}

export function useSelectedVideos(): ReadonlySet<string> {
  return useSyncExternalStore(videos.subscribe, videos.get);
}

/** Tick (``on``) or untick the given videos. */
export function selectVideos(ids: readonly string[], on: boolean): void {
  const next = new Set(videos.get());
  for (const id of ids) {
    if (on) next.add(id);
    else next.delete(id);
  }
  videos.set(next);
}

/** The cards from ``anchor`` (the last one ticked by hand) to ``target``, in display order;
 * only ``target`` when the anchor is not shown any more. */
export function rangeOf(ids: readonly string[], anchor: string | null, target: string): string[] {
  const from = anchor === null ? -1 : ids.indexOf(anchor);
  const to = ids.indexOf(target);
  if (from < 0 || to < 0) return [target];
  return ids.slice(Math.min(from, to), Math.max(from, to) + 1);
}

export function clearVideos(): void {
  videos.set(new Set());
}

/** Stages left out of the next analysis (every other stage is ticked). */
export function useSkippedStages(): ReadonlySet<string> {
  return useSyncExternalStore(skipped.subscribe, skipped.get);
}

/** Tick (``on``) or untick the given stages. */
export function selectStages(names: readonly string[], on: boolean): void {
  const next = new Set(skipped.get());
  for (const name of names) {
    if (on) next.delete(name);
    else next.add(name);
  }
  skipped.set(next);
  try {
    window.localStorage.setItem(SKIPPED_KEY, JSON.stringify([...next]));
  } catch {
    // private window or blocked storage: the choice lasts until the page is reloaded
  }
}

/** Tick every stage, including names remembered from an earlier version. */
export function selectAllStages(): void {
  skipped.set(new Set());
  try {
    window.localStorage.removeItem(SKIPPED_KEY);
  } catch {
    // nothing stored
  }
}

/** Test helper: back to nothing selected and every stage ticked. */
export function resetSelection(): void {
  videos.set(new Set());
  skipped.set(new Set());
  building.set(false);
  try {
    window.localStorage.removeItem(SKIPPED_KEY);
  } catch {
    // nothing stored
  }
}
