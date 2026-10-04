# The logo of Video Frame Expedition for DaVinci Resolve

**English** · [Français](README.fr.md)

![The logo on its backgrounds, in its variants, and as an icon](apercu.png)

The logo is **drawn by a program**, not by a drawing tool: `build.cjs` writes all the files of
`logo/` and `icons/` from `palette.json` (the colours) and `src/lib/` (the shapes and the text).
To change the logo, the program is changed, never an SVG by hand: the next build would overwrite
the retouch.

## The symbol: "Le Repère" (the marker)

Two opposite **framing corners** — top left and bottom right — around a **diamond with four
facets**. The corners are the frame: what is looked for in the picture. The diamond is the
keyframe, the mark placed on the timeline of an edit: the moment kept.

Its facets are lit from the north-east: the two on the right are **amber** (the golden hour), the
two on the left **blue** (the blue hour) — the two moments that the application notes in each
shot. The lightest one is at the top right, where the light comes from.

The frame is open, on two corners only: an expedition is not finished.

### Construction

Everything is laid out on a grid of **96 × 96**, in units of that grid:

| Measure | Value | |
|---|---|---|
| Inset of the corners | 6 | from the edge of the grid |
| Length of the arms | 34 | each arm of the corner |
| Thickness of the stroke | 9 | |
| Radius of the angle | 13 | on the outside; 4 on the inside |
| Half-diagonal of the diamond | 27 | centred at 48, 48 |

The ends of the arms are **cut at 45°**, in the direction of the diamond's diagonal. The bottom
corner is the top corner rotated by 180°, not a mirror image: the diamond and the corners turn
together around the same centre.

The favicon uses a thicker variant (inset 7, arms 38, stroke 12, radius 15, diamond 28): at
16 px, the original stroke disappears.

## The logotype

Three lines of capitals, in a single typeface, **Epilogue**:

1. **VIDEO FRAME** — Epilogue 400, thin and widely spaced: it breathes;
2. **EXPEDITION** — Epilogue 700, in amber: the word that carries the name;
3. **FOR DAVINCI RESOLVE** — Epilogue 500, small: the machine the application plugs into.

### The block is justified

This is what holds the logotype together. The three lines do not have the width the typeface
would give them: **each one is stretched by its letter-spacing to the width of the widest**, that
is, that of "EXPEDITION". The block therefore forms an exact rectangle next to the symbol, and
not a staircase.

Each line is also aligned on the **actual edge of its ink**, not on the advance width of its
first character: the V of "VIDEO", the E of "EXPEDITION" and the F of "FOR" all start at the same
millimetre. It is an optical alignment, the only one the eye perceives as straight.

The letter-spacing is never tightened, only widened: the letters do not touch.

The text of the SVG files is **converted to outlines**. The logo is therefore displayed
identically everywhere, without installing a font and without embedding a font file.

## The files

The reference file is `logo/lockup.svg`, on a dark background. Each name without a suffix is the
**Nuit** version (night, dark background); the `-light` suffix is the **Papier** version (paper,
light background).

| File | When to use it |
|---|---|
| `logo/lockup.svg` | The full logo, with the three lines. Reference version: README, documents, header of a page. |
| `logo/lockup-compact.svg` | The short name, on two lines, when "for DaVinci Resolve" is already written next to it or when there is not enough room in height. |
| `logo/lockup-ui.svg` | The full logo whose third line is enlarged and less widely spaced, to remain legible below 56 px in height. It is the one in the application's sidebar. |
| `logo/lockup-stacked.svg` | The symbol above the centred name: square format, poster, welcome screen. |
| `logo/wordmark.svg` | The name alone, when the symbol is already present elsewhere on the page. |
| `logo/mark.svg` | The symbol alone: avatar, thumbnail, stamp, bullet. |
| `logo/lockup-mono-ink.svg`, `logo/mark-mono-ink.svg` | A single ink, `night-900`, on amber or any bright colour. The facets keep their relief through their opacity. |
| `logo/lockup-mono-mist.svg`, `logo/mark-mono-mist.svg` | A single ink, `mist`, on a dark background: engraving, embroidery, screen printing. |
| `icons/favicon.svg` | Browser tab: the thick symbol on a `night-900` tile, from 16 px. |
| `icons/app-icon.svg`, `icons/icon-512.png`, `icons/icon-192.png` | Application icon, rounded corners, amber and blue glows. |
| `icons/app-icon-square.svg`, `icons/icon-512-maskable.png`, `icons/apple-touch-icon.png` | The same, with square corners: it is the system that cuts out the shape. |

The web interface receives its copies at build time: `lockup-ui` and `lockup-compact` in
`frontend/src/assets/brand/` (both themes), and the favicon in `frontend/public/`. Do not modify
them there: they are rewritten.

## The rules

- **Clear space**: leave around the logo an empty space equal to a **quarter of the height of the
  symbol**. No text, no image edge, no other mark enters it.
- **Minimum sizes**: symbol alone, **16 px**; `lockup-ui`, **32 px** high; full `lockup`,
  **200 px** wide (40 mm in print). Below 56 px in height, take `lockup-ui` and not `lockup`.
- **On a photograph**: first lay a dark scrim (`#0a101bcc`), then the Nuit version.
- **Do not**: rotate, slant, stretch, squash, change the colours, add a shadow, an outline or a
  glow, close the frame on four corners, reset the name in another typeface or with another
  letter-spacing, or separate the symbol from the text within a single lockup.

## The colours

They are described one by one, with their use, in **`palette.json`**. In summary:

| Role | Nuit | Papier |
|---|---|---|
| Background | `night-900` `#0a101b` | `paper` `#f6f2ea` |
| "VIDEO FRAME" and framing corners | `mist` `#eaf0fa` | `night-900` `#0a101b` |
| "EXPEDITION" | `gold-500` `#ffab2e` | `gold-700` `#a85a00` |
| "FOR DAVINCI RESOLVE" | `night-300` `#98a7c2` | `night-500` `#46577a` |
| Amber facets of the diamond | `gold-300`, `gold-500` | `gold-400`, `gold-600` |
| Blue facets of the diamond | `blue-400`, `blue-600` | `blue-500`, `blue-700` |

The build **fails** if a contrast falls below the threshold: 4.5:1 for each line of text on its
background, 3:1 for the framing corners and for each facet on the icon's tile. The lowest today
is 3.1:1 (the `blue-600` facet on `night-900`, a decorative flat fill).

## Rebuilding

```sh
cd docs/brand
npm install --no-save opentype.js   # once: used to convert the text to outlines
node build.cjs                      # the SVGs of logo/ and icons/, and the interface's copies
node render-png.mjs                 # the PNGs of the icons and apercu.png (Microsoft Edge)
```

`build.cjs` downloads the three weights of Epilogue into `.cache/fonts` (not versioned) on its
first run.
`render-png.mjs` borrows the Playwright of the `frontend` folder and the system's Microsoft Edge.

## The typeface

A single one: **[Epilogue](https://github.com/etunni/epilogue)** (Etienne Aubert Bonn, Tunera
Type Foundry), in three weights — 400, 500 and 700. It is under the **SIL Open Font License
1.1**, which allows the logotype to be converted to outlines, and it is taken from the
[`@fontsource/epilogue`](https://fontsource.org/fonts/epilogue) package.

One typeface, not three: the logotype holds by the structure of the block and by the contrast of
the weights, not by mixing typefaces.

## Notices

"DaVinci Resolve" and "Blackmagic Design" are trademarks of Blackmagic Design Pty Ltd. This logo
is that of an independent application, which is neither published, nor endorsed, nor supported by
Blackmagic Design.
