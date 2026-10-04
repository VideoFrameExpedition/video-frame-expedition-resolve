"use strict";
/** Reads `palette.json`: resolves the aliases of each theme and computes contrast ratios. */
const fs = require("node:fs");
const path = require("node:path");

const FILE = path.join(__dirname, "..", "..", "palette.json");

function load() {
  return JSON.parse(fs.readFileSync(FILE, "utf8"));
}

/** `resolve(name, theme)` gives the hex value of a color in a theme, following `{alias}` values. */
function createResolver(palette) {
  const themes = palette.themes.map((t) => t.id);
  const raw = new Map(palette.colors.map((c) => [c.name, c.value]));
  function resolve(name, theme, seen = []) {
    if (!raw.has(name)) throw new Error(`unknown color: ${name}`);
    if (seen.includes(name)) throw new Error(`alias cycle: ${[...seen, name].join(" > ")}`);
    const value = raw.get(name);
    const own = typeof value === "string" ? value : (value[theme] ?? value[themes[0]]);
    const alias = /^\{([^}]+)\}$/.exec(own);
    return alias ? resolve(alias[1], theme, [...seen, name]) : own;
  }
  return { themes, resolve };
}

function luminance(hex) {
  const h = hex.replace("#", "").slice(0, 6);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** WCAG contrast ratio between two colors. */
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

module.exports = { FILE, load, createResolver, contrast };
