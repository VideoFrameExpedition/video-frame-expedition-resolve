import { createContext, useContext } from "react";

import type { BenchModel } from "@/api/client";

import { hash } from "./benchFormat";

/**
 * The models as LM Studio keeps them, and their colour codes.
 *
 * A model's family is the folder LM Studio keeps it in (its publisher: « Qwen3-VL »,
 * « lmstudio-community »…), and inside it the folder of the model, whose files are its
 * quantizations (« qwen3-vl-4b-instruct@q6_k »). The number of parameters and the quantization
 * are ordered ramps (ColorBrewer « Oranges » and « YlGnBu »: each step lighter or darker than the
 * next, still told apart with a colour-vision deficiency); a family has its own colour, always
 * shown with its name.
 */

export interface Chip {
  background: string;
  ink: string; // the text on it (≥ 4.5:1)
}

const DARK_INK = "#0b0e14";
const LIGHT_INK = "#ffffff";

/** Billions of parameters up to each step; above the last, the last step. */
export const PARAMS_BOUNDS = [2, 5, 10, 20, 40] as const;
export const PARAMS_CHIPS: readonly Chip[] = [
  { background: "#feedde", ink: DARK_INK },
  { background: "#fdd0a2", ink: DARK_INK },
  { background: "#fdae6b", ink: DARK_INK },
  { background: "#fd8d3c", ink: DARK_INK },
  { background: "#e6550d", ink: DARK_INK },
  { background: "#a63603", ink: LIGHT_INK },
];

/** Bits per weight up to each step: 1–2, 3, 4, 5–6, 7–8, then 16 and more. */
export const QUANT_BOUNDS = [2, 3, 4, 6, 8] as const;
export const QUANT_CHIPS: readonly Chip[] = [
  { background: "#ffffcc", ink: DARK_INK },
  { background: "#c7e9b4", ink: DARK_INK },
  { background: "#7fcdbb", ink: DARK_INK },
  { background: "#41b6c4", ink: DARK_INK },
  { background: "#2470a8", ink: LIGHT_INK },
  { background: "#253494", ink: LIGHT_INK },
];

/** One colour per family (Tableau 10 and two more), seen on light and dark cards alike. */
export const FAMILY_COLORS = [
  "#4e79a7",
  "#f28e2b",
  "#e15759",
  "#76b7b2",
  "#59a14f",
  "#edc948",
  "#b07aa1",
  "#ff9da7",
  "#9c755f",
  "#499894",
  "#d37295",
  "#8cd17d",
] as const;

/** The step of ``value`` on a ramp of ``bounds``. */
export function stepOf(value: number, bounds: readonly number[]): number {
  const index = bounds.findIndex((bound) => value <= bound);
  return index === -1 ? bounds.length : index;
}

/** "4B", "4.6B", "26B-A4B" (every expert counted), "300M": in billions, or null. */
export function paramsBillions(params: string | null | undefined): number | null {
  const match = /^\s*(\d+(?:\.\d+)?)\s*([BM])/i.exec(params ?? "");
  if (!match?.[1] || !match[2]) return null;
  const value = Number(match[1]);
  return match[2].toUpperCase() === "M" ? value / 1000 : value;
}

/** "Q4_K_M", "IQ2_XXS", "Q8_K_XL", "TQ1_0", "MXFP4", "BF16": bits per weight, or null. */
export function quantBits(name: string | null | undefined): number | null {
  const text = (name ?? "").toUpperCase();
  const float = /^B?F(16|32)\b/.exec(text)?.[1];
  if (float) return Number(float);
  if (text.includes("FP4")) return 4;
  const bits = /^[A-Z]*?Q(\d+)/.exec(text)?.[1];
  return bits ? Number(bits) : null;
}

/** The step of a number of parameters, or null when LM Studio does not say it. */
export function paramsStep(params: string | null | undefined): number | null {
  const billions = paramsBillions(params);
  return billions === null ? null : stepOf(billions, PARAMS_BOUNDS);
}

/** The step of a quantization, or null when its name tells no bits. */
export function quantStep(name: string | null | undefined): number | null {
  const bits = quantBits(name);
  return bits === null ? null : stepOf(bits, QUANT_BOUNDS);
}

export function paramsChip(params: string | null | undefined): Chip | null {
  const step = paramsStep(params);
  return step === null ? null : (PARAMS_CHIPS[step] ?? null);
}

export function quantChip(name: string | null | undefined): Chip | null {
  const step = quantStep(name);
  return step === null ? null : (QUANT_CHIPS[step] ?? null);
}

/** What the filters of the page keep: families, steps of parameters, steps of quantization.
 * Within one kind, any of those chosen; between kinds, all of them; nothing chosen: all. */
export interface ModelFilters {
  families: readonly string[];
  params: readonly number[];
  quants: readonly number[];
}

export const NO_FILTERS: ModelFilters = { families: [], params: [], quants: [] };

export const filtering = (filters: ModelFilters): boolean =>
  filters.families.length + filters.params.length + filters.quants.length > 0;

/** Whether a model (of LM Studio, or measured in a run) passes the filters. */
export function passes(
  model: { publisher?: string | null; params?: string | null; quantization?: string | null },
  filters: ModelFilters,
): boolean {
  const among = (chosen: readonly number[], step: number | null): boolean =>
    chosen.length === 0 || (step !== null && chosen.includes(step));
  return (
    (filters.families.length === 0 || filters.families.includes(familyOf(model))) &&
    among(filters.params, paramsStep(model.params)) &&
    among(filters.quants, quantStep(model.quantization))
  );
}

const compareNames = (a: string, b: string): number =>
  a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });

/** A colour for each family, the same as long as the families are; two families share one
 * only beyond twelve. ``first`` (those of LM Studio now) get theirs before ``then`` (those
 * only the history still names). */
export function familyColors(
  first: Iterable<string>,
  then: Iterable<string> = [],
): Map<string, string> {
  const colors = new Map<string, string>();
  const taken = new Set<number>();
  const size = FAMILY_COLORS.length;
  const leading = [...new Set(first)].sort(compareNames);
  const trailing = [...new Set(then)].filter((name) => !leading.includes(name)).sort(compareNames);
  for (const name of [...leading, ...trailing]) {
    const first = hash(name.toLowerCase()) % size;
    let index = first;
    while (taken.has(index) && taken.size < size) {
      index = (index + 1) % size;
    }
    taken.add(index);
    colors.set(name, FAMILY_COLORS[index] ?? FAMILY_COLORS[first] ?? "#4e79a7");
  }
  return colors;
}

export const FamilyColorsContext = createContext<ReadonlyMap<string, string>>(new Map());

/** The colour of a family on this page (one it does not know yet keeps a stable one). */
export function useFamilyColor(family: string): string {
  const known = useContext(FamilyColorsContext).get(family);
  return known ?? FAMILY_COLORS[hash(family.toLowerCase()) % FAMILY_COLORS.length] ?? "#4e79a7";
}

/** The family of a model: the folder LM Studio keeps it in ("" when it does not say). */
export const familyOf = (model: { publisher?: string | null }): string =>
  model.publisher?.trim() ?? "";

/** The folder of a model inside its family: its key without the quantization nor the
 * publisher (« qwen3-vl-4b-instruct@q6_k » → « qwen3-vl-4b-instruct »). */
export function folderOf(model: { key: string }): string {
  const base = model.key.split("@")[0] ?? model.key;
  return base.slice(base.lastIndexOf("/") + 1);
}

export interface ModelFolder {
  name: string;
  models: BenchModel[]; // its quantizations, the lightest first
}

export interface ModelFamily {
  name: string;
  folders: ModelFolder[];
  count: number;
}

/** A place in the tree: every model, a family, or a folder of a family. */
export interface TreeSpot {
  family?: string;
  folder?: string;
}

const lighter = (a: BenchModel, b: BenchModel): number =>
  (quantBits(a.quantization) ?? 0) - (quantBits(b.quantization) ?? 0) ||
  (a.size_bytes ?? 0) - (b.size_bytes ?? 0) ||
  compareNames(a.key, b.key);

/** The models as LM Studio keeps them: families, then their folders, by name. */
export function modelTree(models: readonly BenchModel[]): ModelFamily[] {
  const families = new Map<string, Map<string, BenchModel[]>>();
  for (const model of models) {
    const folders = families.get(familyOf(model)) ?? new Map<string, BenchModel[]>();
    families.set(familyOf(model), folders);
    folders.set(folderOf(model), [...(folders.get(folderOf(model)) ?? []), model]);
  }
  return [...families.entries()]
    .sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : compareNames(a, b)))
    .map(([name, folders]) => ({
      name,
      folders: [...folders.entries()]
        .sort(([a], [b]) => compareNames(a, b))
        .map(([folder, list]) => ({ name: folder, models: [...list].sort(lighter) })),
      count: [...folders.values()].reduce((sum, list) => sum + list.length, 0),
    }));
}

/** The families and folders under a place of the tree. */
export function underSpot(tree: readonly ModelFamily[], spot: TreeSpot): ModelFamily[] {
  return tree
    .filter((family) => spot.family === undefined || family.name === spot.family)
    .map((family) => {
      const folders = family.folders.filter(
        (folder) => spot.folder === undefined || folder.name === spot.folder,
      );
      return {
        ...family,
        folders,
        count: folders.reduce((sum, folder) => sum + folder.models.length, 0),
      };
    })
    .filter((family) => family.count > 0);
}
