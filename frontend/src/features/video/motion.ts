/**
 * Camera-motion classes (backend ``domain/shots.py``) grouped into four families for colour.
 *
 * Timeline segments can sit next to any other family, so colour identity uses only the first
 * three categorical slots (validated all-pairs) plus the neutral gray for "static"; the precise
 * direction is carried by a glyph and a label, never by colour alone.
 */

export type MotionFamily = "camera" | "zoom" | "free" | "static";

export const MOTION_FAMILIES: readonly MotionFamily[] = ["camera", "zoom", "free", "static"];

const FAMILY: Record<string, MotionFamily> = {
  static: "static",
  pan_left: "camera",
  pan_right: "camera",
  tilt_up: "camera",
  tilt_down: "camera",
  zoom_in: "zoom",
  zoom_out: "zoom",
  handheld: "free",
  moving: "free",
};

export const FAMILY_FILL: Record<MotionFamily, string> = {
  camera: "var(--chart-1)",
  zoom: "var(--chart-2)",
  free: "var(--chart-3)",
  static: "var(--chart-neutral)",
};

const GLYPH: Record<string, string> = {
  static: "•",
  pan_left: "←",
  pan_right: "→",
  tilt_up: "↑",
  tilt_down: "↓",
  zoom_in: "+",
  zoom_out: "−",
  handheld: "≈",
  moving: "↝",
};

export function motionFamily(motion: string): MotionFamily {
  return FAMILY[motion] ?? "free";
}

export function motionGlyph(motion: string): string {
  return GLYPH[motion] ?? "?";
}
