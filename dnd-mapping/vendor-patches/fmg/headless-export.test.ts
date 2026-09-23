// Headless world export: runs FMG's full generation pipeline in Node — no
// browser, no app shell. Bypasses components/lifecycle.ts (which drags in
// dialogs and UI tabs) and replicates its five essential lines:
//   Options.setGraphSize; options.map.seed = seed; Math.random = aleaPRNG(seed);
//   Options.randomize(); await GenerationPipeline.run({});
//
// Run explicitly from the FMG repo root:
//   node node_modules/vitest/vitest.mjs run src/generators/headless-export.test.ts
//
// Env overrides: FMG_SEED (default 878636343 = Bonia), FMG_W, FMG_H, FMG_OUT.
// Output matches the "full JSON export" shape src/import_fmg.py already parses:
// {info, settings, pack}.

import { writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { GenerationPipeline } from "@/generators/generation-pipeline";
import { Options } from "@/components/options-model";
// side-effect imports: every generator module attaches its global (Grid,
// Burgs, States, ...) — the pipeline steps reference these globals by name
import "@/generators";

// The app loads small classic-script libraries from public/libs/ that attach
// globals the generators expect (aleaPRNG, FlatQueue, ...). Load them into
// globalThis the same way the app does, instead of stubbing each by hand.
const libsDir = fileURLToPath(new URL("../../public/libs", import.meta.url));
for (const lib of ["alea.min.js", "flatqueue.js", "polylabel.min.js", "simplify.js"]) {
  try {
    let code = readFileSync(join(libsDir, lib), "utf-8");
    // alea.min.js declares `const aleaPRNG = ...` — a const in eval code is
    // scoped to the eval and vanishes; re-export it explicitly
    if (lib === "alea.min.js") code += "\n;globalThis.aleaPRNG = aleaPRNG;";
    (0, eval)(code);
  } catch (e) {
    console.warn(`lib ${lib} did not evaluate headlessly: ${(e as Error).message}`);
  }
}
// Options.randomize() reads ?options= URL params via Pins; no URL in Node
vi.stubGlobal("location", { href: "http://localhost/" });
// Pins.all() reads localStorage for pinned values; none in Node — everything re-rolls per seed
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {}, removeItem: () => {} });
// grid/pack/mapName are ambient globals the app's classic script defines;
// the pipeline assigns them wholesale, so empty objects are enough to start
vi.stubGlobal("grid", {});
vi.stubGlobal("pack", {});
vi.stubGlobal("mapName", "");

const OUT_DIR = process.env.FMG_OUT
  ?? fileURLToPath(new URL("../../../../worlds/raw", import.meta.url));

describe("headless world export", () => {
  it("generates a world and dumps the pack", async () => {
    const seed = process.env.FMG_SEED || "878636343";
    const width = Number(process.env.FMG_W || 1280);
    const height = Number(process.env.FMG_H || 720);

    Options.setGraphSize(width, height);
    options.map.seed = seed;
    Math.random = aleaPRNG(seed);
    Options.randomize();

    await GenerationPipeline.run({});

    const g = globalThis as Record<string, any>;
    const pack = g.pack;
    expect(pack).toBeTruthy();
    expect(pack.burgs?.length).toBeGreaterThan(0);
    expect(pack.states?.length).toBeGreaterThan(0);

    // typed arrays -> plain arrays so the dump is plain JSON
    const plain = JSON.parse(JSON.stringify(pack, (_k, v) =>
      v instanceof Uint8Array || v instanceof Uint32Array
      || v instanceof Int32Array || v instanceof Float32Array
      || v instanceof Float64Array || v instanceof Uint16Array
        ? Array.from(v)
        : v
    ));

    const dump = {
      info: {
        version: "1.153.1-headless",
        mapName: String(g.mapName ?? "").trim() || `world-${seed}`,
        seed,
        width,
        height,
        generator: "node (headless GenerationPipeline)",
      },
      settings: { seed, graph: { width, height, points: options.map.graph?.points ?? 10000 } },
      pack: plain,
    };

    mkdirSync(OUT_DIR, { recursive: true });
    const file = `${OUT_DIR}/headless-${seed}.json`;
    writeFileSync(file, JSON.stringify(dump));
    console.log(`headless export: ${file} (${pack.burgs.length} burgs, ${pack.states.length} states)`);
  }, 600_000);
});
