#!/usr/bin/env python3
"""assemble.py — build campaigns/ruravaqan/ from the three engine projects.

M0 one-time import (campaign-studio-design.md §M0, decision D4): the studio
never references the engines' live trees; it copies artifacts in, so a
campaign folder is self-contained and portable by zip.

Sources (all READ-ONLY — this script never writes into an engine project):
  agentic-simulator/world/state.yaml      faction sandbox state (canonical)
  agentic-simulator/chronicle/000N-*.md   weekly chronicles
  dnd-mapping/worlds/raw/*<seed>.(json|map)  FMG genesis record
  dnd-mapping/worlds/candidates/<world_source from state atlas>  geography
  dnd-mapping/worlds/ruravaqan/           settlemaker city svg/geojson/params
  verification/lexicons/canon-terms.txt   canonical spellings

Usage:
  python studio/assemble.py [--root PATH] [--check]

  --root   studio root override (default: the parent of this file's dir)
  --check  do not build; validate that the assembled campaign exists and
           every produced .yaml parses (yaml.safe_load)

Stdlib + PyYAML only. Idempotent: every run rewrites its own outputs.
Exit status 2 if a required source is missing, 1 on verification failure.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

CAMPAIGN_NAME = "ruravaqan"
CAMPAIGN_TITLE = "Ruravaqan"
WORLD_NAME = "bonia"
SEED = "878636343"
CREATED = "2026-09-22"

# Relative engine pointers recorded in campaign.yaml (informational
# provenance per the task spec; M0 reads only the copied artifacts).
ENGINE_POINTERS = {
    "mapping": "../dnd-mapping",
    "simulator": "../agentic-simulator",
    "verification": "../verification",
}

SIM_DIR = "agentic-simulator"
MAP_DIR = "dnd-mapping"
VER_DIR = "verification"

REQUIRED_CHRONICLE_WEEKS = (1, 2, 3, 4)


# --------------------------------------------------------------------------
# console hygiene: the parent path and file contents are UTF-8; the Windows
# console may not be.


def _force_utf8_stdout():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


# --------------------------------------------------------------------------
# source discovery


def collect_sources(root):
    """Resolve and validate every source artifact. Exits nonzero, with a
    clear message per missing file, before anything is written."""
    errors = []
    notes = []
    sim = root / SIM_DIR
    mapping = root / MAP_DIR
    verification = root / VER_DIR

    s = {"root": root, "sim": sim, "mapping": mapping}

    # --- state.yaml (canonical world state) --------------------------------
    state_path = sim / "world" / "state.yaml"
    if not state_path.is_file():
        errors.append(f"missing required source: {state_path}")
        s["state"] = None
    else:
        s["state_path"] = state_path
        s["state"] = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        if not isinstance(s["state"], dict) or "factions" not in s["state"]:
            errors.append(f"not a faction-state file (no 'factions'): {state_path}")

    # --- chronicles ---------------------------------------------------------
    chron_dir = sim / "chronicle"
    chronicles = {}
    if not chron_dir.is_dir():
        errors.append(f"missing required directory: {chron_dir}")
    else:
        for p in sorted(chron_dir.glob("*.md")):
            # the week is the LEADING number (0003-the-ash-crossing.md -> 3);
            # joining every digit would read a rerun like 0003-x-2nd.md as 32
            m = re.match(r"(\d+)", p.stem)
            if m:
                chronicles[int(m.group(1))] = p
    for week in REQUIRED_CHRONICLE_WEEKS:
        if week not in chronicles:
            errors.append(
                f"missing required chronicle for week {week} "
                f"(no 000{week}-*.md under {chron_dir})"
            )
    s["chronicles"] = chronicles

    # --- geography: the bonia import named by the state's atlas provenance --
    atlas = (s.get("state") or {}).get("meta", {}).get("atlas", {})
    world_source = atlas.get("world_source")
    if not world_source:
        errors.append(
            "state.yaml meta.atlas.world_source is absent — cannot locate the "
            "geography import"
        )
        s["geography_path"] = None
        s["geography"] = None
    else:
        geo_path = mapping / world_source
        if not geo_path.is_file():
            errors.append(f"missing required source: {geo_path} (from atlas)")
            s["geography_path"] = None
            s["geography"] = None
        else:
            s["geography_path"] = geo_path
            geo = yaml.safe_load(geo_path.read_text(encoding="utf-8"))
            # Task: if the import is already geography-shaped it is copied
            # through verbatim. import_fmg output is {meta, summary, nations,
            # cultures, major_settlements} — verify before trusting.
            if not isinstance(geo, dict) or "nations" not in geo:
                errors.append(
                    f"not geography-shaped (no 'nations' key): {geo_path}"
                )
                s["geography"] = None
            else:
                s["geography"] = geo

    # --- raw FMG dump for the seed ------------------------------------------
    raw_dir = mapping / "worlds" / "raw"
    dump = None
    if not raw_dir.is_dir():
        errors.append(f"missing required directory: {raw_dir}")
    else:
        exact = raw_dir / f"headless-{SEED}.json"
        if exact.is_file():
            dump = exact
        else:
            candidates = [
                p
                for p in sorted(raw_dir.iterdir())
                if p.is_file() and SEED in p.name and p.suffix in (".json", ".map")
            ]
            if candidates:
                dump = candidates[0]
    if dump is None:
        errors.append(
            f"no raw FMG dump for seed {SEED} (.json/.map) under {raw_dir}"
        )
    s["source_dump"] = dump

    # --- settlemaker city outputs -------------------------------------------
    city_dir = mapping / "worlds" / "ruravaqan"
    city_svg = city_dir / "ruravaqan.svg"
    city_geojson = city_dir / "ruravaqan.geojson"
    city_params = city_dir / "params.yaml"
    for required in (city_svg, city_geojson):
        if not required.is_file():
            errors.append(f"missing required source: {required}")
    s["city_dir"] = city_dir
    s["city_svg"] = city_svg
    s["city_geojson"] = city_geojson
    s["city_params"] = city_params if city_params.is_file() else None
    s["city_params_data"] = (
        yaml.safe_load(city_params.read_text(encoding="utf-8"))
        if s["city_params"] is not None
        else None
    )
    if s["city_params"] is None:
        notes.append("city params.yaml not found; a minimal provenance note "
                     "will be written instead")

    # --- optional exported world-map svg ------------------------------------
    # Search worlds/ for an exported world svg matching the seed; the city
    # map, site scenes, raw and candidates trees do not count.
    world_map = None
    if (mapping / "worlds").is_dir():
        found = [
            p
            for p in sorted((mapping / "worlds").rglob("*.svg"))
            if not ({"ruravaqan", "sites", "raw", "candidates"} & set(p.parts))
        ]
        seeded = [p for p in found if SEED in p.name]
        if len(seeded) == 1:
            world_map = seeded[0]
        elif len(found) == 1:
            world_map = found[0]
        elif found:
            notes.append(
                "several candidate world svgs, none named for seed "
                f"{SEED}; skipping world/map.svg ({', '.join(p.name for p in found)})"
            )
    s["world_map_svg"] = world_map
    if world_map is None and not any("world svg" in n for n in notes):
        notes.append("no exported FMG world svg found in dnd-mapping/worlds; "
                     "world/map.svg skipped (the FMG pipeline was NOT run)")

    # --- canon lexicon -------------------------------------------------------
    canon_path = verification / "lexicons" / "canon-terms.txt"
    if not canon_path.is_file():
        errors.append(f"missing required source: {canon_path}")
        s["canon_terms"] = []
    else:
        s["canon_terms"] = _read_name_lines(canon_path)

    if errors:
        print("assemble: required source(s) missing — nothing was built:",
              file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(2)

    for n in notes:
        print(f"assemble: note: {n}")
    return s


def _read_name_lines(path):
    """Non-comment, non-blank lines of a lexicon/name file."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# --------------------------------------------------------------------------
# area builders — each returns a summary row (target, action, detail)


def build_campaign_yaml(camp, state):
    premise = (state.get("meta", {}) or {}).get("premise", "").strip()
    if not premise:
        sys.exit("assemble: state.yaml has no meta.premise — refusing to "
                 "write campaign.yaml without it")
    data = {
        "name": CAMPAIGN_NAME,
        "title": CAMPAIGN_TITLE,
        "world": WORLD_NAME,
        "seed": SEED,
        "created": CREATED,
        "premise": premise,
        "engines": dict(ENGINE_POINTERS),
    }
    dest = camp / "campaign.yaml"
    _write_yaml(dest, data)
    return ("campaign.yaml", "built", "premise from state meta")


def build_world(camp, s):
    rows = []
    world = camp / "world"
    world.mkdir(parents=True, exist_ok=True)

    src = s["source_dump"]
    dest = world / f"source{src.suffix}"
    shutil.copy2(src, dest)
    rows.append((f"world/{dest.name}", "copied",
                 _rel(s["mapping"], src)))

    shutil.copy2(s["geography_path"], world / "geography.yaml")
    rows.append(("world/geography.yaml", "copied",
                 _rel(s["mapping"], s["geography_path"])
                 + " (already geography-shaped)"))

    if s["world_map_svg"] is not None:
        shutil.copy2(s["world_map_svg"], world / "map.svg")
        rows.append(("world/map.svg", "copied",
                     _rel(s["mapping"], s["world_map_svg"])))
    else:
        rows.append(("world/map.svg", "skipped",
                     "no exported FMG world svg in the engine (pipeline not run)"))
    return rows


# titles: (week, actor) -> one title per event in state order. Dry and
# concrete, 3-6 words. If state grows an event this table lacks, assemble
# fails loudly rather than emitting an untitled or invented entry.
EVENT_TITLES = {
    (1, "world"): ["The Doge dies without an heir"],
    (2, "mint"): ["Council attaches the customs revenue"],
    (2, "reliquary"): ["Funeral procession through the Docks"],
    (2, "bastard"): ["Sera caught in the manor"],
    (2, "longshoremen"): ["Strikebreakers take the estate's cargo"],
    (2, "marsh_gang"): ["Two robbers taken at the procession"],
    (3, "mint"): ["The Mint's debt ranked senior"],
    (3, "reliquary"): ["Interdiction declared from the pulpit"],
    (3, "bastard"): ["Manor theft attempt fails"],
    (3, "longshoremen"): ["The harbor strike holds"],
    (3, "marsh_gang"): ["Storeroom raid repelled"],
    (4, "longshoremen"): ["Customs house picketed shut"],
    (4, "mint"): [
        "First coffers offer refused",
        "Coffers offer refused again",
        "Coffers offer refused a third time",
    ],
    (4, "reliquary"): ["Ferrand shipments impounded under seal"],
    (4, "bastard"): ["Reliquary to examine the instrument"],
    (4, "marsh_gang"): ["The gang scatters into the Tanneries"],
}


def build_timeline(camp, state):
    entries = []
    seen = {}
    for ev in state.get("events", []):
        week, actor = int(ev["week"]), ev["actor"]
        idx = seen.get((week, actor), 0)
        seen[(week, actor)] = idx + 1
        titles = EVENT_TITLES.get((week, actor))
        if not titles or idx >= len(titles):
            sys.exit(
                f"assemble: no hand-written title for event "
                f"(week {week}, actor {actor}, occurrence {idx + 1}) — "
                "add it to EVENT_TITLES; refusing to invent one"
            )
        title = titles[idx]
        if not 3 <= len(title.split()) <= 6:
            sys.exit(f"assemble: title '{title}' is not 3-6 words")
        entries.append(
            {
                "id": f"t{len(entries) + 1:04d}",
                "when": f"week {week}",
                "title": title,
                "text": ev["text"].strip(),
                "source": "authored",
            }
        )
    dest = camp / "lore" / "timeline.yaml"
    _write_yaml(dest, {"entries": entries})
    weeks = sorted({e["when"] for e in entries})
    return ("lore/timeline.yaml", "built",
            f"{len(entries)} entries ({weeks[0]}..{weeks[-1]}, source: authored)")


# lore/entities/ — the vault pages (obsidian-integration.md §1): one
# markdown page per entity, frontmatter carrying the exact fields the old
# entities.yaml had, the note as the opening body. Faction notes are the
# factions' own goal lines from state.yaml; everything else is one line
# backed by the state or the chronicles.
FACTION_ENTITY_IDS = [
    "mint",
    "reliquary",
    "bastard",
    "longshoremen",
    "marsh_gang",
]
FACTION_ALIASES = {
    "mint": ["House Ferrand", "the Mint", "Ferrand"],
    "reliquary": ["the Reliquary", "church of the Interred"],
    "bastard": ["the Bastard", "Sera", "Sera of Ruravaqan"],
    "longshoremen": ["the Longshoremen", "the guild"],
    "marsh_gang": ["the Marsh Gang", "the Gang"],
}

_RESERVED_DEVICES = {"con", "prn", "aux", "nul",
                     *(f"com{i}" for i in range(1, 10)),
                     *(f"lpt{i}" for i in range(1, 10))}


def _page_stem(name: str):
    """Display-name page stem, or None when the filesystem hates it (the
    caller slugs instead). Mirrors spine.entity_page_filename's rules."""
    stem = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", name or "")
    stem = re.sub(r"\s+", " ", stem).strip()
    if (not stem or stem.startswith(".") or stem != stem.rstrip(" .")
            or stem.lower() in _RESERVED_DEVICES or len(stem) > 80):
        return None
    return stem


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "page"


def build_entities(camp, state):
    factions = {f["id"]: f for f in state["factions"]}
    entities = []
    for fid in FACTION_ENTITY_IDS:
        f = factions.get(fid)
        if f is None:
            sys.exit(f"assemble: state.yaml has no faction '{fid}'")
        entities.append(
            {
                "id": fid,
                "kind": f.get("kind") or "unknown",
                "name": f.get("name") or fid,
                "aliases": list(FACTION_ALIASES[fid]),
                "note": (f.get("goal") or "").strip(),
            }
        )
    entities.extend(
        [
            {
                "id": "sera",
                "kind": "person",
                "name": "Sera of Ruravaqan",
                "aliases": ["Sera", "the Bastard"],
                "note": "Claims the Doge's parentage and a seat on the "
                        "estate's council; the face of the Bastard's Claim.",
            },
            {
                "id": "mint-quarter",
                "kind": "place",
                "name": "the Mint quarter",
                "aliases": ["the Mint quarter", "Mint quarter"],
                "note": "One of Ruravaqan's four districts.",
            },
            {
                "id": "reliquary-quarter",
                "kind": "place",
                "name": "the Reliquary",
                "aliases": ["the Reliquary", "the church quarter"],
                "note": "The church quarter of Ruravaqan; its storerooms held "
                        "the funeral wealth the Marsh Gang failed to carry "
                        "off (week 3).",
            },
            {
                "id": "docks",
                "kind": "place",
                "name": "the Docks",
                "aliases": ["the Docks", "the harbor"],
                "note": "The harbor district — the funeral procession, the "
                        "harbor strike, and the impounded Ferrand shipments "
                        "all played out here.",
            },
            {
                "id": "tanneries",
                "kind": "place",
                "name": "the Tanneries",
                "aliases": ["the Tanneries", "Tannery men"],
                "note": "District of hides and strikebreakers — Tannery men "
                        "took the estate's loading (week 2), and the marsh "
                        "gang scattered into it (week 4).",
            },
            {
                "id": "ruravaqan",
                "kind": "city",
                "name": "Ruravaqan",
                "aliases": ["Ruravaqan"],
                "note": "Port of Shadia on the southern shore of the Bonia "
                        "archipelago; population 29,600; in practice governed "
                        "by its great families.",
            },
            {
                "id": "bonia",
                "kind": "world",
                "name": "Bonia",
                "aliases": ["Bonia", "the Bonia archipelago"],
                "note": "The world: FMG seed 878636343, three major "
                        "landmasses, eighteen nations.",
            },
            {
                "id": "shadia",
                "kind": "nation",
                "name": "Shadia",
                "aliases": ["Shadia"],
                "note": "Merchant republic of nineteen burgs whose real "
                        "capital lies elsewhere (its seat is Mish); "
                        "Ruravaqan is its port.",
            },
        ]
    )
    pages = camp / "lore" / "entities"
    pages.mkdir(parents=True, exist_ok=True)
    for old in pages.glob("*.md"):
        old.unlink()  # idempotent: every run rewrites its own outputs
    used = set()  # casefold: Windows filenames are case-insensitive
    for e in entities:
        stem = _page_stem(e["name"]) or _slug(e["name"])
        filename = stem + ".md"
        while filename.casefold() in used:
            filename = f"{filename[:-3]} (2).md"
        used.add(filename.casefold())
        meta = {k: e[k] for k in ("id", "kind", "name", "aliases") if e.get(k)}
        front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True,
                               default_flow_style=False).strip()
        (pages / filename).write_text(
            f"---\n{front}\n---\n\n{e['note'].strip()}\n", encoding="utf-8")
    return ("lore/entities/ (13 pages)", "built",
            f"{len(entities)} entity pages")


def build_style(camp, s):
    """lore/style/: names-shadian.txt (seed list) + voice.md (house voice)."""
    style = camp / "lore" / "style"
    style.mkdir(parents=True, exist_ok=True)
    rows = []

    chronicle_text = "\n".join(
        p.read_text(encoding="utf-8") for p in s["chronicles"].values()
    )
    # the chronicles wrap prose mid-phrase; match on flattened, case-folded text
    chronicle_flat = " ".join(chronicle_text.split()).casefold()

    names = list(s["canon_terms"])
    # proper nouns from the chronicles that the canon lexicon doesn't cover
    for candidate in ("the Interred", "the Marsh Gang"):
        if candidate.casefold() in chronicle_flat and candidate not in names:
            names.append(candidate)
    # Shadia settlements from the geography import (grounded seed stock)
    geo = s["geography"] or {}
    for sett in geo.get("major_settlements", []):
        if sett.get("nation") == "Shadia" and sett.get("name") not in names:
            names.append(sett["name"])
    # neighboring burgs recorded in the city params' road routes
    params = s.get("city_params_data")
    if params:
        for road in params.get("burg_input", {}).get("roadBearings", []) or []:
            route = str(road.get("route_id", "")).removesuffix("-road")
            name = route.replace("-", " ").title().replace(" ", "")
            if name and name not in names:
                names.append(name)

    names_path = style / "names-shadian.txt"
    header = (
        "# names-shadian.txt — a seed list the DM curates; one name per line.\n"
        "# Generated names draw from this list plus voice.md (in-voice, not\n"
        "# guaranteed). Seeded from verification/lexicons/canon-terms.txt,\n"
        "# the chronicles, and the Shadia settlements and road-neighbors\n"
        "# recorded in the Bonia geography and city params.\n"
    )
    names_path.write_text(
        header + "\n".join(names) + "\n", encoding="utf-8"
    )
    rows.append(("lore/style/names-shadian.txt", "built",
                 f"{len(names)} names (seed list; DM curates)"))

    voice = style / "voice.md"
    voice.write_text(VOICE_MD, encoding="utf-8")
    rows.append(("lore/style/voice.md", "built",
                 "4 verbatim excerpts, 4 never-rules from the narrator ethos"))
    return rows


VOICE_MD = """# House voice — Ruravaqan

Curated by the DM. The excerpts below define the tone; they are lifted
verbatim from the campaign chronicles (`simulation/chronicle/`). New prose —
generated backstory, named things, approved weeks — is judged against these,
not against volume of text.

## Excerpts

> The bells rang for three days. The creditors arrived on the fourth.
> (week 1)

> The gap existed. The opportunity did not.
> (week 3)

> The city's principal ceremony became, in effect, a receipt.
> (week 3)

> The coffers stayed shut. The crates stayed sealed. The harbor stayed closed, pending wages.
> (week 4)

## Never

Lifted from the narrator ethos in the simulator (`src/llm.py`):

- No prophecies, no ancient evils, no tavern meetings, no "little did they know."
- Invent no facts the ledger does not back — no new facts, no new characters, no dialogue.
- Do not resolve anything further than the ledger records.
- State only what follows from the ledger of resolved actions and the city state.

Names come from `names-shadian.txt` plus the voice above. In-voice is a soft
guarantee, never a hard one.
"""


def build_simulation(camp, s):
    rows = []
    sim = camp / "simulation"
    sim.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s["state_path"], sim / "state.yaml")
    rows.append(("simulation/state.yaml", "copied",
                 _rel(s["sim"], s["state_path"]) + " (verbatim)"))

    chron_dest = sim / "chronicle"
    chron_dest.mkdir(exist_ok=True)
    weeks = sorted(s["chronicles"])
    for week in weeks:
        src = s["chronicles"][week]
        shutil.copy2(src, chron_dest / src.name)
    rows.append(("simulation/chronicle/", "copied",
                 f"{len(weeks)} weekly files "
                 f"(weeks {', '.join(str(w) for w in weeks)})"))
    return rows


def build_city_map(camp, s):
    dest = camp / "maps" / "cities" / "ruravaqan"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s["city_svg"], dest / s["city_svg"].name)
    shutil.copy2(s["city_geojson"], dest / s["city_geojson"].name)
    if s["city_params"] is not None:
        s["city_params_data"] = yaml.safe_load(
            s["city_params"].read_text(encoding="utf-8")
        )
        shutil.copy2(s["city_params"], dest / "params.yaml")
        detail = ("settlemaker svg + geojson + generator params "
                  f"(seed {s['city_params_data'].get('seed')})")
    else:
        (dest / "params.yaml").write_text(
            "# Provenance: generated by dnd-mapping scripts/gen-ruravaqan.mjs\n"
            f"# from world seed {SEED}; original params.yaml not found.\n",
            encoding="utf-8",
        )
        detail = "settlemaker svg + geojson + fallback params note"
    return ((f"maps/cities/ruravaqan/ ({s['city_svg'].name}, "
             f"{s['city_geojson'].name}, params.yaml)", "copied", detail),)


# --------------------------------------------------------------------------
# verification + summary


def verify(root, camp):
    """safe_load every produced .yaml and confirm the required outputs exist.
    Returns the number of yaml files checked; exits 1 on failure."""
    errors = []
    required = [
        "campaign.yaml",
        "world/geography.yaml",
        "lore/timeline.yaml",
        "lore/style/names-shadian.txt",
        "lore/style/voice.md",
        "simulation/state.yaml",
        "maps/cities/ruravaqan/params.yaml",
    ]
    for rel in required:
        if not (camp / rel).is_file():
            errors.append(f"missing output: {rel}")
    if not list((camp / "lore" / "entities").glob("*.md")):
        errors.append("missing output: lore/entities/*.md")
    if not any((camp / "world").glob("source.*")):
        errors.append("missing output: world/source.<ext>")
    if not any((camp / "maps" / "cities" / "ruravaqan").glob("*.svg")):
        errors.append("missing output: maps/cities/ruravaqan/*.svg")
    chron = list((camp / "simulation" / "chronicle").glob("*.md"))
    if len(chron) < len(REQUIRED_CHRONICLE_WEEKS):
        errors.append(
            f"missing chronicles: found {len(chron)}, need "
            f"{len(REQUIRED_CHRONICLE_WEEKS)}"
        )

    checked = 0
    for p in sorted(camp.rglob("*.yaml")):
        try:
            yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"unparseable yaml {p.relative_to(root)}: {e}")
        else:
            checked += 1

    if errors:
        print("assemble: verification FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    return checked


def print_summary(rows, camp, root):
    width = max(len(r[0]) for r in rows) + 2
    print()
    print("Assembled campaign: " + str(camp.relative_to(root)))
    print("-" * 78)
    for target, action, detail in rows:
        print(f"  {target.ljust(width)}{action.ljust(9)} {detail}")
    print("-" * 78)


# --------------------------------------------------------------------------
# helpers


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, width=100,
        default_flow_style=False,
    )
    path.write_text(text, encoding="utf-8")


def _rel(base, path):
    try:
        return str(Path(path).relative_to(base))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------


def build(root):
    camp = root / "campaigns" / CAMPAIGN_NAME
    s = collect_sources(root)
    rows = []
    rows.append(build_campaign_yaml(camp, s["state"]))
    rows.extend(build_world(camp, s))
    rows.append(build_timeline(camp, s["state"]))
    rows.append(build_entities(camp, s["state"]))
    rows.extend(build_style(camp, s))
    rows.extend(build_simulation(camp, s))
    rows.extend(build_city_map(camp, s))

    n_yaml = verify(root, camp)
    print_summary(rows, camp, root)
    print(f"verification: {n_yaml} .yaml files parsed clean "
          f"(yaml.safe_load)")
    return camp


def check(root):
    camp = root / "campaigns" / CAMPAIGN_NAME
    if not camp.is_dir():
        print(f"assemble: no campaign at {camp} — run without --check first",
              file=sys.stderr)
        return sys.exit(1)
    n_yaml = verify(root, camp)
    tl = yaml.safe_load((camp / "lore" / "timeline.yaml").read_text("utf-8"))
    pages = list((camp / "lore" / "entities").glob("*.md"))
    print(f"check: {camp.relative_to(root)}")
    print(f"  timeline entries: {len(tl['entries'])}")
    print(f"  entity pages: {len(pages)}")
    print(f"  chronicles: {len(list((camp / 'simulation' / 'chronicle').glob('*.md')))}")
    print(f"  {n_yaml} .yaml files parsed clean")
    return 0


def main(argv=None):
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(
        prog="assemble.py",
        description="Build campaigns/ruravaqan/ from the three engine "
        "projects (copy-in per design decision D4).",
    )
    ap.add_argument(
        "--root",
        default=None,
        help="studio root override (default: the parent of this file's "
        "directory, i.e. Documents/D&D)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="do not build; verify the existing campaign (all .yaml parses, "
        "required outputs present)",
    )
    args = ap.parse_args(argv)
    root = (
        Path(args.root).resolve()
        if args.root
        else Path(__file__).resolve().parent.parent
    )
    if not root.is_dir():
        sys.exit(f"assemble: root does not exist: {root}")
    if args.check:
        sys.exit(check(root))
    build(root)
    sys.exit(0)


if __name__ == "__main__":
    main()
