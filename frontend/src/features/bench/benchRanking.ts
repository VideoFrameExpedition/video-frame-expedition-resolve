import { gigabytes, MEASURES, type BenchRow } from "./benchFormat";

/** The measures a ranking counts, each brought to points out of 100. */
export const CRITERIA = ["vram", "speed", "language", "text", "positions", "quality"] as const;
export type Criterion = (typeof CRITERIA)[number];
/** What matters most to the user: that measure weighs three times in the total. */
export const PRIORITIES = ["balanced", "vram", "speed", "quality"] as const;
export type Priority = (typeof PRIORITIES)[number];

const HEAVY = 3;
const MAX_RATING = 3;

export interface Ranked {
  row: BenchRow;
  points: Partial<Record<Criterion, number>>; // 0–100; a measure the model does not have is absent
  places: Partial<Record<Criterion, number>>; // 1: the best; equal points share a place
  total: number | null; // the weighted mean of the counted measures
  place: number | null;
}

export interface Ranking {
  ranked: Ranked[]; // the best total first
  counted: Criterion[]; // the measures every model has: the total counts those only
  left: Criterion[]; // measured for some models only: shown, not counted
}

const whole = (points: number): number => Math.round(Math.max(0, Math.min(100, points)));

/** Each measure as points out of 100, the more the better:
 * - memory: the share of the card the model leaves free (without a card: against the lightest);
 * - speed: against the fastest model (twice as slow: half the points);
 * - language, text, positions: the share measured (positions disabled: none);
 * - quality: the mean of the blind ratings. */
function pointsOf(rows: BenchRow[]): Partial<Record<Criterion, number>>[] {
  const known = (values: (number | null | undefined)[]): number[] =>
    values.filter((value): value is number => typeof value === "number" && value > 0);
  const cards = known(rows.map((row) => gigabytes(row.model.scores.vram_total_mib)));
  const card = cards.length > 0 ? Math.max(...cards) : null;
  const lightest = Math.min(...known(rows.map(MEASURES.vram.value)));
  const fastest = Math.min(...known(rows.map(MEASURES.speed.value)));
  return rows.map((row) => {
    const points: Partial<Record<Criterion, number>> = {};
    const memory = MEASURES.vram.value(row);
    if (memory !== null && memory > 0) {
      points.vram = whole(card ? 100 * (1 - memory / card) : (100 * lightest) / memory);
    }
    const seconds = MEASURES.speed.value(row);
    if (typeof seconds === "number" && seconds > 0) {
      points.speed = whole((100 * fastest) / seconds);
    }
    const language = MEASURES.language.value(row);
    if (language !== null) {
      points.language = whole(100 * language);
    }
    const text = MEASURES.text.value(row);
    if (typeof text === "number") {
      points.text = whole(100 * text);
    }
    const positions = MEASURES.positions.value(row);
    if (row.model.scores.positions_enabled === false) {
      points.positions = 0;
    } else if (typeof positions === "number") {
      points.positions = whole(100 * positions);
    }
    if (row.rating.mean !== null) {
      points.quality = whole((100 * row.rating.mean) / MAX_RATING);
    }
    return points;
  });
}

/** The place of each value among all: 1 for the highest, equal values sharing a place. */
function placesOf(values: (number | null | undefined)[]): (number | null)[] {
  return values.map((value) =>
    typeof value === "number"
      ? 1 + values.filter((other) => typeof other === "number" && other > value + 1e-9).length
      : null,
  );
}

/** The tested models ranked: a place per measure, and a total out of 100 on the measures they
 * all have (so that every model is judged on the same ground). */
export function rank(rows: BenchRow[], priority: Priority = "balanced"): Ranking {
  const tested = rows.filter((row) => row.model.status === "done");
  const points = pointsOf(tested);
  const has = (criterion: Criterion) => points.filter((p) => p[criterion] !== undefined).length;
  const counted = CRITERIA.filter((c) => tested.length > 0 && has(c) === tested.length);
  const left = CRITERIA.filter((c) => has(c) > 0 && has(c) < tested.length);
  const weight = (criterion: Criterion): number => (criterion === priority ? HEAVY : 1);
  const weights = counted.reduce((sum, criterion) => sum + weight(criterion), 0);
  const totals = points.map((p) =>
    weights > 0
      ? Math.round((10 * counted.reduce((sum, c) => sum + weight(c) * (p[c] ?? 0), 0)) / weights) /
        10
      : null,
  );
  const totalPlaces = placesOf(totals);
  const ranked: Ranked[] = tested.map((row, index) => ({
    row,
    points: points[index] ?? {},
    places: {},
    total: totals[index] ?? null,
    place: totalPlaces[index] ?? null,
  }));
  for (const criterion of CRITERIA) {
    for (const [index, place] of placesOf(points.map((p) => p[criterion])).entries()) {
      const model = ranked[index];
      if (model && place !== null) {
        model.places[criterion] = place;
      }
    }
  }
  ranked.sort(
    (a, b) => (b.total ?? -1) - (a.total ?? -1) || a.row.label.localeCompare(b.row.label),
  );
  return { ranked, counted, left };
}

/** « 1er », « 2e »… in French; « 1st », « 2nd »… otherwise. */
export function ordinal(place: number, locale: string): string {
  if (locale.startsWith("fr")) {
    return place === 1 ? "1er" : `${String(place)}e`;
  }
  const suffixes: Partial<Record<Intl.LDMLPluralRule, string>> = {
    one: "st",
    two: "nd",
    few: "rd",
  };
  const rule = new Intl.PluralRules("en", { type: "ordinal" }).select(place);
  return `${String(place)}${suffixes[rule] ?? "th"}`;
}

/** The corners of a model's profile on a radar chart: one branch per measure, from the centre
 * (0 points) to the rim (100), the first one pointing up, clockwise. */
export function radarPoints(
  values: readonly number[],
  centre: { x: number; y: number },
  radius: number,
): { x: number; y: number }[] {
  return values.map((value, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / values.length;
    const reach = (radius * Math.max(0, Math.min(100, value))) / 100;
    return { x: centre.x + reach * Math.cos(angle), y: centre.y + reach * Math.sin(angle) };
  });
}
