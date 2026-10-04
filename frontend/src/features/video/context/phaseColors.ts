import type { DayPhase } from "./format";

/** Colour tokens of the light phases (validated for colour-vision deficiencies). */
export const PHASE_FILL: Record<DayPhase, string> = {
  night: "var(--phase-night)",
  astronomical_twilight: "var(--phase-astronomical)",
  nautical_twilight: "var(--phase-nautical)",
  blue_hour: "var(--phase-blue)",
  golden_hour: "var(--phase-golden)",
  day: "var(--phase-day)",
};
