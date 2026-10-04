#!/usr/bin/env node
"use strict";
/**
 * Draws the logo of Video Frame Expedition for DaVinci Resolve: the SVG files of `logo/` and
 * `icons/`, and the copies that the web interface embeds (`frontend/src/assets/brand`,
 * `frontend/public/favicon.svg`). Colors come from `palette.json`, shapes and text from
 * `src/lib/logo.cjs`.
 *
 *   cd docs/brand
 *   npm install --no-save opentype.js     # once, to outline the logotype
 *   node build.cjs
 *
 * The PNG files (launcher icons, overview) are rendered from these SVGs by `render-png.mjs`.
 */
const fs = require("node:fs");
const path = require("node:path");

const { ensureFonts } = require("./src/lib/text.cjs");
const paletteLib = require("./src/lib/palette.cjs");
const logo = require("./src/lib/logo.cjs");

const ROOT = __dirname;
const REPO = path.join(ROOT, "..", "..");
const APP_ASSETS = path.join(REPO, "frontend", "src", "assets", "brand");
const APP_PUBLIC = path.join(REPO, "frontend", "public");

const write = (file, text) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text.replace(/\r\n/g, "\n"), "utf8");
};

/** The build fails, not the reader: every line of the logotype must read on the page it is drawn for. */
function checkContrast(R, lines) {
  const checks = [];
  for (const theme of ["dark", "light"]) {
    const bg = R("bg", theme);
    const c = lines(theme);
    checks.push([`${theme} « VIDEO FRAME »`, c.l1, bg, 4.5]);
    checks.push([`${theme} « EXPEDITION »`, c.l2, bg, 4.5]);
    checks.push([`${theme} « FOR DAVINCI RESOLVE »`, c.l3, bg, 4.5]);
    checks.push([`${theme} coins de visée`, R("frame", theme), bg, 3]);
  }
  // The favicon and the app icon are always drawn with the Nuit colors on a `night-900` tile.
  const tile = R("night-900", "dark");
  for (const name of ["frame", "mark-gold-light", "mark-gold", "mark-blue", "mark-blue-light"]) {
    checks.push([`icône ${name}`, R(name, "dark"), tile, 3]);
  }
  const measured = checks.map(([what, fg, bg, min]) => ({ what, min, ratio: paletteLib.contrast(fg, bg) }));
  const failed = measured.filter((m) => m.ratio < m.min);
  if (failed.length) {
    throw new Error(failed.map((m) => `contrast ${m.what}: ${m.ratio.toFixed(2)}:1 < ${m.min}:1`).join("\n"));
  }
  return `${measured.length} contrast checks passed (lowest ${Math.min(...measured.map((m) => m.ratio)).toFixed(1)}:1)`;
}

async function main() {
  await ensureFonts();
  const { resolve: R } = paletteLib.createResolver(paletteLib.load());

  const symbol = (theme) => ({
    frame: R("frame", theme),
    goldLight: R("mark-gold-light", theme),
    gold: R("mark-gold", theme),
    blue: R("mark-blue", theme),
    blueLight: R("mark-blue-light", theme),
  });
  const lines = (theme) => ({
    l1: R("text", theme),
    l2: R("accent-text", theme),
    l3: R("text-muted", theme),
  });
  console.log(checkContrast(R, lines));

  const out = {};
  const suffix = { dark: "", light: "-light" };
  for (const theme of ["dark", "light"]) {
    const s = suffix[theme];
    const pal = symbol(theme);
    const colors = lines(theme);
    out[`logo/mark${s}.svg`] = logo.svgDoc(logo.markBody(pal), { w: 96, h: 96, vb: "0 0 96 96" });
    out[`logo/lockup${s}.svg`] = logo.lockupH("full", pal, colors);
    out[`logo/lockup-compact${s}.svg`] = logo.lockupH("compact", pal, colors);
    out[`logo/lockup-stacked${s}.svg`] = logo.lockupStacked(pal, colors);
    out[`logo/lockup-ui${s}.svg`] = logo.lockupH("ui", pal, colors);
    out[`logo/wordmark${s}.svg`] = logo.wordmark(colors);
  }
  const mist = R("mist", "dark");
  const ink = R("night-900", "dark");
  out["logo/mark-mono-mist.svg"] = logo.svgDoc(logo.markMonoBody(mist), { w: 96, h: 96, vb: "0 0 96 96" });
  out["logo/mark-mono-ink.svg"] = logo.svgDoc(logo.markMonoBody(ink), { w: 96, h: 96, vb: "0 0 96 96" });
  out["logo/lockup-mono-mist.svg"] = logo.lockupMono(mist);
  out["logo/lockup-mono-ink.svg"] = logo.lockupMono(ink);

  const iconColors = { top: R("night-800", "dark"), bottom: ink, glowGold: R("gold-500", "dark"), glowBlue: R("blue-600", "dark") };
  out["icons/favicon.svg"] = logo.favicon(symbol("dark"), { tile: ink });
  out["icons/app-icon.svg"] = logo.appIcon(symbol("dark"), iconColors);
  // Square corners: the system (maskable icon, iOS) cuts the shape itself.
  out["icons/app-icon-square.svg"] = logo.appIcon(symbol("dark"), { ...iconColors, rx: 0 });

  for (const [rel, svg] of Object.entries(out)) write(path.join(ROOT, rel), svg);

  // What the web interface embeds (frontend/src/components/Logo.tsx, frontend/index.html).
  const app = {
    "lockup-dark.svg": "logo/lockup-ui.svg",
    "lockup-light.svg": "logo/lockup-ui-light.svg",
    "lockup-compact-dark.svg": "logo/lockup-compact.svg",
    "lockup-compact-light.svg": "logo/lockup-compact-light.svg",
  };
  for (const [name, rel] of Object.entries(app)) write(path.join(APP_ASSETS, name), out[rel]);
  write(path.join(APP_PUBLIC, "favicon.svg"), out["icons/favicon.svg"]);

  console.log(`${Object.keys(out).length} SVG files in docs/brand, ${Object.keys(app).length + 1} copies in frontend`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
