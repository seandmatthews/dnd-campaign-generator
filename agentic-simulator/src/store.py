"""State-file I/O and the engine's small shared vocabulary.

One loader, one dumper, one default path — the per-module copies this
replaces had diverged in signature only. `load_state(path=None)` /
`save_state(state, path=None)`: omit the path for the engine's own
world/state.yaml; ingest passes its --state target explicitly.

save_state writes tmp + os.replace: a crash or kill mid-write used to
truncate world/state.yaml in place — for the standalone workflow that file
is the single source of truth, so the write itself must be atomic.
"""

import os
import pathlib
import re
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "world" / "state.yaml"

_WORD_RE = re.compile(r"[a-z0-9]+")

# The resource ladder, canonically: keys, grades, grade order.
RESOURCE_KEYS = ("coin", "muscle", "legitimacy")
RESOURCE_LEVELS = ("none", "low", "medium", "high")
LEVEL_INDEX = {g: i for i, g in enumerate(RESOURCE_LEVELS)}

# Rumor fidelity grades in decay order — rumors.spread indexes into this
# ladder, so a hand-invented grade must be refused, not decayed from.
RUMOR_FIDELITY = ("eyewitness", "reliable", "hearsay", "dead")


def split_argv(argv, *valued_flags):
    """Shared CLI parsing: (values, rest) — each flag in valued_flags
    consumes `--flag X` or `--flag=X` (last wins); everything else lands
    in rest, order kept."""
    values, rest, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        for flag in valued_flags:
            if a == flag and i + 1 < len(argv):
                values[flag] = argv[i + 1]
                i += 1
                break
            if a.startswith(flag + "="):
                values[flag] = a.split("=", 1)[1]
                break
        else:
            rest.append(a)
        i += 1
    return values, rest


def faction_map(state):
    """Factions keyed by str(id), skipping non-dict or id-less entries —
    the guarded lookup the goal review, views, and project reporting share."""
    return {str(f.get("id")): f for f in (state or {}).get("factions", [])
            if isinstance(f, dict) and f.get("id")}


def load_state(path=None):
    with open(path or STATE_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _place_id(name):
    """A stable place id from its name — hyphen-slug, capped like the spine
    caps entity stems so a 150-char place name cannot push a filename or
    prompt line past Windows limits."""
    slug = "-".join(_WORD_RE.findall(str(name).lower()))[:80].rstrip("-")
    return slug or "place"


def places_of(state):
    """Every simulated place, normalized: the `places` list schema, or the
    legacy single-`city` dict (kind "city"). Each place carries an `id`
    (derived from the name when absent) and a `kind`; file order holds and
    the FIRST place is primary — the default home of factions naming no
    `place`."""
    raw = state.get("places") if isinstance(state, dict) else None
    out = []
    if isinstance(raw, list) and raw:
        for p in raw:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            place = dict(p)
            place["name"] = name
            place.setdefault("id", _place_id(name))
            place.setdefault("kind", "place")
            out.append(place)
        return out
    city = state.get("city") if isinstance(state, dict) else None
    if isinstance(city, dict) and str(city.get("name") or "").strip():
        place = dict(city)
        place["name"] = str(city.get("name") or "").strip()
        place["id"] = _place_id(place["name"])
        place.setdefault("kind", "city")
        return [place]
    return []


def faction_place(state, faction):
    """The place dict a faction operates from: its `place` id resolved
    against places_of, else the primary place. validate_state refuses an
    unknown place id before any spend, so a miss here is a legacy/odd state
    degrading to primary — never a KeyError mid-week."""
    places = places_of(state)
    pid = (faction or {}).get("place") if isinstance(faction, dict) else None
    for p in places:
        if pid is not None and p["id"] == pid:
            return p
    return places[0] if places else {}


def place_words(state, include_districts=True):
    """Lowercase words naming any place (and its districts unless
    include_districts=False). Scheduler/consistency subtract these so a
    place word never activates or vetoes a similarly-named faction; ingest
    passes False because notes say "the Mint" meaning the FACTION nicknamed
    after the district."""
    words = set()
    for p in places_of(state):
        words |= set(_WORD_RE.findall(str(p.get("name", "")).lower()))
        if include_districts:
            for d in p.get("districts") or []:
                words |= set(_WORD_RE.findall(str(d).lower()))
    return words


def validate_state(state):
    """The shape a week spends against, checked BEFORE any LLM call — each
    gap here used to crash after the spend, two of them after the canon
    write. The state file is hand-edited by design, so refuse a partial
    edit naming the missing piece rather than buying it with a week's
    provider spend. Container existence AND element shape: a faction the
    scheduler, views, and the chronicle summary direct-index (name, goal,
    resources) must carry those keys; an explicitly-null optional
    (disposition:, knows:) is refused too — `.get(key, default)` defaults
    only when the key is ABSENT, so a hand-written null defeats every
    downstream reader. Raises ValueError."""
    if not isinstance(state, dict):
        raise ValueError("state is not a mapping (empty or corrupt file?)")
    places = places_of(state)
    if not places:
        raw_places = state.get("places")
        if isinstance(raw_places, list) and raw_places:
            # a nonempty list that normalizes to nothing is a MALFORMED
            # list, not a missing one — naming the legacy city here would
            # tell the author to add data they already have
            raise ValueError(
                f"places has {len(raw_places)} entr"
                f"{'y' if len(raw_places) == 1 else 'ies'} but none is "
                "usable: every entry must be a mapping with at least "
                "{name: ...} — fix or empty the list (the legacy city "
                "fallback only applies when `places` is absent)")
        raise ValueError(
            "no places: set places: [{name: ...}, ...] (a city, its wider "
            "world — anything factions belong to), or the legacy "
            "city.name")
    seen_ids = set()
    for p in places:
        pid = p["id"]
        if pid in seen_ids:
            raise ValueError(
                f"two places collapse to the same id '{pid}' — give them "
                "distinct names or explicit ids")
        seen_ids.add(pid)
    meta = state.get("meta")
    clock = meta.get("clock") if isinstance(meta, dict) else None
    week = clock.get("week") if isinstance(clock, dict) else None
    if not isinstance(week, int) or isinstance(week, bool):
        raise ValueError(f"meta.clock.week is {week!r}, not an integer")
    factions = state.get("factions")
    if not isinstance(factions, list) or not factions:
        raise ValueError("factions is missing or empty — a week needs at "
                         "least one faction")
    for f in factions:
        if not isinstance(f, dict) or not str(f.get("id") or "").strip():
            raise ValueError("every faction needs a non-empty id")
        fid = str(f["id"])
        if not str(f.get("name") or "").strip():
            raise ValueError(f"faction '{fid}' needs a non-empty name — "
                             "the scheduler and the inspect view read it")
        if not str(f.get("goal") or "").strip():
            raise ValueError(f"faction '{fid}' needs a non-empty goal — "
                             "the chronicle's state summary reads it")
        resources = f.get("resources")
        if not isinstance(resources, dict):
            raise ValueError(f"faction '{fid}' needs a resources mapping "
                             f"(coin/muscle/legitimacy), not {resources!r}")
        for k, v in resources.items():
            if v not in RESOURCE_LEVELS:
                raise ValueError(
                    f"faction '{fid}' resource {k!r} is {v!r}, not one of "
                    f"{', '.join(RESOURCE_LEVELS)} — the consequence rules "
                    "index the ladder by grade")
        if "disposition" in f and not isinstance(f["disposition"], dict):
            raise ValueError(f"faction '{fid}' disposition must be a "
                             f"mapping, not {f['disposition']!r} — a "
                             "hand-written `disposition:` (null) defeats "
                             "every .get() default that reads it; delete "
                             "the key or give it faction_id: posture entries")
        if "knows" in f and not isinstance(f["knows"], list):
            raise ValueError(f"faction '{fid}' knows must be a list, not "
                             f"{f['knows']!r} — delete the key or list the "
                             "facts it knows")
        place = f.get("place")
        if place is not None and place not in seen_ids:
            raise ValueError(
                f"faction '{fid}' names place '{place}', which is not "
                f"one of: {', '.join(sorted(seen_ids))}")
    events = state.get("events")
    if not isinstance(events, list):
        raise ValueError("events is missing or not a list — resolved weeks "
                         "append to it")
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise ValueError(f"event {i + 1} is not a mapping")
        if not isinstance(ev.get("week"), int) or isinstance(ev.get("week"), bool):
            raise ValueError(f"event {i + 1} needs an integer week, not "
                             f"{ev.get('week')!r}")
        if not str(ev.get("actor") or "").strip():
            raise ValueError(f"event {i + 1} needs a non-empty actor — the "
                             "rumor mill reads it (world events never spawn "
                             "rumors but still carry the key)")
        if not str(ev.get("text") or "").strip():
            raise ValueError(f"event {i + 1} needs non-empty text")
    rumors_list = state.get("rumors")
    if rumors_list is not None:
        if not isinstance(rumors_list, list):
            raise ValueError(f"rumors must be a list, not {rumors_list!r}")
        for i, r in enumerate(rumors_list):
            if not isinstance(r, dict):
                raise ValueError(f"rumor {i + 1} is not a mapping")
            missing = [k for k in ("id", "claim", "fidelity")
                       if not str(r.get(k) or "").strip()]
            if missing:
                raise ValueError(f"rumor {i + 1} is missing "
                                 f"{', '.join(missing)}")
            if r.get("fidelity") not in RUMOR_FIDELITY:
                raise ValueError(
                    f"rumor {i + 1} fidelity {r.get('fidelity')!r} is not "
                    f"one of {', '.join(RUMOR_FIDELITY)}")
            for k in ("heard_by", "transmissions"):
                if not isinstance(r.get(k), list):
                    raise ValueError(f"rumor {i + 1} needs {k} as a list, "
                                     f"not {r.get(k)!r}")
            if not isinstance(r.get("born_week"), int) \
                    or isinstance(r.get("born_week"), bool):
                raise ValueError(f"rumor {i + 1} needs an integer "
                                 f"born_week, not {r.get('born_week')!r}")
            for j, t in enumerate(r.get("transmissions")):
                tmiss = [k for k in ("week", "from", "to",
                                     "fidelity_before", "fidelity_after")
                         if not str(t.get(k) or "").strip()]
                if not isinstance(t, dict) or tmiss:
                    raise ValueError(
                        f"rumor {i + 1} transmission {j + 1} is missing "
                        f"{', '.join(tmiss) if isinstance(t, dict) else 'its mapping shape'}"
                        " — views.heard reads every field")


def save_state(state, path=None):
    path = pathlib.Path(path or STATE_FILE)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(state, f, sort_keys=False, allow_unicode=True, width=100)
    # Windows raises PermissionError for an instant while a concurrent
    # reader holds the destination; retry rather than lose the week to a
    # sharing violation.
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 2:
                tmp.unlink(missing_ok=True)  # no litter, old state intact
                raise
            time.sleep(0.05 * (attempt + 1))
