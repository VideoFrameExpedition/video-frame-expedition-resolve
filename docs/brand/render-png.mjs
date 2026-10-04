#!/usr/bin/env node
// Renders the PNG files of the logo from the SVGs that `build.cjs` writes: the launcher icons
// (icons/) and the overview sheet (apercu.png, shown in README.md). Uses the system Microsoft
// Edge through the Playwright of the frontend.
//
//   node build.cjs && node render-png.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const playwright = join(here, "..", "..", "frontend", "node_modules", "@playwright", "test", "index.mjs");
const { chromium } = await import(pathToFileURL(playwright).href);

const read = (rel) => readFileSync(join(here, rel), "utf8");

// [source SVG, output PNG, size in px, transparent background]
const icons = [
  ["icons/app-icon.svg", "icons/icon-512.png", 512, true],
  ["icons/app-icon.svg", "icons/icon-192.png", 192, true],
  ["icons/app-icon-square.svg", "icons/icon-512-maskable.png", 512, false],
  ["icons/app-icon-square.svg", "icons/apple-touch-icon.png", 180, false],
];

// Each SVG is a separate image: their gradient ids cannot collide as they would if inlined together.
const img = (rel, height) =>
  `<img alt="" style="height:${height}px" src="data:image/svg+xml;base64,${Buffer.from(read(rel)).toString("base64")}">`;
const tile = (theme, caption, content, minHeight) =>
  `<div class="tile ${theme}" style="min-height:${minHeight}px"><div class="row">${content}</div><span class="cap">${caption}</span></div>`;

/** The logo on its backgrounds, in its variants and as icons. */
function overview() {
  const iconRow = img("icons/favicon.svg", 16) + img("icons/favicon.svg", 32) + img("icons/favicon.svg", 64) + img("icons/app-icon.svg", 128);
  const tiles = [
    tile("night", "Logotype · fond Nuit", img("logo/lockup.svg", 112), 190),
    tile("paper", "Logotype · fond Papier", img("logo/lockup-light.svg", 112), 190),
    tile("night", "Symbole · empilé · compact — Nuit", img("logo/mark.svg", 120) + img("logo/lockup-stacked.svg", 170) + img("logo/lockup-compact.svg", 64), 260),
    tile("paper", "Symbole · empilé · compact — Papier", img("logo/mark-light.svg", 120) + img("logo/lockup-stacked-light.svg", 170) + img("logo/lockup-compact-light.svg", 64), 260),
    tile("gold", "Une seule encre : night-900 sur ambre", img("logo/lockup-mono-ink.svg", 100), 170),
    tile("night", "Une seule encre : mist sur Nuit", img("logo/lockup-mono-mist.svg", 100), 170),
    tile("dusk", "Favicon 16 · 32 · 64 px et icône d'application", iconRow, 210),
    tile("paper", "Favicon 16 · 32 · 64 px et icône d'application", iconRow, 210),
  ];
  return `<!doctype html><meta charset="utf-8"><style>
    html, body { margin: 0; }
    body { width: 1200px; padding: 16px; box-sizing: border-box; background: #7d8698; font: 11px/1.3 Consolas, "Cascadia Mono", monospace; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .tile { position: relative; display: flex; align-items: center; justify-content: center; padding: 28px 24px 36px; border-radius: 12px; }
    .row { display: flex; align-items: flex-end; gap: 28px; }
    .night { background: #0a101b; --cap: #98a7c2; }
    .dusk { background: #1b273d; --cap: #98a7c2; }
    .paper { background: #f6f2ea; --cap: #46577a; }
    .gold { background: #ffab2e; --cap: #3a2400; }
    .cap { position: absolute; left: 14px; bottom: 10px; color: var(--cap); letter-spacing: .02em; }
    img { display: block; width: auto; }
  </style><div class="grid">${tiles.join("")}</div>`;
}

const browser = await chromium.launch({ channel: "msedge" });

for (const [source, target, size, transparent] of icons) {
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  // The SVG is inlined: a page set from a string cannot load file:// images.
  const svg = read(source).replace(/^<svg /, `<svg style="display:block;width:${size}px;height:${size}px" `);
  await page.setContent(`<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}</style>${svg}`, { waitUntil: "load" });
  await page.screenshot({ path: join(here, target), omitBackground: transparent });
  await page.close();
  console.log(`${target} (${size}×${size})`);
}

const sheet = await browser.newPage({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 1 });
await sheet.setContent(overview(), { waitUntil: "load" });
await sheet.waitForFunction(() => Array.from(document.images).every((image) => image.complete && image.naturalWidth > 0));
await sheet.screenshot({ path: join(here, "apercu.png"), fullPage: true });
await sheet.close();
console.log("apercu.png");

await browser.close();
