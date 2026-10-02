"""State-file I/O and the engine's small shared vocabulary.

One loader, one dumper, one default path — the per-module copies this
replaces had diverged in signature only. `load_state(path=None)` /
`save_state(state, path=None)`: omit the path for the engine's own
world/state.yaml; ingest passes its --state target explicitly.

save_state writes a per-writer UNIQUE tmp + os.replace: a crash or kill
mid-write used to truncate world/state.yaml in place — for the standalone
workflow that file is the single source of truth, so the write itself must
be atomic — and two writers sharing one fixed tmp name could land a
half-written file (one writer's replace consuming the other's tmp
mid-write), so the tmp is minted fresh per call (mkstemp).
"""

import contextlib
import os
import pathlib
import re
import tempfile
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

# ------------------------------------------------- engine-home cross-process
# The home (world/) is ONE scratch shared by the studio's week/forecast
# jobs — which copy a campaign state in, run this engine, and diff the
# outputs back out — and this repo's standalone CLIs (tick.py --live,
# effects.py --apply, ingest.py --apply). Two writers interleave
# state.yaml last-writer-wins and can leak a foreign chronicle into a
# studio proposal, so the studio takes a cross-process lock file over the
# home for its whole engine job (studio/spine.py try_file_lock, taken in
# studio/engines.py engine_lock_logged) and marks the engine processes it
# launches with STUDIO_ENGINE_HOME_HELD — those children must NOT re-take
# the lock (they would deadlock on their own parent). Every other run of
# the mains above is standalone: it claims the lock through
# standalone_home_lock() and refuses with a readable message while a
# studio job (or another standalone run) holds it.

HOME_LOCK_FILE = ROOT / "world" / ".engine-home.lock"
STUDIO_ENGINE_HOME_HELD = "STUDIO_ENGINE_HOME_HELD"

try:
    import fcntl as _fcntl
    _FILE_LOCK = "posix"
except ImportError:  # not POSIX
    try:
        import msvcrt as _msvcrt
        _FILE_LOCK = "windows"
    except ImportError:  # exotic platform: no cross-process exclusion
        _FILE_LOCK = "none"


class HomeLockBusy(RuntimeError):
    """The shared engine home is held by another process (a studio week or
    forecast job, or another standalone run) — the CLI must refuse without
    touching the home. str(this) is the user-facing message."""


@contextlib.contextmanager
def standalone_home_lock():
    """A standalone CLI's claim on the shared engine home: byte-lock the
    lock file (Windows msvcrt / POSIX flock — the same dance the studio's
    spine.CampaignLock performs) for the whole run. No-op when this
    process is a studio child (STUDIO_ENGINE_HOME_HELD: the parent server
    holds the lock) or the platform has no file locking. Raises
    HomeLockBusy when another process holds the home — refuse, never
    queue: a standalone run silently waiting out a half-hour studio week
    is a surprise nobody asked for."""
    if _FILE_LOCK == "none" or os.environ.get(STUDIO_ENGINE_HOME_HELD):
        yield
        return
    try:
        HOME_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = open(HOME_LOCK_FILE, "a+b")  # noqa: SIM115 — held for the run,
        # unlocked and closed by the finally below
    except OSError as e:
        raise HomeLockBusy(
            f"the engine home lock {HOME_LOCK_FILE} cannot be opened ({e})"
            " — refusing rather than risk racing a studio run")
    try:
        try:
            if _FILE_LOCK == "windows":
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
            else:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            raise HomeLockBusy(
                "the engine home is busy — another process (a studio week "
                "or forecast job, or another standalone run) holds "
                f"{HOME_LOCK_FILE}. Wait for it to finish before running "
                "standalone against the shared home (world/).")
        try:
            yield
        finally:
            if _FILE_LOCK == "windows":
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
            else:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    finally:
        fh.close()


def home_lock_held() -> bool:
    """Whether any process holds the engine-home lock (fresh-handle probe:
    locking through a second handle fails while a first one holds the byte
    range — including a holder inside this process)."""
    if _FILE_LOCK == "none":
        return False
    try:
        fh = open(HOME_LOCK_FILE, "a+b")  # noqa: SIM115 — probe: closed
        # within this function
    except OSError:
        return False
    try:
        if _FILE_LOCK == "windows":
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
        else:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


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
    downstream reader. Faction ids are TEXT and unique (an unquoted YAML
    number is refused, not stringified: the scheduler's tokens, llm's
    targets, and the resolver's known ids all key on the id as text);
    `aliases` values and `generic_words` are lists of words (a scalar is
    refused — downstream joins it letter-by-letter); `meta.rules`
    (when present) is a mapping with a numeric default_dc the resolver
    can int() after the spend. Raises ValueError."""
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
    rules = meta.get("rules") if isinstance(meta, dict) else None
    if rules is not None and not isinstance(rules, dict):
        raise ValueError(
            f"meta.rules must be a mapping (rules: {{default_dc: 12}}), "
            f"not {rules!r} — resolve_action calls rules.get('default_dc') "
            "on it for every unopposed roll")
    if isinstance(rules, dict) and rules.get("default_dc") is not None:
        dc = rules["default_dc"]
        numeric = not isinstance(dc, bool)
        if numeric:
            try:
                int(dc)
            except (TypeError, ValueError):
                numeric = False
        if not numeric:
            raise ValueError(
                f"meta.rules.default_dc is {dc!r}, not a number — write "
                "default_dc: 12; the resolver rolls against "
                "int(default_dc), and that call used to fire only after "
                "the week's whole provider spend")
    factions = state.get("factions")
    if not isinstance(factions, list) or not factions:
        raise ValueError("factions is missing or empty — a week needs at "
                         "least one faction")
    seen_fids = set()
    for f in factions:
        if not isinstance(f, dict) or not str(f.get("id") or "").strip():
            raise ValueError("every faction needs a non-empty id")
        if not isinstance(f["id"], str):
            # ids key the scheduler's activation tokens, llm's proposal
            # targets, and the resolver's known-target set; YAML hands a
            # raw number to some of those and a stringified one to others
            raise ValueError(
                f"faction id {f['id']!r} must be text, not "
                f"{type(f['id']).__name__} — quote it in state.yaml "
                f"(id: '{f['id']}')")
        fid = f["id"]
        if fid in seen_fids:
            # mirrors the places check above: by_actor keeps the last of a
            # duplicate pair, silently merging two factions' weeks
            raise ValueError(
                f"two factions share the id '{fid}' — give each a distinct "
                "id: the scheduler, the resolver, and the rumor mill all "
                "key on it, and a duplicate silently merges their weeks")
        seen_fids.add(fid)
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
    aliases = state.get("aliases")
    if aliases is not None:
        if not isinstance(aliases, dict):
            raise ValueError(
                f"aliases must be a mapping of faction id to a list of "
                f"match words, not {aliases!r}")
        for key, val in aliases.items():
            if isinstance(val, str) or not (
                    isinstance(val, list)
                    and all(isinstance(w, str) and w.strip() for w in val)):
                raise ValueError(
                    f"aliases for faction '{key}' must be a list of match "
                    f"words (aliases: {{{key}: [ferrand, ...]}}), not "
                    f"{val!r} — ingest joins the value into the faction's "
                    "match words, and a scalar joins letter-by-letter so "
                    "the alias never matches a note")
    generic_words = state.get("generic_words")
    if generic_words is not None and (
            not isinstance(generic_words, list)
            or not all(isinstance(w, str) and w.strip()
                       for w in generic_words)):
        raise ValueError(
            f"generic_words must be a list of words (generic_words: "
            f"[council, harbor, ...]), not {generic_words!r} — consistency "
            "builds the city-book filter from it, and a scalar string "
            "would be read as single letters, replacing the default "
            "filter with nonsense")
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
            for h in r["heard_by"]:
                if not isinstance(h, str) or not h.strip():
                    raise ValueError(f"rumor {i + 1} heard_by entries must "
                                     f"be non-empty faction ids, not {h!r} "
                                     "— the spread loop sorts them")
            if "quiet" in r:
                # rumors.spread does `f not in rumor.get("quiet", [])`: a
                # hand-written null (the key PRESENT, value null — the
                # default only applies when absent) must fail HERE, not
                # as a TypeError mid-week (the disposition:/knows: rule)
                quiet = r["quiet"]
                if not isinstance(quiet, list) or \
                        not all(isinstance(q, str) and q.strip()
                                for q in quiet):
                    raise ValueError(f"rumor {i + 1} quiet must be a list "
                                     "of faction ids (or absent), not "
                                     f"{quiet!r}")
            if not isinstance(r.get("born_week"), int) \
                    or isinstance(r.get("born_week"), bool):
                raise ValueError(f"rumor {i + 1} needs an integer "
                                 f"born_week, not {r.get('born_week')!r}")
            for j, t in enumerate(r.get("transmissions")):
                # shape FIRST: t.get must never run on a non-mapping (a
                # stray string here used to escape as an AttributeError)
                if not isinstance(t, dict):
                    raise ValueError(
                        f"rumor {i + 1} transmission {j + 1} is not a "
                        f"mapping, not {t!r} — views.heard reads every "
                        "field")
                tmiss = [k for k in ("week", "from", "to",
                                     "fidelity_before", "fidelity_after")
                         if not str(t.get(k) or "").strip()]
                if tmiss:
                    raise ValueError(
                        f"rumor {i + 1} transmission {j + 1} is missing "
                        f"{', '.join(tmiss)} — views.heard reads every "
                        "field")


def save_state(state, path=None):
    path = pathlib.Path(path or STATE_FILE)
    # per-writer UNIQUE tmp (mkstemp): two writers sharing one fixed tmp
    # name could land a half-written file — one writer's os.replace
    # consuming the other's tmp mid-write (ticket 26). Same atomic
    # replace contract as before, impossible to collide.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".",
                                    suffix=".tmp")
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(state, f, sort_keys=False, allow_unicode=True,
                           width=100)
        # Windows raises PermissionError for an instant while a concurrent
        # reader holds the destination; retry rather than lose the week to a
        # sharing violation.
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)  # no litter on any exit (a successful
        # replace already consumed the tmp; missing_ok makes that a no-op)
