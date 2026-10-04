/** Formatting helpers shared by the UI (locale-aware where it matters). */

export function formatClock(seconds: number | null | undefined, withMillis = false): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return "—";
  }
  const totalMs = withMillis ? Math.round(seconds * 1000) : Math.floor(seconds) * 1000;
  const whole = Math.floor(totalMs / 1000);
  const ms = totalMs % 1000;
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const pad = (value: number, size = 2): string => value.toString().padStart(size, "0");
  const base = hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${pad(minutes)}:${pad(secs)}`;
  return withMillis ? `${base}.${pad(ms, 3)}` : base;
}

export function formatBytes(bytes: number | null | undefined, locale = "fr"): string {
  if (bytes === null || bytes === undefined) {
    return "—";
  }
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = value >= 100 || unit === 0 ? 0 : 1;
  return `${value.toLocaleString(locale, { maximumFractionDigits: digits })}\u00a0${units[unit] ?? ""}`;
}

export function formatDateTime(iso: string | null | undefined, locale = "fr"): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return "—";
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  const seconds = ms / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)} s` : formatClock(seconds);
}

/** A span of time in words, for waits and elapsed times: « 45 s », « 12 min », « 2 h 05 ». */
export function formatSpan(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "—";
  }
  const space = "\u00a0";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) {
    return `${String(whole)}${space}s`;
  }
  const minutes = Math.round(whole / 60);
  if (minutes < 60) {
    return `${String(minutes)}${space}min`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const head = `${String(hours)}${space}h`;
  return rest > 0 ? `${head}${space}${String(rest).padStart(2, "0")}` : head;
}

export function formatPercent(fraction: number): string {
  return `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}\u00a0%`;
}

/** Locale-aware number with a fixed number of decimals, "—" when missing. */
export function formatNumber(
  value: number | null | undefined,
  locale = "fr",
  digits = 0,
  unit = "",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const text = value.toLocaleString(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return unit ? `${text}\u00a0${unit}` : text;
}

/** ``120`` → ``UTC+02:00``, ``-210`` → ``UTC−03:30`` (true minus sign). */
export function formatUtcOffset(minutes: number): string {
  const sign = minutes < 0 ? "\u2212" : "+";
  const abs = Math.abs(minutes);
  const pad = (value: number): string => value.toString().padStart(2, "0");
  return `UTC${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** A file name cut after its "_" and "-", where a line may break (never "….m" / "p4"). */
export function splitAfterSeparators(name: string): string[] {
  return name.split(/(?<=[_-])/);
}
