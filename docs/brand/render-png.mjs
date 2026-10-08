#!/usr/bin/env node
// Renders the PNG files of the logo from the SVGs that `build.cjs` writes: the launcher icons
// (icons/), the icon of the Windows Start menu shortcut (icons/app-icon.ico) and the overview
// sheet (apercu.png, shown in README.md). Uses the system Microsoft Edge through the Playwright
// of the frontend.
//
//   node build.cjs && node render-png.mjs
import { readFileSync, writeFileSync } from "node:fs";
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

// The sizes of the .ico, each from the drawing made for it: the favicon below 32 px.
const icoSizes = [
  ["icons/favicon.svg", 16],
  ["icons/favicon.svg", 24],
  ["icons/app-icon.svg", 32],
  ["icons/app-icon.svg", 48],
  ["icons/app-icon.svg", 64],
  ["icons/app-icon.svg", 256],
];

/** An .ico file made of PNG images, which Windows reads since Vista. */
function ico(images) {
  const head = Buffer.alloc(6 + 16 * images.length);
  head.writeUInt16LE(1, 2); // type: icon
  head.writeUInt16LE(images.length, 4);
  let offset = head.length;
  images.forEach(([size, png], i) => {
    const entry = 6 + 16 * i;
    head.writeUInt8(size % 256, entry); // 0 means 256
    head.writeUInt8(size % 256, entry + 1);
    head.writeUInt16LE(1, entry + 4); // colour planes
    head.writeUInt16LE(32, entry + 6); // bits per pixel
    head.writeUInt32LE(png.length, entry + 8);
    head.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  });
  return Buffer.concat([head, ...images.map(([, png]) => png)]);
}

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

/** The SVG `source` as a PNG of `size` px (written to `target` when given). */
async function render(source, size, transparent, target) {
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  // The SVG is inlined: a page set from a string cannot load file:// images.
  const svg = read(source).replace(/^<svg /, `<svg style="display:block;width:${size}px;height:${size}px" `);
  await page.setContent(`<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}</style>${svg}`, { waitUntil: "load" });
  const png = await page.screenshot({ path: target && join(here, target), omitBackground: transparent });
  await page.close();
  return png;
}

for (const [source, target, size, transparent] of icons) {
  await render(source, size, transparent, target);
  console.log(`${target} (${size}×${size})`);
}

const images = [];
for (const [source, size] of icoSizes) images.push([size, await render(source, size, true)]);
writeFileSync(join(here, "icons/app-icon.ico"), ico(images));
console.log(`icons/app-icon.ico (${icoSizes.map(([, size]) => size).join(", ")})`);

const sheet = await browser.newPage({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 1 });
await sheet.setContent(overview(), { waitUntil: "load" });
await sheet.waitForFunction(() => Array.from(document.images).every((image) => image.complete && image.naturalWidth > 0));
await sheet.screenshot({ path: join(here, "apercu.png"), fullPage: true });
await sheet.close();
console.log("apercu.png");

await browser.close();
