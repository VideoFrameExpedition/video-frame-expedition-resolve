"use strict";
/**
 * Text to SVG path data, glyph by glyph (no GSUB shaping), with kerning and tracking.
 *
 * The logotype is drawn from outlines so that it renders the same everywhere and needs no
 * installed font. Fonts are the open-licence (OFL) files of the `@fontsource` packages, fetched
 * once into `docs/brand/.cache/fonts` (not versioned).
 *
 * Lines report the real edges of their ink, not their advance widths: the logotype is aligned
 * and justified on what the eye sees, which is what makes its lines stack into a clean block.
 */
const fs = require("node:fs");
const path = require("node:path");

const CACHE = path.join(__dirname, "..", "..", ".cache", "fonts");

/** The logotype is set in Epilogue alone. key -> [fontsource package, file stem, cached name] */
const FONTS = {
  "epilogue-400": ["epilogue", "epilogue-latin-400-normal", "Epilogue-400.woff"],
  "epilogue-500": ["epilogue", "epilogue-latin-500-normal", "Epilogue-500.woff"],
  "epilogue-700": ["epilogue", "epilogue-latin-700-normal", "Epilogue-700.woff"],
};

function cachePath(key) {
  return path.join(CACHE, FONTS[key][2]);
}

/** Downloads the fonts that are not cached yet. */
async function ensureFonts() {
  fs.mkdirSync(CACHE, { recursive: true });
  for (const [key, [pkg, stem]] of Object.entries(FONTS)) {
    const target = cachePath(key);
    if (fs.existsSync(target)) continue;
    const url = `https://cdn.jsdelivr.net/npm/@fontsource/${pkg}@5/files/${stem}.woff`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`font ${key}: ${url} answered ${response.status}`);
    fs.writeFileSync(target, Buffer.from(await response.arrayBuffer()));
    console.log(`  font downloaded: ${FONTS[key][2]}`);
  }
}

let opentype = null;
const parsed = new Map();

/** Own path serializer: opentype.js 2.x sometimes prints "NaN" in `toPathData`, whatever the options. */
function pathData(commands) {
  const f = (v) => {
    if (!Number.isFinite(v)) throw new Error(`non-finite coordinate in a glyph outline: ${v}`);
    const r = Math.round(v * 100) / 100;
    return Object.is(r, -0) ? "0" : String(r);
  };
  let d = "";
  for (const c of commands) {
    if (c.type === "M" || c.type === "L") d += `${c.type}${f(c.x)} ${f(c.y)}`;
    else if (c.type === "Q") d += `Q${f(c.x1)} ${f(c.y1)} ${f(c.x)} ${f(c.y)}`;
    else if (c.type === "C") d += `C${f(c.x1)} ${f(c.y1)} ${f(c.x2)} ${f(c.y2)} ${f(c.x)} ${f(c.y)}`;
    else if (c.type === "Z") d += "Z";
  }
  return d;
}

function loadFont(key) {
  if (!opentype) {
    try {
      opentype = require("opentype.js");
    } catch {
      throw new Error("opentype.js is missing: run `npm install --no-save opentype.js` in docs/brand");
    }
  }
  if (!parsed.has(key)) {
    const bytes = fs.readFileSync(cachePath(key));
    parsed.set(key, opentype.parse(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)));
  }
  return parsed.get(key);
}

/**
 * @param {string} key font key of FONTS
 * @param {string} text
 * @param {number} size font size in SVG user units
 * @param {{x?: number, y?: number, tracking?: number}} [o] tracking in em
 * @returns {{d: string, gaps: number, inkLeft: number, inkRight: number, inkWidth: number,
 *            advance: number, capHeight: number}}
 *   `inkLeft` and `inkRight` are relative to `x`: where the drawing really starts and ends.
 */
function textPath(key, text, size, o = {}) {
  const font = loadFont(key);
  const scale = size / font.unitsPerEm;
  const x0 = o.x ?? 0;
  const y0 = o.y ?? 0;
  const track = (o.tracking ?? 0) * size;
  const glyphs = Array.from(text).map((ch) => font.charToGlyph(ch));
  const parts = [];
  let pen = 0;
  let inkLeft = Infinity;
  let inkRight = -Infinity;
  glyphs.forEach((glyph, i) => {
    parts.push(pathData(glyph.getPath(x0 + pen, y0, size).commands));
    const box = glyph.getBoundingBox();
    // A space has no outline: it moves the pen without adding ink.
    if (Number.isFinite(box.x1) && box.x2 > box.x1) {
      inkLeft = Math.min(inkLeft, pen + box.x1 * scale);
      inkRight = Math.max(inkRight, pen + box.x2 * scale);
    }
    let advance = glyph.advanceWidth * scale;
    if (i < glyphs.length - 1) {
      try {
        advance += font.getKerningValue(glyph, glyphs[i + 1]) * scale;
      } catch {
        /* unsupported kerning table */
      }
      advance += track;
    }
    pen += advance;
  });
  const os2 = font.tables.os2 || {};
  return {
    d: parts.join(" "),
    gaps: Math.max(glyphs.length - 1, 0),
    inkLeft,
    inkRight,
    inkWidth: inkRight - inkLeft,
    advance: pen,
    capHeight: (os2.sCapHeight || font.ascender * 0.7) * scale,
  };
}

/** The tracking, in em, that makes this line's ink exactly `target` wide. */
function trackingFor(key, text, size, target, base = 0) {
  const probe = textPath(key, text, size, { tracking: base });
  if (!probe.gaps) return base;
  return base + (target - probe.inkWidth) / (probe.gaps * size);
}

module.exports = { FONTS, CACHE, cachePath, ensureFonts, textPath, trackingFor };
