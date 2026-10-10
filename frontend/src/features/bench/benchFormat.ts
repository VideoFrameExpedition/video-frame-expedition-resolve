import type { BenchModelRun, BenchRun, BenchRunSummary } from "@/api/client";

export const LETTERS = "ABCDEFGHIJKL";
export const MARKS = [0, 1, 2, 3] as const;
export type Mark = (typeof MARKS)[number];
/** What the page shows instead of one run: the latest result of every model ever tested. */
export const ALL_RUNS = "all";

/** FNV-1a: a stable number from a string. */
export function hash(text: string): number {
  let value = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 0x01000193);
  }
  return value >>> 0;
}

/** The models in an order that depends on the frame: a letter never tells which model wrote a
 * description, and the same frame keeps its letters when the page is opened again. */
export function blindOrder(runId: string, keyframeId: string, modelKeys: string[]): string[] {
  return [...modelKeys].sort(
    (a, b) =>
      hash(`${runId}|${keyframeId}|${a}`) - hash(`${runId}|${keyframeId}|${b}`) ||
      a.localeCompare(b),
  );
}

export interface Rating {
  count: number;
  mean: number | null;
}

/** The blind ratings a model got in this run: how many, and their mean (0–3). */
export function ratingOf(run: BenchRun, modelKey: string): Rating {
  const marks = Object.values(run.ratings)
    .map((byModel) => byModel[modelKey])
    .filter((mark): mark is number => typeof mark === "number");
  if (marks.length === 0) {
    return { count: 0, mean: null };
  }
  return { count: marks.length, mean: marks.reduce((sum, mark) => sum + mark, 0) / marks.length };
}

/** How many descriptions of the run can be rated, and how many are. */
export function ratingProgress(run: BenchRun): { rated: number; total: number } {
  let rated = 0;
  let total = 0;
  for (const frame of run.frames) {
    for (const [model, answer] of Object.entries(frame.answers)) {
      if (answer.ok) {
        total += 1;
        rated += typeof run.ratings[frame.keyframe_id]?.[model] === "number" ? 1 : 0;
      }
    }
  }
  return { rated, total };
}

export function gigabytes(mib: number | null | undefined): number | null {
  return mib === null || mib === undefined ? null : mib / 1024;
}

/** A file size in the unit of the memory figures (GiB), so that both compare. */
export function fileGigabytes(bytes: number | null | undefined): number | null {
  return bytes === null || bytes === undefined ? null : bytes / 1024 ** 3;
}

export function share(part: number, whole: number): number | null {
  return whole > 0 ? part / whole : null;
}

/** One model as the table and the charts show it. */
export interface BenchRow {
  id: string; // unique among the rows shown together
  label: string; // the model's name, told apart from a namesake by its quantization
  model: BenchModelRun;
  rating: Rating;
  origin?: BenchRunSummary; // the run the measure comes from, when the rows mix several runs
}

type Unlabelled = Omit<BenchRow, "label">;

/** Each model's name, told apart from a namesake by its quantization. */
export function modelLabels(models: BenchModelRun[]): string[] {
  const namesakes = new Map<string, number>();
  for (const model of models) {
    namesakes.set(model.display_name, (namesakes.get(model.display_name) ?? 0) + 1);
  }
  return models.map((model) => {
    const name = model.display_name;
    const twice = (namesakes.get(name) ?? 0) > 1 && model.quantization;
    return twice ? `${name} ${model.quantization ?? ""}` : name;
  });
}

function labelled(rows: Unlabelled[]): BenchRow[] {
  const labels = modelLabels(rows.map((row) => row.model));
  return rows.map((row, index) => ({ ...row, label: labels[index] ?? row.model.display_name }));
}

/** The models of one run, in the order they were tested (the smallest first). */
export function rowsOfRun(run: BenchRun): BenchRow[] {
  return labelled(
    run.model_runs.map((model) => ({ id: model.key, model, rating: ratingOf(run, model.key) })),
  );
}

export const isActive = (status: BenchRunSummary["status"] | undefined): boolean =>
  status === "queued" || status === "running";

const tested = (run: BenchRunSummary): BenchModelRun[] =>
  isActive(run.status) ? [] : run.model_runs.filter((model) => model.status === "done");

/** The languages the finished runs asked the descriptions in, the latest run's first. */
export function historyLanguages(runs: BenchRunSummary[]): string[] {
  return [...new Set(runs.filter((run) => tested(run).length > 0).map((run) => run.language))];
}

/** The latest result of every model tested in ``language``, all runs together (``runs``: the
 * latest first). A model is the same when its key and its quantization are. */
export function rowsOfHistory(runs: BenchRunSummary[], language: string): BenchRow[] {
  const latest = new Map<string, Unlabelled>();
  for (const run of runs) {
    if (run.language !== language) {
      continue;
    }
    for (const model of tested(run)) {
      const id = `${model.key}|${model.quantization ?? ""}`;
      if (!latest.has(id)) {
        const rating = { count: model.scores.rated, mean: model.scores.rating ?? null };
        latest.set(id, { id, model, rating, origin: run });
      }
    }
  }
  return labelled(
    [...latest.values()].sort(
      (a, b) =>
        (a.model.size_bytes ?? 0) - (b.model.size_bytes ?? 0) ||
        a.model.display_name.localeCompare(b.model.display_name),
    ),
  );
}

/** Whether the rows come from runs that did not ask about the same frames: memory and speed
 * still compare, the rest only roughly. */
export function mixedImages(rows: BenchRow[]): boolean {
  return new Set(rows.map((row) => row.origin?.image_set).filter(Boolean)).size > 1;
}

/** The best value of a measure among the tested models (highlighted in the table and in the
 * charts); null when fewer than two models have it, or when they all have the same: nothing
 * to tell apart. */
export function best(
  rows: BenchRow[],
  value: (row: BenchRow) => number | null | undefined,
  direction: "min" | "max",
): number | null {
  const values = rows
    .filter((row) => row.model.status === "done")
    .map(value)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const [low, high] = [Math.min(...values), Math.max(...values)];
  if (values.length < 2 || high - low < 1e-9) {
    return null;
  }
  return direction === "min" ? low : high;
}

/** The context most of the rows were loaded with: a model loaded otherwise says so, since its
 * memory does not compare as it is. */
export function usualContext(rows: BenchRow[]): number | null {
  const counts = new Map<number, number>();
  for (const { model } of rows) {
    if (typeof model.context_length === "number") {
      counts.set(model.context_length, (counts.get(model.context_length) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

export const same = (a: number | null | undefined, b: number | null): boolean =>
  typeof a === "number" && b !== null && Math.abs(a - b) < 1e-9;

/** The measures the table and the charts compare, each with the direction that is better. */
export const MEASURES = {
  vram: { value: (row: BenchRow) => gigabytes(row.model.scores.vram_mib), better: "min" },
  speed: { value: (row: BenchRow) => row.model.scores.seconds_per_image, better: "min" },
  valid: {
    value: (row: BenchRow) => share(row.model.scores.valid, row.model.scores.requests),
    better: "max",
  },
  language: {
    value: ({ model: { scores } }: BenchRow) =>
      share(scores.language_checked - scores.wrong_language, scores.language_checked),
    better: "max",
  },
  text: { value: (row: BenchRow) => row.model.scores.text_recall, better: "max" },
  positions: {
    value: ({ model: { scores } }: BenchRow) =>
      scores.positions_enabled === false ? null : scores.position_recall,
    better: "max",
  },
  quality: { value: (row: BenchRow) => row.rating.mean, better: "max" },
} as const satisfies Record<
  string,
  { value: (row: BenchRow) => number | null | undefined; better: "min" | "max" }
>;
export type MeasureId = keyof typeof MEASURES;

export interface LabelSpot {
  x: number;
  y: number;
  anchor: "start" | "end" | "middle";
}

interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

const overlaps = (a: Box, b: Box): boolean =>
  a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

/** Where to write the name of each point of a scatter chart: beside it, on the first side
 * where it covers neither another point nor a name already written, inside ``bounds``; null
 * where no side is free (the point keeps its tooltip, and the bars below name every model).
 * ``width``: the name's width in pixels. */
export function placeLabels(
  points: readonly { x: number; y: number; width: number }[],
  bounds: Box,
  { gap = 9, height = 13, radius = 6 } = {},
): (LabelSpot | null)[] {
  const taken: Box[] = points.map((point) => ({
    left: point.x - radius,
    right: point.x + radius,
    top: point.y - radius,
    bottom: point.y + radius,
  }));
  const rise = height * 0.3; // the baseline that centres a name on its point
  return points.map((point) => {
    const candidates: LabelSpot[] = [
      { x: point.x + gap, y: point.y + rise, anchor: "start" },
      { x: point.x - gap, y: point.y + rise, anchor: "end" },
      { x: point.x, y: point.y - gap, anchor: "middle" },
      { x: point.x, y: point.y + gap + height * 0.7, anchor: "middle" },
      { x: point.x + gap, y: point.y - gap, anchor: "start" },
      { x: point.x + gap, y: point.y + gap + height * 0.7, anchor: "start" },
      { x: point.x - gap, y: point.y - gap, anchor: "end" },
      { x: point.x - gap, y: point.y + gap + height * 0.7, anchor: "end" },
    ];
    const boxOf = (spot: LabelSpot): Box => {
      const left =
        spot.anchor === "start"
          ? spot.x
          : spot.anchor === "end"
            ? spot.x - point.width
            : spot.x - point.width / 2;
      return { left, right: left + point.width, top: spot.y - height * 0.8, bottom: spot.y + 3 };
    };
    const fits = (spot: LabelSpot): boolean => {
      const box = boxOf(spot);
      return (
        box.left >= bounds.left &&
        box.right <= bounds.right &&
        box.top >= bounds.top &&
        box.bottom <= bounds.bottom &&
        !taken.some((other) => overlaps(box, other))
      );
    };
    const spot = candidates.find(fits);
    if (!spot) {
      return null;
    }
    taken.push(boxOf(spot));
    return spot;
  });
}
