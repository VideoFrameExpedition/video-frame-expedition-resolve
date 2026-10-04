/** Small, dependency-free helpers for the SVG charts (linear scales, clean ticks, lookups). */

import { median } from "@/lib/kelvin";

export interface LinearScale {
  (value: number): number;
  readonly domain: readonly [number, number];
  readonly range: readonly [number, number];
  invert: (px: number) => number;
}

export function linearScale(
  domain: readonly [number, number],
  range: readonly [number, number],
): LinearScale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  const scale = ((value: number) => r0 + ((value - d0) / span) * (r1 - r0)) as LinearScale;
  return Object.assign(scale, {
    domain,
    range,
    invert: (px: number) => d0 + ((px - r0) / (r1 - r0 || 1)) * span,
  });
}

function niceStep(rough: number): number {
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * power;
}

/** Round tick values (1-2-5 steps) covering ``[min, max]``; the domain is widened to the ticks. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return [];
  }
  if (max - min < 1e-9) {
    const pad = Math.abs(min) > 1e-9 ? Math.abs(min) * 0.1 : 1;
    return niceTicks(min - pad, max + pad, count);
  }
  const step = niceStep((max - min) / Math.max(1, count));
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step; // the last tick always covers the maximum
  const ticks: number[] = [];
  for (let value = start; value <= end + step * 1e-6; value += step) {
    ticks.push(Number(value.toFixed(10)) + 0); // + 0 turns -0 into 0
  }
  return ticks;
}

/** Tick values of a logarithmic axis covering ``[min, max]`` (both positive): 1, 2 and 5 of
 * every power of ten. */
export function logTicks(min: number, max: number): number[] {
  if (!(min > 0) || !(max >= min) || !Number.isFinite(max)) {
    return [];
  }
  const ticks: number[] = [];
  for (let power = Math.floor(Math.log10(min)); power <= Math.ceil(Math.log10(max)); power += 1) {
    for (const digit of [1, 2, 5]) {
      ticks.push(Number((digit * 10 ** power).toPrecision(12)));
    }
  }
  const first = ticks.findLastIndex((tick) => tick <= min * (1 + 1e-9));
  const last = ticks.findIndex((tick) => tick >= max * (1 - 1e-9));
  return ticks.slice(Math.max(0, first), last < 0 ? undefined : last + 1);
}

const TIME_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

/** Clock-friendly tick positions (in seconds) for a timeline ``width`` pixels wide. */
export function timeTicks(duration: number, width: number, minSpacingPx = 72): number[] {
  if (duration <= 0 || width <= 0) {
    return [];
  }
  const wanted = Math.max(1, Math.floor(width / minSpacingPx));
  const step = TIME_STEPS.find((s) => duration / s <= wanted) ?? 7200;
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 1e-6; t += step) {
    ticks.push(Number(t.toFixed(3)));
  }
  return ticks;
}

/** Index of the value in ascending ``sorted`` that is closest to ``x`` (-1 when empty). */
export function nearestIndex(sorted: readonly number[], x: number): number {
  if (sorted.length === 0) {
    return -1;
  }
  let lo = 0;
  let hi = sorted.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((sorted[mid] ?? 0) < x) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  const before = lo - 1;
  if (before >= 0 && Math.abs((sorted[before] ?? 0) - x) <= Math.abs((sorted[lo] ?? 0) - x)) {
    return before;
  }
  return lo;
}

/**
 * Typical spacing of ascending sample times: the median gap between neighbours, robust to a
 * series that starts late or has holes (``fallback`` when there are fewer than two samples).
 */
export function medianStep(sorted: readonly number[], fallback = 1): number {
  const gaps: number[] = [];
  for (let i = 1; i < sorted.length; i += 1) {
    const gap = (sorted[i] ?? 0) - (sorted[i - 1] ?? 0);
    if (gap > 0) {
      gaps.push(gap);
    }
  }
  return median(gaps) ?? fallback;
}

/**
 * Inserts a ``null`` sample midway through every gap wider than ``maxGap`` so that
 * ``linePath`` and ``areaPath`` lift the pen instead of bridging missing measurements.
 */
export function breakGaps(
  ts: readonly number[],
  values: readonly (number | null)[],
  maxGap: number,
): { t: number[]; values: (number | null)[] } {
  const t: number[] = [];
  const out: (number | null)[] = [];
  for (const [i, time] of ts.entries()) {
    const previous = t[t.length - 1];
    if (previous !== undefined && time - previous > maxGap) {
      t.push((previous + time) / 2);
      out.push(null);
    }
    t.push(time);
    out.push(values[i] ?? null);
  }
  return { t, values: out };
}

/** ``value`` limited to ``[lo, hi]`` (bounds in either order). */
export function clampTo(value: number, [a, b]: readonly [number, number]): number {
  return Math.max(Math.min(a, b), Math.min(Math.max(a, b), value));
}

/** Index of the span ``[start, end)`` containing ``x`` among ascending, contiguous spans. */
export function spanIndexAt(starts: readonly number[], x: number): number {
  let lo = 0;
  let hi = starts.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((starts[mid] ?? 0) <= x + 1e-6) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** SVG path for a polyline; ``null`` values break the line. */
export function linePath(
  xs: readonly number[],
  ys: readonly (number | null)[],
  x: (v: number) => number,
  y: (v: number) => number,
): string {
  let path = "";
  let pen = false;
  for (let i = 0; i < xs.length; i += 1) {
    const value = ys[i];
    const t = xs[i];
    if (value === null || value === undefined || t === undefined || !Number.isFinite(value)) {
      pen = false;
      continue;
    }
    path += `${pen ? "L" : "M"}${x(t).toFixed(1)},${y(value).toFixed(1)}`;
    pen = true;
  }
  return path;
}

/** Closed area under a polyline down to ``baseline`` (segments split on gaps). */
export function areaPath(
  xs: readonly number[],
  ys: readonly (number | null)[],
  x: (v: number) => number,
  y: (v: number) => number,
  baseline: number,
): string {
  let path = "";
  let run: [number, number][] = [];
  const close = (): void => {
    const first = run[0];
    const last = run[run.length - 1];
    if (first && last && run.length > 1) {
      path += `M${first[0].toFixed(1)},${baseline.toFixed(1)}`;
      for (const [px, py] of run) {
        path += `L${px.toFixed(1)},${py.toFixed(1)}`;
      }
      path += `L${last[0].toFixed(1)},${baseline.toFixed(1)}Z`;
    }
    run = [];
  };
  for (let i = 0; i < xs.length; i += 1) {
    const value = ys[i];
    const t = xs[i];
    if (value === null || value === undefined || t === undefined || !Number.isFinite(value)) {
      close();
      continue;
    }
    run.push([x(t), y(value)]);
  }
  close();
  return path;
}
