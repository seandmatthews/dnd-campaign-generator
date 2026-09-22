// Generate Ruravaqan with settlemaker, from Bonia's own data.
//
// Inputs are derived artifacts of the atlas: the burg record and road
// bearings below were extracted from worlds/raw/candidate-03.map
// (seed 878636343) — see scripts note in README. Regenerate after editing;
// never hand-edit the SVG.
//
// Run from the project root:  node scripts/gen-ruravaqan.mjs

import { writeFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT = join(ROOT, "world", "atlas", "ruravaqan");

const settlemaker = await import(
  pathToFileURL(join(ROOT, "tools", "settlemaker", "dist", "index.js")).href
);

// Ruravaqan as FMG generated it (candidate-03.map, burg record verbatim):
// x 276.8, y 442.9, pop 29,562, port/walls/plaza/temple, no citadel, not capital.
const burg = {
  name: "Ruravaqan",
  population: 29562,
  biome: "coastal",
  port: true,
  citadel: false,
  walls: true,
  plaza: true,
  temple: true,
  shanty: false,
  capital: false,
  engine: "city",
  // roads from bearings to real neighboring burgs in the Bonia export
  roadBearings: [
    { bearing_deg: 263, kind: "main", route_id: "mish-road" },   // Mish, the republic's capital (27px W)
    { bearing_deg: 83, kind: "main", route_id: "natabashe-road" }, // Natabashe, pop 25k (24px E)
    { bearing_deg: 50, kind: "town", route_id: "mabashad-road" }, // Mabashad, pop 20k (29px NE)
    { bearing_deg: 28, kind: "local", route_id: "chogzi-road" },  // Chogzi, pop 6k (15px NNE)
  ],
  oceanBearing: 180, // the sea south of the city (assumption from the Bonia coast)
  harbourSize: "large", // the whole campaign turns on this harbor
};

const SEED = 636343; // derived: world seed 878636343 mod 1e6

const result = settlemaker.generateSettlement(burg, { seed: SEED });

await mkdir(OUT, { recursive: true });
await writeFile(join(OUT, "ruravaqan.svg"), result.svg);
await writeFile(join(OUT, "ruravaqan.geojson"), JSON.stringify(result.geojson, null, 2));
await writeFile(
  join(OUT, "params.yaml"),
  [
    "# Provenance record — regenerate with scripts/gen-ruravaqan.mjs, never hand-edit outputs.",
    "source_map: worlds/raw/candidate-03.map",
    "source_seed: '878636343'",
    "settlemaker_version: 3.0.1",
    `seed: ${SEED}`,
    "burg_input:",
    ...(function* () {
      for (const [k, v] of Object.entries(burg)) yield `  ${k}: ${JSON.stringify(v)}`;
    })(),
    "",
    `kind: ${result.kind}`,
    `degraded_flags: ${JSON.stringify(result.degradedFlags)}`,
  ].join("\n") + "\n",
  { encoding: "utf-8" },
);

console.log(`kind: ${result.kind}`);
console.log(`degraded flags: ${JSON.stringify(result.degradedFlags)}`);
console.log(`wrote ${OUT}/ruravaqan.svg (+ geojson, params)`);
