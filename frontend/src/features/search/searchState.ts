import type { Schemas, SearchHit } from "@/api/client";
import type { SearchParams } from "@/api/queries";

import type { Ranges } from "./Highlighted";

/** The search page's state lives in the address: shareable, and kept by « Back ». */
export const SEARCH_KINDS = ["video", "chapter", "shot", "keyframe", "transcript"] as const;
export type SearchKind = (typeof SEARCH_KINDS)[number];
export const WEATHERS = [
  "clear",
  "partly_cloudy",
  "overcast",
  "fog",
  "drizzle",
  "rain",
  "snow",
  "thunderstorm",
] as const;
export type Weather = (typeof WEATHERS)[number];
export const PHASES = [
  "golden_hour",
  "blue_hour",
  "day",
  "nautical_twilight",
  "astronomical_twilight",
  "night",
] as const;
export type Phase = (typeof PHASES)[number];
export const SHOT_TYPES = [
  "extreme_wide",
  "wide",
  "medium",
  "close_up",
  "extreme_close_up",
  "macro",
] as const;
export type ShotType = (typeof SHOT_TYPES)[number];
export const ORIENTATIONS = ["horizontal", "vertical", "square"] as const;
export type Orientation = (typeof ORIENTATIONS)[number];
export const USABILITY_STEPS = [50, 70, 85] as const;

export interface SearchPageSearch {
  q?: string;
  kind?: SearchKind;
  from?: string; // YYYY-MM-DD
  to?: string;
  place?: string;
  weather?: Weather;
  light?: Phase;
  device?: string;
  orientation?: Orientation;
  speech?: "yes" | "no";
  subject?: string;
  shot?: ShotType;
  rating?: number;
  favorite?: true;
  root?: string; // a folder: its root, and a path in it ("" for the root itself)
  folder?: string;
  usability?: number;
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;

function oneOf<T extends string>(values: readonly T[], value: unknown): T | undefined {
  return typeof value === "string" && (values as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.slice(0, 200) : undefined;
}

function whole(value: unknown, min: number, max: number): number | undefined {
  const number = Number(value);
  return value !== undefined &&
    value !== "" &&
    Number.isInteger(number) &&
    number >= min &&
    number <= max
    ? number
    : undefined;
}

/** The search and its filters from the URL: unknown or malformed values are dropped. */
export function validateSearchPage(search: Record<string, unknown>): SearchPageSearch {
  const result: SearchPageSearch = {};
  const q = typeof search.q === "string" && search.q.trim() ? search.q.slice(0, 500) : undefined;
  const values: SearchPageSearch = {
    q,
    kind: oneOf(SEARCH_KINDS, search.kind),
    from: typeof search.from === "string" && DAY.test(search.from) ? search.from : undefined,
    to: typeof search.to === "string" && DAY.test(search.to) ? search.to : undefined,
    place: text(search.place),
    weather: oneOf(WEATHERS, search.weather),
    light: oneOf(PHASES, search.light),
    device: text(search.device),
    orientation: oneOf(ORIENTATIONS, search.orientation),
    speech: oneOf(["yes", "no"] as const, search.speech),
    subject: text(search.subject),
    shot: oneOf(SHOT_TYPES, search.shot),
    rating: whole(search.rating, 1, 5),
    favorite:
      search.favorite === true || search.favorite === "1" || search.favorite === 1
        ? true
        : undefined,
    usability: whole(search.usability, 0, 100),
  };
  if (typeof search.root === "string" && search.root) {
    values.root = search.root;
    values.folder = typeof search.folder === "string" ? search.folder : "";
  }
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) {
      (result as Record<string, unknown>)[key] = value;
    }
  }
  return result;
}

/** Filters chosen (the text left aside). */
export function hasFilters(search: SearchPageSearch): boolean {
  return Object.entries(search).some(([key, value]) => key !== "q" && value !== undefined);
}

/** What the page asks the API: null while there is nothing to search. */
export function searchParams(search: SearchPageSearch): SearchParams | null {
  if (!search.q && !hasFilters(search)) {
    return null;
  }
  return {
    q: search.q ?? "",
    kind: search.kind ? [search.kind] : undefined,
    date_from: search.from,
    date_to: search.to,
    place: search.place,
    weather: search.weather ? [search.weather] : undefined,
    light_phase: search.light ? [search.light] : undefined,
    device: search.device,
    orientation: search.orientation,
    has_speech: search.speech === undefined ? undefined : search.speech === "yes",
    subject: search.subject ? [search.subject] : undefined,
    shot_type: search.shot ? [search.shot] : undefined,
    min_rating: search.rating,
    favorite: search.favorite,
    root_id: search.root,
    folder: search.root ? search.folder : undefined,
    min_usability: search.usability,
  };
}

/** The filters cleared, the text kept. */
export function withoutFilters(search: SearchPageSearch): SearchPageSearch {
  return search.q ? { q: search.q } : {};
}

export type RootFolders = Schemas["RootFoldersOut"];
type FolderNode = RootFolders["tree"];

export interface Option {
  value: string;
  label: string;
}

/** Every folder of every root, as « Root › sub › sub » (a root alone is its own folder). */
export function folderOptions(roots: readonly RootFolders[]): Option[] {
  const out: Option[] = [];
  const visit = (rootId: string, node: FolderNode, trail: string[]): void => {
    const names = [...trail, node.name];
    out.push({ value: `${rootId}:${node.path}`, label: names.join(" › ") });
    for (const child of node.children) {
      visit(rootId, child, names);
    }
  };
  for (const root of roots) {
    visit(root.root_id, root.tree, []);
  }
  return out;
}

/** A passage as the client receives it (its highlight pairs as plain arrays). */
export type Hit = Omit<SearchHit, "highlights"> & { highlights: Ranges };

export interface VideoGroup {
  videoId: string;
  name: string; // the title when there is one, else the file name
  filename: string;
  hits: Hit[];
}

/** Hits grouped by video, in the order of each video's best hit. */
export function groupByVideo(hits: readonly Hit[]): VideoGroup[] {
  const groups = new Map<string, VideoGroup>();
  for (const hit of hits) {
    const group = groups.get(hit.video_id);
    if (group) {
      group.hits.push(hit);
    } else {
      groups.set(hit.video_id, {
        videoId: hit.video_id,
        name: hit.title ?? hit.filename,
        filename: hit.filename,
        hits: [hit],
      });
    }
  }
  return [...groups.values()];
}
