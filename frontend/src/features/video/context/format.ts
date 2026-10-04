/** Pure helpers of the Context tab: local clock times, compass, DMS, day phases. */

export interface LocalZone {
  timeZone: string | null | undefined;
  offsetMin: number | null | undefined;
}

/** ``HH:MM`` in the place's zone, else with the known offset, else UTC (never the browser's). */
export function localClock(
  iso: string | null | undefined,
  zone: LocalZone,
  locale: string,
): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const style: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit" };
  if (zone.timeZone) {
    try {
      return date.toLocaleTimeString(locale, { ...style, timeZone: zone.timeZone });
    } catch {
      // Unknown zone for this browser: fall back to the offset.
    }
  }
  const shifted = new Date(date.getTime() + (zone.offsetMin ?? 0) * 60_000);
  return shifted.toLocaleTimeString(locale, { ...style, timeZone: "UTC" });
}

/** Minutes since local midnight of ``iso`` in ``zone`` (for the day strip). */
export function localMinutes(iso: string, zone: LocalZone): number | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  if (zone.timeZone) {
    try {
      const parts = new Intl.DateTimeFormat("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
        timeZone: zone.timeZone,
      }).formatToParts(date);
      const hour = Number(parts.find((p) => p.type === "hour")?.value);
      const minute = Number(parts.find((p) => p.type === "minute")?.value);
      if (Number.isFinite(hour) && Number.isFinite(minute)) return hour * 60 + minute;
    } catch {
      // fall through
    }
  }
  const shifted = new Date(date.getTime() + (zone.offsetMin ?? 0) * 60_000);
  return shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
}

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"] as const; // prettier-ignore

/** 16-point compass key (``W``, ``NNE``…), translated by the caller. */
export function compassKey(degrees: number): (typeof COMPASS)[number] {
  return COMPASS[((Math.round(degrees / 22.5) % 16) + 16) % 16] ?? "N";
}

/** ``48.8584`` → ``48°51′30.2″ N`` (latitude) / ``E``/``W`` for longitude. */
export function toDms(value: number, axis: "lat" | "lon"): string {
  const hemisphere = axis === "lat" ? (value >= 0 ? "N" : "S") : value >= 0 ? "E" : "W";
  const abs = Math.abs(value);
  let degrees = Math.floor(abs);
  let minutes = Math.floor((abs - degrees) * 60);
  let seconds = Math.round(((abs - degrees) * 60 - minutes) * 600) / 10;
  if (seconds >= 60) {
    seconds -= 60;
    minutes += 1;
  }
  if (minutes >= 60) {
    minutes -= 60;
    degrees += 1;
  }
  return `${degrees}°${minutes.toString().padStart(2, "0")}′${seconds.toFixed(1).padStart(4, "0")}″\u00a0${hemisphere}`;
}

export const DAY_PHASES = [
  "night",
  "astronomical_twilight",
  "nautical_twilight",
  "blue_hour",
  "golden_hour",
  "day",
] as const;
export type DayPhase = (typeof DAY_PHASES)[number];

/** Crossing name (backend ``EVENT_TARGETS``) → the phase entered when the sun rises past it. */
const RISING_ENTERS: Record<string, DayPhase> = {
  astronomical: "astronomical_twilight",
  nautical: "nautical_twilight",
  civil: "blue_hour",
  blue_golden: "golden_hour",
  golden_day: "day",
};
/** …and the phase entered when the sun sets past it. */
const SETTING_ENTERS: Record<string, DayPhase> = {
  golden_day: "golden_hour",
  blue_golden: "blue_hour",
  civil: "nautical_twilight",
  nautical: "astronomical_twilight",
  astronomical: "night",
};

export interface PhaseSegment {
  phase: DayPhase;
  start: number; // minutes since local midnight
  end: number;
}

function below(phase: DayPhase): DayPhase {
  return DAY_PHASES[Math.max(0, DAY_PHASES.indexOf(phase) - 1)] ?? "night";
}

function above(phase: DayPhase): DayPhase {
  return DAY_PHASES[Math.min(DAY_PHASES.length - 1, DAY_PHASES.indexOf(phase) + 1)] ?? "day";
}

/**
 * Day strip segments (local midnight → midnight) from the sun events of the capture's local date.
 * Without any crossing (polar day or night), the whole day has the phase seen at the capture.
 */
export function phaseSegments(
  events: {
    rising: Record<string, string | null | undefined>;
    setting: Record<string, string | null | undefined>;
  },
  zone: LocalZone,
  fallback: DayPhase,
): PhaseSegment[] {
  const marks: { minute: number; enters: DayPhase; before: DayPhase }[] = [];
  for (const [name, iso] of Object.entries(events.rising)) {
    const enters = RISING_ENTERS[name];
    const minute = iso ? localMinutes(iso, zone) : null;
    if (enters && minute !== null) marks.push({ minute, enters, before: below(enters) });
  }
  for (const [name, iso] of Object.entries(events.setting)) {
    const enters = SETTING_ENTERS[name];
    const minute = iso ? localMinutes(iso, zone) : null;
    if (enters && minute !== null) marks.push({ minute, enters, before: above(enters) });
  }
  marks.sort((a, b) => a.minute - b.minute);
  const first = marks[0];
  if (!first) return [{ phase: fallback, start: 0, end: 1440 }];
  const segments: PhaseSegment[] = [];
  let start = 0;
  let phase = first.before;
  for (const mark of marks) {
    if (mark.minute > start) segments.push({ phase, start, end: mark.minute });
    start = mark.minute;
    phase = mark.enters;
  }
  if (start < 1440) segments.push({ phase, start, end: 1440 });
  return segments;
}

export function isDayPhase(value: string | null | undefined): value is DayPhase {
  return (DAY_PHASES as readonly string[]).includes(value ?? "");
}
