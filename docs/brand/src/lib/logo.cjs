"use strict";
/**
 * The symbol « Le Repère » and the logotype, as SVG strings.
 *
 * The symbol lives on a 96 × 96 grid: two opposite viewfinder corners (top left, bottom right,
 * ends cut at 45°) around a four-facet diamond, lit from the top right. The diamond is the
 * keyframe; the corners are the frame; its two halves are the golden hour (right) and the blue
 * hour (left). Colors come from `palette.json` (`frame`, `mark-*`), never from here.
 *
 * The logotype is set in Epilogue alone and **justified**: every line is stretched by its
 * tracking to the width of the widest one, and aligned on the real left edge of its ink. The
 * lines therefore stack into a true rectangle beside the symbol (docs/brand/README.md).
 */
const { textPath, trackingFor } = require("./text.cjs");

const NAME = "Video Frame Expedition for DaVinci Resolve";

/** Geometry presets on the 96-unit grid. `small` is heavier, for favicons. */
const GEO = {
  primary: { inset: 6, len: 34, w: 9, r: 13, R: 27 },
  small: { inset: 7, len: 38, w: 12, r: 15, R: 28 },
};

const n = (x) => +x.toFixed(2);
const P = (pts) => pts.map(([x, y]) => `${n(x)},${n(y)}`).join(" ");

/** One corner bracket (top left) with 45° cut ends; the bottom right one is its 180° rotation. */
function bracketPath({ inset, len, w, r }) {
  const a = inset;
  const ri = Math.max(r - w, 0.01);
  return (
    `M${a} ${a + len} V${a + r} A${r} ${r} 0 0 1 ${a + r} ${a} H${a + len} L${a + len - w} ${a + w} ` +
    `H${a + r} A${ri} ${ri} 0 0 0 ${a + w} ${a + r} V${a + len - w} Z`
  );
}

/**
 * The symbol without its <svg> wrapper.
 * @param {{frame: string, goldLight: string, gold: string, blue: string, blueLight: string}} pal
 */
function markBody(pal, geo = GEO.primary) {
  const d = bracketPath(geo);
  const c = [48, 48];
  const { R } = geo;
  const N = [48, 48 - R];
  const E = [48 + R, 48];
  const S = [48, 48 + R];
  const W = [48 - R, 48];
  return (
    `<path fill="${pal.frame}" d="${d}"/>` +
    `<path fill="${pal.frame}" transform="rotate(180 48 48)" d="${d}"/>` +
    `<polygon fill="${pal.goldLight}" points="${P([c, N, E])}"/>` +
    `<polygon fill="${pal.gold}" points="${P([c, E, S])}"/>` +
    `<polygon fill="${pal.blue}" points="${P([c, S, W])}"/>` +
    `<polygon fill="${pal.blueLight}" points="${P([c, W, N])}"/>`
  );
}

/** Single-ink symbol: the four facets keep their relief through opacity only. */
function markMonoBody(ink, geo = GEO.primary) {
  const d = bracketPath(geo);
  const c = [48, 48];
  const { R } = geo;
  const N = [48, 48 - R];
  const E = [48 + R, 48];
  const S = [48, 48 + R];
  const W = [48 - R, 48];
  return (
    `<path fill="${ink}" d="${d}"/>` +
    `<path fill="${ink}" transform="rotate(180 48 48)" d="${d}"/>` +
    `<polygon fill="${ink}" points="${P([c, N, E])}"/>` +
    `<polygon fill="${ink}" fill-opacity=".74" points="${P([c, E, S])}"/>` +
    `<polygon fill="${ink}" fill-opacity=".46" points="${P([c, S, W])}"/>` +
    `<polygon fill="${ink}" fill-opacity=".6" points="${P([c, W, N])}"/>`
  );
}

/** A standalone <svg>. `uid` prefixes ids so several can share one HTML page. */
function svgDoc(inner, { w, h, vb, title = NAME, defs = "", uid = "" }) {
  const t = `${uid}title`;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${vb}" width="${n(w)}" height="${n(h)}" role="img" aria-labelledby="${t}">` +
    `<title id="${t}">${title}</title>${defs}${inner}</svg>\n`
  );
}

/**
 * Line specs of the logotype, in units of the symbol's 96-unit grid. `lead` is the distance from
 * the previous baseline; `track` is the tracking the line keeps *before* justification, which
 * only ever adds to it.
 */
const VARIANTS = {
  // The full name on three lines. The reference lockup.
  full: [
    { font: "epilogue-400", text: "VIDEO FRAME", size: 21, track: 0.03 },
    { font: "epilogue-700", text: "EXPEDITION", size: 31, lead: 33, accent: true },
    { font: "epilogue-500", text: "FOR DAVINCI RESOLVE", size: 10, lead: 21, track: 0.09, muted: true },
  ],
  // The short name, where « for DaVinci Resolve » sits elsewhere or the height is short.
  compact: [
    { font: "epilogue-400", text: "VIDEO FRAME", size: 26, track: 0.03 },
    { font: "epilogue-700", text: "EXPEDITION", size: 38, lead: 41, accent: true },
  ],
  // Interface lockup: the descriptor is enlarged, and tracked less, to hold below 56 px high.
  ui: [
    { font: "epilogue-400", text: "VIDEO FRAME", size: 20, track: 0.03 },
    { font: "epilogue-700", text: "EXPEDITION", size: 29, lead: 32, accent: true },
    { font: "epilogue-500", text: "FOR DAVINCI RESOLVE", size: 12, lead: 23, track: 0.06, muted: true },
  ],
};

/**
 * @typedef {{l1: string, l2: string, l3: string}} TextColors the three roles of the logotype
 *
 * Lays the lines of a variant out, justified to the widest one and aligned on their ink.
 * `cy` centres the block vertically; `x0` is the left edge of the ink.
 */
function layoutText(variant, colors, { x0 = 0, cy = 48, align = "left", width = null } = {}) {
  const lines = VARIANTS[variant];
  const fill = (l) => (l.accent ? colors.l2 : l.muted ? colors.l3 : colors.l1);
  const natural = lines.map((l) => textPath(l.font, l.text, l.size, { tracking: l.track ?? 0 }));
  // Justify to the widest line: the tracking only ever grows, never tightens the letters.
  const target = width ?? Math.max(...natural.map((m) => m.inkWidth));
  const tracks = lines.map((l, i) =>
    natural[i].gaps ? trackingFor(l.font, l.text, l.size, target, l.track ?? 0) : (l.track ?? 0),
  );
  let baseline = 0;
  const baselines = lines.map((l, i) => (i === 0 ? 0 : (baseline += l.lead)));
  // Centre the block on `cy`, between the cap line of the first line and the last baseline.
  const top0 = baselines[0] - natural[0].capHeight;
  const shift = cy - (top0 + baselines[baselines.length - 1]) / 2;
  let out = "";
  lines.forEach((l, i) => {
    const m = textPath(l.font, l.text, l.size, { tracking: tracks[i] });
    const x = align === "center" ? x0 + (target - m.inkWidth) / 2 : x0;
    const set = textPath(l.font, l.text, l.size, { tracking: tracks[i], x: x - m.inkLeft, y: baselines[i] + shift });
    out += `<path d="${set.d}" fill="${fill(l)}"/>`;
  });
  return {
    out,
    width: target,
    top: top0 + shift,
    bottom: baselines[baselines.length - 1] + shift,
  };
}

/** Horizontal lockup, from an already drawn symbol: the symbol, then the logotype beside it. */
function lockupFrom(symbol, variant, colors, uid) {
  const x0 = 96 + 22;
  const t = layoutText(variant, colors, { x0 });
  const w = x0 + t.width + 6;
  return svgDoc(symbol + t.out, { w, h: 96, vb: `0 0 ${n(w)} 96`, uid });
}

/** Horizontal lockup as a standalone SVG. */
function lockupH(variant, pal, colors, { uid = "" } = {}) {
  return lockupFrom(markBody(pal), variant, colors, uid);
}

/** Single-ink lockup (one colour, tonal symbol): print, engraving, coloured grounds. */
function lockupMono(ink, { uid = "" } = {}) {
  return lockupFrom(markMonoBody(ink), "full", { l1: ink, l2: ink, l3: ink }, uid);
}

/** Stacked lockup: the symbol above, the logotype centred under it. */
function lockupStacked(pal, colors, { uid = "" } = {}) {
  const probe = layoutText("full", colors, { x0: 0 });
  const w = probe.width + 12;
  const gap = 30;
  const t = layoutText("full", colors, { x0: 6, cy: 0, align: "center", width: probe.width });
  // Put the block's cap line `gap` under the symbol's frame.
  const dy = 96 + gap - t.top;
  const inner =
    `<g transform="translate(${n(w / 2 - 48)} 0)">${markBody(pal)}</g>` + `<g transform="translate(0 ${n(dy)})">${t.out}</g>`;
  const h = 96 + gap + (t.bottom - t.top) + 8;
  return svgDoc(inner, { w, h, vb: `0 0 ${n(w)} ${n(h)}`, uid });
}

/** The logotype alone (no symbol), left aligned. */
function wordmark(colors, { uid = "" } = {}) {
  const t = layoutText("full", colors, { x0: 6, cy: 48 });
  const top = t.top - 6;
  const h = t.bottom - t.top + 18;
  return svgDoc(t.out, { w: t.width + 12, h, vb: `6 ${n(top)} ${n(t.width + 12)} ${n(h)}`, uid });
}

/** 32-unit tile for browser tabs and app launchers: the symbol, heavier, on a `tile` ground. */
function favicon(pal, { tile = "#0a101b", uid = "" } = {}) {
  const s = 0.3;
  const off = (32 - 96 * s) / 2;
  const inner = `<rect width="32" height="32" rx="7.5" fill="${tile}"/><g transform="translate(${n(off)} ${n(off)}) scale(${s})">${markBody(pal, GEO.small)}</g>`;
  return svgDoc(inner, { w: 32, h: 32, vb: "0 0 32 32", uid });
}

/** 512-unit launcher icon: night tile, soft golden and blue glows, the symbol. */
function appIcon(pal, { uid = "", top = "#121b2c", bottom = "#0a101b", glowGold = "#ffab2e", glowBlue = "#3f52d9", rx = 112 } = {}) {
  const defs =
    `<defs><linearGradient id="${uid}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${top}"/><stop offset="1" stop-color="${bottom}"/></linearGradient>` +
    `<radialGradient id="${uid}glow" cx="74%" cy="22%" r="72%"><stop offset="0" stop-color="${glowGold}" stop-opacity=".32"/>` +
    `<stop offset=".55" stop-color="${glowBlue}" stop-opacity=".16"/><stop offset="1" stop-color="${bottom}" stop-opacity="0"/></radialGradient></defs>`;
  const inner =
    `<rect width="512" height="512" rx="${rx}" fill="url(#${uid}bg)"/><rect width="512" height="512" rx="${rx}" fill="url(#${uid}glow)"/>` +
    `<g transform="translate(96 96) scale(3.3333)">${markBody(pal)}</g>`;
  return svgDoc(inner, { w: 512, h: 512, vb: "0 0 512 512", defs, uid });
}

module.exports = { NAME, GEO, VARIANTS, bracketPath, markBody, markMonoBody, svgDoc, layoutText, lockupH, lockupMono, lockupStacked, wordmark, favicon, appIcon };
