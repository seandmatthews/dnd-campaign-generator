// gen-city.mjs — generate any burg of an FMG world with settlemaker.
//
// Generalizes the removed one-off gen-ruravaqan.mjs: everything is DERIVED
// from a world dump (the same data the atlas comes from):
//
//   burg record   read from the dump by name or index (`--burg`)
//   roadBearings  neighbor burgs from the dump's own route geometry (see
//                 deriveRoadBearings); sorted by bearing, capped at 4.
//   harbourSize   'large' whenever the dump's port flag is set (otherwise
//                 no piers).
//   oceanBearing  bearing to nearest open-ocean cell (JSON dumps); .map
//                 saves assume due south, flagged in params.yaml.
//
// CLI (this exact contract is what the studio invokes, see studio/API.md):
//
//   node scripts/gen-city.mjs --world <FMG world dump> --burg "<name|index>" --out <outdir>
//
// `--world` accepts what import_fmg.py accepts: an FMG full JSON export, a
// headless JSON dump, or a .map save.

import { writeFile, mkdir, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

// settlemaker_version is read from the CHECKOUT at run time (ticket 60):
// the pinned literal kept claiming 3.0.1 after any change under tools/.
// The tool tree's package.json is the record; "unknown" is honest when the
// tree cannot be read.
const readSettlemakerVersion = async () => {
  try {
    const pkg = JSON.parse(
      await readFile(join(ROOT, "tools", "settlemaker", "package.json"), "utf8"),
    );
    if (pkg && typeof pkg.version === "string" && pkg.version) return pkg.version;
  } catch {
    /* fall through */
  }
  return "unknown";
};

// settlemaker never imports more than 4 approaches in the worked example and
// decays extras (RANK_DECAY 0.55/rank), so 4 is the cap. Sorted by bearing
// first so the cut is deterministic.
const MAX_ROAD_BEARINGS = 4;

// Trail neighbors at or above this population (thousands) get class 'town',
// smaller ones 'local'. Roads and searoutes are 'main'. Feeds settlemaker's
// road-width hierarchy.
const TOWN_CLASS_POP = 10;

const usage = (code) => {
  // errors exit nonzero and print to stderr; --help prints to stdout
  const emit = code === 0 ? console.log : console.error;
  emit(
    [
      "Usage: node scripts/gen-city.mjs --world <FMG world dump> --burg <name|index> --out <outdir>",
      "",
      "  --world   FMG world dump: full JSON export, headless dump, or .map save",
      "  --burg    burg name (case-insensitive) or burg index (the dump's `i`)",
      "  --out     output directory (created; <slug>.svg, <slug>.geojson, params.yaml)",
      "  --seed    optional settlemaker seed; default: world seed mod 1e6 (text seeds FNV-1a-hash to 0..999999)",
    ].join("\n"),
  );
  process.exit(code);
};

// --- world dump loading (mirrors src/import_fmg.py's signature approach) ---

// placeholder `0` entries are dropped at load; order is preserved, so
// index == id for the survivors.
const dicts = (list) => (Array.isArray(list) ? list.filter((el) => el && typeof el === "object") : []);

async function loadWorld(path) {
  const text = await readFile(path, "utf8");
  const world = { source: path, cells: null, biomes: [], features: [] };

  if (text.startsWith("{")) {
    // FMG full JSON export or headless dump: {info, settings, pack, ...}
    const data = JSON.parse(text);
    Object.assign(world, {
      seed: String(data.info?.seed ?? ""),
      mapName: data.info?.mapName ?? "",
      version: data.info?.version ?? "",
      burgs: dicts(data.pack?.burgs),
      routes: dicts(data.pack?.routes),
      biomes: dicts(data.pack?.biomes),
      features: dicts(data.pack?.features),
      // raw cell arrays (poles `p`, water type `t`, feature `f`, biome id)
      cells: data.pack?.cells ?? null,
    });
    return world;
  }

  // .map save: pipe-separated header | settings JSON | ... positional
  // sections embedded among SVG lines. Sections are found by content
  // signature (unique keys), never by position — see import_fmg.py.
  // CR-tolerant: a .map round-tripped through a Windows editor carries \r\n
  const lines = text.split(/\r?\n/);
  const header = lines[0].split("|");
  let settings = {};
  try {
    settings = JSON.parse(lines[1]);
  } catch {
    /* header-only info is enough to proceed */
  }

  const section = (signature) => {
    for (let i = 2; i < lines.length; i++) {
      const ln = lines[i];
      if (!ln.includes(signature)) continue;
      try {
        const parsed = JSON.parse(ln);
        if (Array.isArray(parsed)) return parsed;
      } catch {
        /* not the data line (e.g. quoted key inside settings-like text) */
      }
    }
    return null;
  };
  // arrays whose members carry `signature` on the objects themselves —
  // survives sections whose one-line JSON blob mentions the signature text
  // only in passing
  const sectionWhere = (pred) => {
    for (let i = 2; i < lines.length; i++) {
      if (!lines[i].startsWith("[")) continue;
      try {
        const parsed = JSON.parse(lines[i]);
        if (Array.isArray(parsed) && parsed.some((el) => el && typeof el === "object" && pred(el)))
          return parsed;
      } catch {
        /* not a data line */
      }
    }
    return null;
  };

  world.seed = String(settings?.seed ?? header[3] ?? "");
  world.mapName = settings?.lore?.name ?? "";
  world.version = header[0] ?? "";
  // burgs: only the burgs array carries a literal `"port":` key per burg
  world.burgs = dicts(section('"port":'));
  // routes: the one array whose members carry BOTH a route `group` and a
  // `points` list — `"group":` alone also matches burgs (capital/…), and
  // `"points":` alone also matches the rulers section
  world.routes = dicts(
    sectionWhere((el) => typeof el.group === "string" && Array.isArray(el.points)),
  );
  world.biomes = dicts(section('"habitability"'));
  // .map saves carry no pack.cells arrays (positional, unsigned columns —
  // deliberately not parsed); oceanBearing falls back to the assumed south.
  world.cells = null;
  return world;
}

// --- burg selection ---

function findBurg(burgs, arg) {
  const wanted = String(arg).trim();
  const byId = /^\d+$/.test(wanted) ? burgs.find((b) => b.i === Number(wanted)) : null;
  if (byId) return byId;
  const byName = burgs.filter((b) => typeof b.name === "string"
      && b.name.toLowerCase() === wanted.toLowerCase());
  if (byName.length === 1) return byName[0];
  if (byName.length > 1) {
    console.error(`burg "${wanted}": ambiguous, ${byName.length} burgs share the name (ids ${byName.map((b) => b.i).join(", ")}) — pass the index`);
    process.exit(2);
  }
  const suggestions = burgs
    .filter((b) => b.name?.toLowerCase().includes(wanted.toLowerCase()))
    .slice(0, 8)
    .map((b) => `${b.name} (i=${b.i})`);
  console.error(`burg "${wanted}" not found in dump.${suggestions.length ? " similar: " + suggestions.join(", ") : ""}`);
  process.exit(2);
}

// --- derivations ---

/** Compass bearing from `a` to `b` in degrees 0..359; FMG maps are y-down,
 * so north is -y: bearing = atan2(dx, -dy). Matches the removed
 * hand-computed script's values (Mish 263°, Natabashe 83°). */
function bearingDeg(fromX, fromY, toX, toY) {
  return Math.round(((Math.atan2(toX - fromX, -(toY - fromY)) * 180) / Math.PI + 360) % 360) % 360;
}

const routeClass = (group, neighborPop) => {
  if (group === "trails") return neighborPop >= TOWN_CLASS_POP ? "town" : "local";
  return "main"; // roads, searoutes
};
const CLASS_RANK = { main: 0, town: 1, local: 2 };

const routeId = (route) => {
  const name = route.name;
  const slug = name && name !== "undefined" ? slugify(name) : "";
  return slug || `${route.group}-${route.i}`;
};

/**
 * Road-connected neighbors of `burg`: for every route (roads/trails/searoutes)
 * whose geometry passes through the burg's cell, the next DIFFERENT burg on
 * each side is a route neighbor. This is exactly how FMG builds its network
 * (routes run burg-anchor to burg-anchor; `getCellAnchor` snaps in-burg cells
 * to the burg's own x/y), so on a freshly generated world it reduces to plain
 * route-graph adjacency — and it still finds approaches when the burg sits
 * mid-route rather than at a route end (coastal road chains, searoutes).
 *
 * @returns {Array<object>} settlemaker road-bearing entries enriched with
 *   `neighbor`/`neighborPop` provenance fields (stripped before the
 *   generateSettlement call, kept for params.yaml).
 */
function deriveRoadBearings(burg, burgs, routes, { includeSearoutes }) {
  const cellToBurg = new Map(burgs.map((b) => [b.cell, b]));
  const found = new Map(); // neighbor burg i -> entry (best class wins)
  for (const route of routes) {
    if (includeSearoutes === false && route.group === "searoutes") continue;
    const points = route.points ?? [];
    // indices where the route is anchored at the target burg
    const touches = [];
    points.forEach((p, idx) => {
      const b = cellToBurg.get(p[2]);
      if (b && b.i === burg.i) touches.push(idx);
    });
    if (!touches.length) continue;
    // walk outward from each side of the touched span to the next burg
    for (const [start, dir] of [
      [touches[0], -1],
      [touches[touches.length - 1], +1],
    ]) {
      for (let i = start + dir; i >= 0 && i < points.length; i += dir) {
        const b = cellToBurg.get(points[i][2]);
        if (!b || b.i === burg.i) continue;
        const entry = {
          bearing_deg: bearingDeg(burg.x, burg.y, b.x, b.y),
          kind: routeClass(route.group, b.population ?? 0),
          route_id: routeId(route),
          neighbor: b.name,
          neighborPop: Math.round((b.population ?? 0) * 1000),
        };
        const prev = found.get(b.i);
        if (!prev || CLASS_RANK[entry.kind] < CLASS_RANK[prev.kind]) found.set(b.i, entry);
        break;
      }
    }
  }
  return [...found.values()]
    // codepoint compare, not localeCompare: collation follows the runtime's
    // ICU/locale, so the same dump+seed could order ties (and cut different
    // roads) on a different machine
    .sort((a, b) => a.bearing_deg - b.bearing_deg
      || (a.route_id < b.route_id ? -1 : a.route_id > b.route_id ? 1 : 0))
    .slice(0, MAX_ROAD_BEARINGS);
}

/**
 * Bearing to the nearest open-ocean cell (JSON dumps only: pack.cells.p
 * poles, pack.cells.t water flags, pack.features to exclude lakes). Returns
 * null when the dump has no cell arrays (.map saves) or no ocean found.
 */
function deriveOceanBearing(world, burg) {
  const t = world.cells?.t;
  const p = world.cells?.p;
  if (!t || !p || t[burg.cell] === undefined) return null;
  const lakes = new Set(
    world.features.filter((f) => f.type === "lake").map((f) => f.i),
  );
  let best = null;
  // cells.t may be an array OR a typed array serialized as a plain object
  // with numeric keys (t.length undefined) — iterate whichever it is.
  const cellIds = Array.isArray(t) ? t.keys() : Object.keys(t).map(Number);
  for (const c of cellIds) {
    if (t[c] !== -1) continue; // land
    const f = world.cells.f?.[c];
    if (f !== undefined && lakes.has(f)) continue; // lakes are not the ocean
    const pole = p[c];
    if (!pole) continue;
    const d = Math.hypot(pole[0] - burg.x, pole[1] - burg.y);
    if (d > 0.5 && (!best || d < best.d)) best = { d, pole };
  }
  if (!best) return null;
  return { bearing: bearingDeg(burg.x, burg.y, best.pole[0], best.pole[1]), distance: Math.round(best.d * 10) / 10 };
}

/** Burg biome: cell's biome name from JSON dumps; port fallback 'coastal'
 * (what the worked example used); otherwise omitted → settlemaker's
 * temperate default. */
function deriveBiome(world, burg, isPort) {
  const biomeId = world.cells?.biome?.[burg.cell];
  const name = world.biomes.find((b) => b.i === biomeId)?.name;
  if (name && name.toLowerCase() !== "marine") return name;
  return isPort ? "coastal" : null;
}

// --- output helpers ---

function slugify(name) {
  const s = String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return s || "unnamed";
}

const yamlStr = (v) => JSON.stringify(String(v));

function paramsYaml({ world, burg, record, roadEntries, seed, result, oceanSource, settlemakerVersion }) {
  return (
    [
      "# Provenance record — regenerate with scripts/gen-city.mjs, never hand-edit outputs.",
      `generator: scripts/gen-city.mjs`,
      `source_map: ${yamlStr(world.source)}`,
      `world_map_name: ${yamlStr(world.mapName)}`,
      `fmg_version: ${yamlStr(world.version)}`,
      `source_seed: ${yamlStr(world.seed)}`,
      `settlemaker_version: ${yamlStr(settlemakerVersion)}`,
      `seed: ${seed}`,
      `name: ${yamlStr(burg.name)}`, // the studio Maps tab reads name|label first
      `burg_name: ${yamlStr(burg.name)}`,
      `burg_id: ${burg.i}`,
      `burg_input:`,
      ...Object.entries(record).map(([k, v]) => `  ${k}: ${JSON.stringify(v)}`),
      `derivation:`,
      `  road_bearing_method: >-`,
      `    neighbor burgs taken from the dump's own route geometry: every road,`,
      `    trail or searoute touching the burg's cell contributes the next burg on`,
      `    each side; bearing = atan2(dx, -dy) compass angle; sorted by bearing,`,
      `    capped at ${MAX_ROAD_BEARINGS}. kinds: roads/searoutes=main, trails by`,
      `    neighbor population (>=${TOWN_CLASS_POP}k=town, else local).`,
      `  road_bearings:`,
      ...roadEntries.map(
        (r) =>
          `    - {bearing_deg: ${r.bearing_deg}, kind: ${r.kind}, route_id: ${yamlStr(r.route_id)}, neighbor: ${yamlStr(r.neighbor)}, neighbor_pop: ${r.neighborPop}}`,
      ),
      `  ocean_bearing: ${record.oceanBearing ?? "null"}`,
      `  ocean_bearing_source: ${yamlStr(oceanSource)}`,
      `  harbour: ${record.port ? yamlStr(record.harbourSize) : "none (not a port)"}`,
      ``,
      `kind: ${result.kind}`,
      `degraded_flags: ${JSON.stringify(result.degradedFlags)}`,
    ].join("\n") + "\n"
  );
}

// --- main ---

const args = process.argv.slice(2);
const opt = {};
for (let i = 0; i < args.length; i++) {
  const k = args[i];
  if (k === "--help" || k === "-h") usage(0);
  if (!k.startsWith("--") || args[i + 1] === undefined || args[i + 1].startsWith("--")) usage(2);
  opt[k.slice(2)] = args[++i];
}
for (const required of ["world", "burg", "out"]) {
  if (!opt[required]) {
    console.error(`missing --${required}`);
    usage(2);
  }
}

const world = await loadWorld(opt.world);
if (!world.burgs.length) {
  console.error(`no burgs found in ${opt.world} — is this an FMG dump?`);
  process.exit(1);
}
const burg = findBurg(world.burgs, opt.burg);
if (!Number.isFinite(burg.x) || !Number.isFinite(burg.y)) {
  console.error(`burg ${JSON.stringify(burg.name ?? burg.i)} has no valid coordinates in the dump - cannot derive a position`);
  process.exit(2);
}

const isPort = Boolean(burg.port);
const population = Math.round((burg.population ?? 0) * 1000); // dump is in thousands
const roadEntries = deriveRoadBearings(burg, world.burgs, world.routes, {
  includeSearoutes: isPort, // a searoute approach is only a quay for a port;
}); // a portless coastal burg would grow a land road into the sea
// settlemaker speaks {bearing_deg, kind, route_id} — the neighbor fields are
// provenance only and are stripped here.
const roadBearings = roadEntries.map(({ bearing_deg, kind, route_id }) => ({
  bearing_deg,
  kind,
  route_id,
}));
const ocean = deriveOceanBearing(world, burg);
const oceanBearing = ocean ? ocean.bearing : 180;
const oceanSource = ocean
  ? `derived: bearing ${ocean.bearing}° to nearest open-ocean cell, ${ocean.distance}px away`
  : "assumed: 180 (south) — dump carries no cell-level water data";

const biome = deriveBiome(world, burg, isPort);
const record = {
  name: burg.name,
  population,
  ...(biome ? { biome } : {}),
  port: isPort,
  citadel: Boolean(burg.citadel),
  walls: Boolean(burg.walls),
  plaza: Boolean(burg.plaza),
  temple: Boolean(burg.temple),
  shanty: Boolean(burg.shanty),
  capital: Boolean(burg.capital),
  ...(population >= 1000 ? { engine: "city" } : {}), // settlemaker auto-switches at 1,000
  ...(roadBearings.length ? { roadBearings } : {}),
  oceanBearing,
  ...(isPort ? { harbourSize: "large" } : {}), // REQUIRED for piers on ports
};

const rawSeed = String(opt.seed !== undefined ? opt.seed : world.seed ?? "").trim();
// Numeric seeds pass through (mod 1e6); text seeds FNV-1a-hash so the same
// world always builds the same city.
const seedFromText = (s) => {
  // FNV-1a 32-bit over UTF-16 code units: deterministic per machine and
  // per world, which is the requirement — but NOT byte-canonical FNV, so a
  // non-ASCII world name will not match a byte-wise FNV in another
  // language. Never reuse this value as a cross-tool identity.
  let h = 2166136261; // FNV-1a 32-bit
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % 1e6;
};
const numeric = Number(rawSeed);
// one derivation for every path: mod 1e6 applies to an explicit numeric
// --seed too, so `--seed <the world's own seed>` reproduces the default
// city instead of silently building a different one
const seed = rawSeed === "" ? NaN
  : Number.isFinite(numeric)
    ? ((numeric % 1e6) + 1e6) % 1e6
    : seedFromText(rawSeed);
if (rawSeed === "" || !Number.isFinite(seed)) {
  console.error(`cannot derive a seed from world seed ${JSON.stringify(world.seed)} — pass --seed`);
  process.exit(2);
}
if (!Number.isFinite(numeric)) {
  console.log(`seed: ${seed} (FNV-1a hashed from ${JSON.stringify(rawSeed)})`);
}

const settlemaker = await import(pathToFileURL(join(ROOT, "tools", "settlemaker", "dist", "index.js")).href);
const result = settlemaker.generateSettlement(record, { seed });

const slug = slugify(burg.name);
await mkdir(opt.out, { recursive: true });
await writeFile(join(opt.out, `${slug}.svg`), result.svg);
await writeFile(join(opt.out, `${slug}.geojson`), JSON.stringify(result.geojson, null, 2));
await writeFile(
  join(opt.out, "params.yaml"),
  paramsYaml({ world, burg, record, roadEntries, seed, result, oceanSource }),
);

console.log(`burg: ${burg.name} (i=${burg.i}) — pop ${population.toLocaleString("en-US")}${isPort ? ", port" : ""}`);
console.log(
  `roads: ${roadEntries.length ? roadEntries.map((r) => `${r.neighbor} ${r.bearing_deg}°(${r.kind}, ${r.route_id})`).join(", ") : "none found in dump"}`,
);
console.log(`oceanBearing: ${oceanBearing} — ${oceanSource}`);
console.log(`kind: ${result.kind}`);
console.log(`degraded flags: ${JSON.stringify(result.degradedFlags)}`);
console.log(`wrote ${opt.out}/${slug}.svg (+ ${slug}.geojson, params.yaml)`);
