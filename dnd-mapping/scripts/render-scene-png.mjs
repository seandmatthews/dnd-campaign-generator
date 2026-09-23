// Rasterize a battlemap scene SVG to an exact-size PNG (the UVTT image).
// Usage: node scripts/render-scene-png.mjs <scene-dir>
//   <scene-dir> is relative to the project root (worlds/...), e.g.:
//     node scripts/render-scene-png.mjs worlds/sites/marsh-gang-camp
//     node scripts/render-scene-png.mjs worlds/ruravaqan/scenes/harbour-quay
// Reads <scene-dir>/scene.json + scene.svg, writes <scene-dir>/scene.png.

import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const sceneDir = join(ROOT, process.argv[2] || "worlds/ruravaqan/scenes/harbour-quay");

// Tracked pin: tracked scene.png/.uvtt bytes depend on the rasterizer's
// exact version, and the tool tree (where sharp lives) is git-ignored —
// without this, a future pnpm install could change every rasterized byte
// silently. Bump the pin only together with re-rendering the scenes.
const PINNED_SHARP = "0.33.5";

const { default: sharp } = await import(
  pathToFileURL(join(
    ROOT, "tools", "settlemaker", "node_modules", "sharp", "lib", "index.js",
  )).href
);

// sharp itself does not export its package version — read it from the
// module's own package.json (same directory the import above resolved)
const sharpVersion = JSON.parse(await readFile(
  join(ROOT, "tools", "settlemaker", "node_modules", "sharp", "package.json"),
  "utf-8",
)).version;

if (sharpVersion !== PINNED_SHARP) {
  console.error(
    `sharp ${sharpVersion} is installed but ${PINNED_SHARP} is pinned — ` +
    `rasterized bytes would not reproduce. Fix with: ` +
    `cd tools/settlemaker && pnpm add sharp@${PINNED_SHARP} ` +
    `(or bump the pin in scripts/render-scene-png.mjs and re-render the scenes).`,
  );
  process.exit(1);
}

const scene = JSON.parse(await readFile(join(sceneDir, "scene.json"), "utf-8"));
const w = Math.round(scene.grids.x * scene.pixels_per_grid);
const h = Math.round(scene.grids.y * scene.pixels_per_grid);
const svg = await readFile(join(sceneDir, "scene.svg"));

// the SVG must already BE the target size: resize-with-fill would silently
// distort a stale scene.svg instead of failing here (dimensions may be
// floats — "3220.0" — compare numerically)
const intrinsic = /width="([\d.]+)" height="([\d.]+)"/.exec(svg.toString("utf-8"));
if (!intrinsic) {
  console.error(`scene.svg has no width/height attributes — re-render it`);
  process.exit(1);
}
const [sw, sh] = [Number(intrinsic[1]), Number(intrinsic[2])];
if (Math.abs(sw - w) > 1 || Math.abs(sh - h) > 1) {
  console.error(
    `scene.svg is ${sw}x${sh} but the scene resolves to ${w}x${h} — ` +
    `re-render scene.svg (battlemap.py render --dir <dir>) before rasterizing`);
  process.exit(1);
}

await sharp(svg, { density: 96 })
  .resize(w, h, { fit: "fill" })
  .png()
  .toFile(join(sceneDir, "scene.png"));

console.log(`scene.png: ${w}x${h} -> ${sceneDir}`);
