"""Session-notes ingestion.

Session notes become pending events; only what the DM leaves in the file
becomes canon.

Two modes:
  python src/ingest.py notes.md             # bullets -> world/pending-week-N.yml
  python src/ingest.py --apply world/pending-week-N.yml
                                            # surviving lines -> state.yaml
Either mode takes --state PATH to read/apply against that state file
instead of the default world/state.yaml.

Proposals are dated the clock's current week + 1 — the next week to be
simulated — unless the DM supplies an explicit week in the pending file.
apply() appends events and applies any structured `effects:` entries
through effects.py (the shared validator: typed, allowlisted, dedup by
stable effect id); the clock is advanced by tick.py --live or the DM,
never here. History bullets carry NO mechanical effect — a treasury
destroyed in prose changes nothing until the DM adds a set_resource
effect.
"""

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import effects
from store import ROOT, STATE_FILE
from store import load_state as _store_load
from store import save_state as _store_save

PENDING_COMMENT = "# edit freely: delete or fix lines, then: python src/ingest.py --apply <this file>"

# Words too generic to point at a faction: articles and name decorations.
_STOPWORDS = {"the", "a", "an", "of", "and", "s", "house", "claim"}

# Extra match words where a faction's id and its name diverge. Per-campaign
# override lives in state.yaml under `aliases: {faction_id: [words...]}`;
# this constant is the fallback for states that predate that key.
_DEFAULT_ALIASES = {"mint": ["ferrand"], "bastard": ["sera"]}

_BULLET = re.compile(r"^(?:[-*]|\d+[.)])\s+(.+)$")


def _words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def faction_tokens(state):
    """Distinctive match words per faction id, in state order. A word that
    names two factions names neither; city-name words name no one."""
    aliases = state.get("aliases") or _DEFAULT_ALIASES
    city = _words(state.get("city", {}).get("name", ""))
    table = {}
    for f in state["factions"]:
        words = _words(f["id"]) | _words(f["name"]) | _words(" ".join(aliases.get(f["id"], [])))
        table[f["id"]] = words - _STOPWORDS - city
    owners = {}
    for fid, toks in table.items():
        for t in toks:
            owners.setdefault(t, set()).add(fid)
    shared = {t for t, seen in owners.items() if len(seen) > 1}
    return {fid: toks - shared for fid, toks in table.items()}


def resolve_actor(text, tokens):
    """First faction (state order) whose words appear in the line, else "world"."""
    words = _words(text)
    for fid, toks in tokens.items():
        if toks & words:
            return fid
    return "world"


def extract(notes_text, state):
    """Non-empty bullet lines (-, *, 1.) become proposals dated the clock's
    week + 1 (the next week to be simulated); every other line is ignored.
    Actor: first faction whose distinctive words appear, else "world"."""
    week = state["meta"]["clock"]["week"] + 1
    tokens = faction_tokens(state)
    out = []
    for line in notes_text.splitlines():
        m = _BULLET.match(line.strip())
        if not m:
            continue
        text = m.group(1).strip()
        out.append({"actor": resolve_actor(text, tokens), "text": text, "week": week})
    return out


def write_pending(proposals, path):
    """Write the DM-editable queue: {week, proposals} with the edit-me comment on top."""
    week = proposals[0]["week"] if proposals else None
    with open(path, "w", encoding="utf-8") as f:
        f.write(PENDING_COMMENT + "\n")
        yaml.safe_dump(
            {"week": week, "proposals": [{"actor": p["actor"], "text": p["text"]} for p in proposals]},
            f, sort_keys=False, allow_unicode=True, width=100,
        )
    return path


def read_pending(path):
    """Parse a pending file back; the leading comment is just a YAML comment."""
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not doc:
        doc = {}
    doc.setdefault("week", None)
    doc.setdefault("proposals", [])
    return doc


def apply(pending, state_path):
    """Apply the DM's surviving record: each surviving proposal appends to
    state.yaml's events as {week, actor, text} (deduplicated — reimporting
    the same file adds nothing), and any structured `effects:` entries are
    validated and applied through effects.apply_session_record (all-or-
    nothing: one invalid effect refuses the whole file). Returns the count
    merged (events + effects), or -1 (nothing written) on any refusal.
    Bumps nothing else — the clock belongs to tick.py / the DM.
    A pending week that is not the clock's next week is applied to the week
    it claims (the DM's file is authoritative) but warns loudly — stale
    files land in past weeks the scheduler no longer looks at."""
    state = load_state(state_path)
    week = pending.get("week")
    next_week = state["meta"]["clock"]["week"] + 1
    if week is not None and week != next_week:
        print(f"warning: pending file is dated week {week}, but the next week "
              f"to simulate is {next_week} — these events land in week {week}, "
              f"which the scheduler reads only for history")
    record = {
        "week": week,
        "events": pending.get("proposals") or [],
        "effects": pending.get("effects") or [],
    }
    if pending.get("source"):
        record["source"] = pending["source"]
    events, applied, errors = effects.apply_session_record(record, state)
    if errors:
        for e in errors:
            print(f"refusing: {e}", file=sys.stderr)
        return -1
    if events or applied:
        save_state(state, state_path)
    return events + applied


def load_state(path):
    """Delegates to store, resolving the module-global STATE_FILE at call
    time so tests can point the default at a temp copy."""
    return _store_load(path or STATE_FILE)


def save_state(state, path):
    _store_save(state, path or STATE_FILE)


def main(argv):
    state_path = STATE_FILE
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--state" and i + 1 < len(argv):
            state_path = argv[i + 1]
            i += 2
            continue
        if argv[i].startswith("--state="):
            state_path = argv[i].split("=", 1)[1]
            i += 1
            continue
        rest.append(argv[i])
        i += 1
    if "--apply" in rest:
        i = rest.index("--apply")
        paths = rest[:i] + rest[i + 1:]
        if len(paths) != 1:
            print("usage: python src/ingest.py --apply <pending.yml> [--state <state.yaml>]", file=sys.stderr)
            return 2
        try:
            pending = read_pending(paths[0])
            n = apply(pending, state_path)
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if n < 0:
            return 1
        print(f"merged {n} events")
        return 0
    if len(rest) != 1:
        print("usage: python src/ingest.py <notes.md>  |  python src/ingest.py --apply <pending.yml>  [--state <state.yaml>]", file=sys.stderr)
        return 2
    try:
        notes_text = pathlib.Path(rest[0]).read_text(encoding="utf-8")
        state = load_state(state_path)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    proposals = extract(notes_text, state)
    if not proposals:
        print(f"no bullet lines found in {argv[0]}", file=sys.stderr)
        return 1
    week = proposals[0]["week"]
    out = ROOT / "world" / f"pending-week-{week}.yml"
    write_pending(proposals, out)
    print(f"{out} ({len(proposals)} proposals)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
