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

const { default: sharp } = await import(
  pathToFileURL(join(
    ROOT, "tools", "settlemaker", "node_modules", "sharp", "lib", "index.js",
  )).href
);

const scene = JSON.parse(await readFile(join(sceneDir, "scene.json"), "utf-8"));
const w = Math.round(scene.grids.x * scene.meters_per_grid * scene.pixels_per_grid / scene.meters_per_grid);
const h = Math.round(scene.grids.y * scene.meters_per_grid * scene.pixels_per_grid / scene.meters_per_grid);
const svg = await readFile(join(sceneDir, "scene.svg"));

await sharp(svg, { density: 96 })
  .resize(w, h, { fit: "fill" })
  .png()
  .toFile(join(sceneDir, "scene.png"));

console.log(`scene.png: ${w}x${h} -> ${sceneDir}`);
