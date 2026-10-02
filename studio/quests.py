"""Quests as first-class entities (docs/tickets/06-quests-first-class.md).

A quest is a lore page: markdown under lore/entities/, Obsidian-canonical,
wiki-linked like every other page — the PLACES pattern with a different
frontmatter contract:

    ---
    kind: quest
    name: The Troll's Tithe
    status: hooked          # hooked | active | resolved | abandoned
    given_by: Falburn       # optional — page name of the patron
    places: [Greywatch Hollow]   # optional — page names, matched through
    factions: [Stoneyvale Wardens]  #  the links index
    due: 6                  # optional — week number (or ISO date) the
    level: "2-4"            #  timer runs out; free text, never parsed
    ---

Everything except `status` is optional; extra frontmatter keys are ignored,
never errors; an unknown status degrades to `hooked` with a review finding
(same posture as entity validation elsewhere) — a hand-edit must never make
quests vanish. Non-quest pages are invisible to this read model. The read
model reads disk on every call: hand-edits in Obsidian are equally valid
and there is no cache to invalidate.

Status changes are reviewed short writes: campaign lock or 409 (the route
takes it), publication-path revision checks (a stale
expected_page_revision -> 409, nothing written), transactional, and the
write is LINE SURGERY on the status line — body, unknown keys, comments
and line endings survive byte-for-byte (ticket 10).

CLI (same service the HTTP routes use):
    python studio/quests.py <campaign> list [--json]
    python studio/quests.py <campaign> show <page> [--json]
    python studio/quests.py <campaign> set <page> <status>
"""

import datetime
import json
import re
import sys
from pathlib import Path

import canon
import links
import publication
import spine
from places import (PlaceError, _changed_page_text, _frontmatter_of,
                    _page_snapshot, _id_list)

STATUSES = ("hooked", "active", "resolved", "abandoned")
DEFAULT_STATUS = "hooked"

# `due` forms: a week number ("6", 6) or an ISO date ("2026-10-08" — yaml
# also hands us date objects when the value was unquoted).
_DUE_WEEK_RE = re.compile(r"^-?\d+$")


class QuestError(Exception):
    """A quest problem that maps to a 422."""


class QuestNotFound(QuestError):
    """Unknown quest page — the server maps this to 404."""


class QuestConflict(QuestError):
    """A stale-revision conflict — the server maps this to 409."""


def _locked_publish(name: str, writes: list[dict], operation: str,
                    base_revisions: dict) -> dict:
    """publish, with the revision conflict translated to the quest error
    the routes map to 409. Caller holds the campaign lock."""
    try:
        return publication.publish(name, writes, operation=operation,
                                   base_revisions=base_revisions)
    except publication.RevisionConflict as e:
        raise QuestConflict(str(e))


# ----------------------------------------------------------------- due dates


def _parse_due(meta: dict) -> dict:
    """The `due` frontmatter, parsed only as far as the panel and briefing
    need: a week number or an ISO date, never anything else. Anything that
    is neither comes back as `due_invalid` — a finding, never a crash
    (level: "2-4" taught us free text ends up in every field eventually)."""
    v = meta.get("due")
    out = {"due_week": None, "due_date": None, "due_invalid": ""}
    if v is None or v == "":
        return out
    if isinstance(v, bool):
        out["due_invalid"] = str(v)
        return out
    if isinstance(v, datetime.datetime):
        out["due_date"] = v.date().isoformat()
        return out
    if isinstance(v, datetime.date):
        out["due_date"] = v.isoformat()
        return out
    if isinstance(v, int):
        out["due_week"] = v
        return out
    if isinstance(v, float) and v.is_integer():
        out["due_week"] = int(v)
        return out
    if isinstance(v, str):
        s = v.strip()
        if _DUE_WEEK_RE.match(s):
            out["due_week"] = int(s)
            return out
        try:
            out["due_date"] = datetime.date.fromisoformat(s).isoformat()
            return out
        except ValueError:
            out["due_invalid"] = s
            return out
    out["due_invalid"] = str(v)
    return out


def _is_overdue(record: dict, current_week, today: datetime.date) -> bool:
    """A timer the GM forgot is the one that kills the campaign: `due` in
    the past (week or date) while the quest is still open. resolved ended
    the story; abandoned walked away from it — neither owes a timer. A
    week due is overdue strictly BEFORE the current week (due week 6 of
    week 6 is the timer running out, not missed); a date due is overdue
    before today. No clock (week form) or an unparsable due -> never
    overdue."""
    if record["status"] in ("resolved", "abandoned"):
        return False
    if record["due_week"] is not None:
        return isinstance(current_week, int) \
            and record["due_week"] < current_week
    if record["due_date"] is not None:
        try:
            return datetime.date.fromisoformat(record["due_date"]) < today
        except ValueError:
            return False
    return False


# ---------------------------------------------------------------- resolution


def _resolve_place_ids(names: list[str], idx: dict | None,
                       sim_places: list[dict]) -> list[str]:
    """Quest `places:` page names -> place ids where resolvable. The links
    index resolves a name to a page; a page with `place_id:` owns the id.
    A name with no id-carrying page may still uniquely match a simulated
    place by normalized name — the registry's SUGGESTED posture (a name
    match never merges identity; here it only widens the briefing's
    intersection). Ambiguous or unresolvable names stay raw on the record
    and simply never intersect — never a guess."""
    out = []
    for nm in names:
        target = idx["resolve"].get(links.normalize_key(nm)) \
            if idx is not None else None
        pid = str((target or {}).get("place_id") or "").strip() \
            if target is not None and target.get("source") == "entity" else ""
        if pid:
            out.append(pid)
            continue
        key = links.normalize_key(nm)
        matches = [p["id"] for p in sim_places
                   if links.normalize_key(p["name"]) == key]
        if len(matches) == 1:
            out.append(matches[0])
    return list(dict.fromkeys(out))


def _resolve_faction_ids(names: list[str],
                         factions_by_key: dict[str, str]) -> list[str]:
    """Quest `factions:` names -> simulation faction ids. A faction may be
    named by page name ("Stoneyvale Wardens"), by faction id, or by page
    whose name IS the faction's — the normalized-name table covers all
    three. Unresolvable names stay raw on the record."""
    out = []
    for nm in names:
        fid = factions_by_key.get(links.normalize_key(nm))
        if fid:
            out.append(fid)
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- read model


def _entity_pages_degraded(name: str, finding) -> list[dict]:
    """Every entity page, with one unparsable page costing only itself —
    the module's contract is that a hand-edit must never make quests
    vanish, and spine.entity_pages raises on the FIRST bad page (an NPC's
    stray tab would otherwise empty the whole read model, panel and
    briefing included). Mirrors entity_pages' FileNotFoundError skip."""
    root = spine.campaign_dir(name) / "lore" / "entities"
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*.md")):
        try:
            out.append(spine.parse_entity_page(p))
        except FileNotFoundError:
            continue  # unlinked mid-glob (migration/race) — never a 500
        except (spine.CampaignError, OSError) as e:
            finding("quests/lore-unreadable", "error", str(e),
                    f"lore/entities/{p.name}")
    return out


def load(name: str) -> dict:
    """Every `kind: quest` entity page, normalized. Read fresh from disk on
    every call (hand-edits in Obsidian are equally valid; no cache).

    quests    [{page, file, name, status, status_raw?, given_by, places,
                place_ids, factions, faction_ids, due_week, due_date,
                due_invalid, overdue, level, note, body, revision}]
    by_page   page stem -> quest record
    findings  [{rule, level, message, source}] — advisory
    counts    {status: n} over the canonical four statuses
    """
    spine.require_campaign(name)
    d = spine.campaign_dir(name)
    findings: list[dict] = []

    def finding(rule, level, message, source):
        findings.append({"rule": rule, "level": level,
                         "message": message, "source": source})

    state = spine.load_state(name)
    current_week = spine.state_clock_week(state)
    sim_places = spine.state_places(state)
    factions_by_key: dict[str, str] = {}
    for f in state.get("factions") or []:
        if isinstance(f, dict) and f.get("id"):
            factions_by_key[links.normalize_key(f.get("name") or "")] = \
                str(f["id"])
            factions_by_key[links.normalize_key(str(f["id"]))] = str(f["id"])

    try:
        idx = links.build_index(name)
    except spine.CampaignFileError as e:
        idx = None
        finding("quests/links-unreadable", "error", str(e), "lore/entities/")

    pages = _entity_pages_degraded(name, finding)

    today = datetime.date.today()
    seen_names: dict[str, str] = {}   # normalized display name -> file
    quests = []
    for p in pages:
        if p["kind"] != "quest":
            continue  # non-quest pages are invisible to this read model
        path = d / Path(*p["file"].split("/"))
        try:
            # one byte read for BOTH the text and the revision the UI hands
            # back as expected_page_revision (see places._page_snapshot)
            raw, revision, _newline = _page_snapshot(path, None)
            meta, body = _frontmatter_of(raw)
        except (OSError, ValueError, PlaceError) as e:
            # OSError: vanished mid-read; ValueError: not UTF-8; PlaceError:
            # frontmatter that does not parse — one bad page costs only
            # itself, never the model
            finding("quests/page-unreadable", "error",
                    f"quest page {p['file']} could not be read ({e})",
                    p["file"])
            continue
        meta = meta if isinstance(meta, dict) else {}

        status_raw = str(meta.get("status") or "").strip().lower()
        status = status_raw if status_raw in STATUSES else DEFAULT_STATUS
        if status_raw and status_raw not in STATUSES:
            finding("quests/unknown-status", "warning",
                    f"quest '{p['name']}' has unknown status "
                    f"'{meta.get('status')}' — treated as {DEFAULT_STATUS} "
                    f"(one of: {', '.join(STATUSES)})", p["file"])

        place_names = _id_list(meta.get("places"))
        faction_names = _id_list(meta.get("factions"))
        due = _parse_due(meta)
        record = {
            "page": Path(p["file"]).stem,
            "file": p["file"],
            "name": p["name"],
            "kind": "quest",
            "status": status,
            "given_by": str(meta.get("given_by") or "").strip(),
            "places": place_names,
            "place_ids": _resolve_place_ids(place_names, idx, sim_places),
            "factions": faction_names,
            "faction_ids": _resolve_faction_ids(faction_names,
                                                factions_by_key),
            "due_week": due["due_week"],
            "due_date": due["due_date"],
            "level": str(meta.get("level") or "").strip(),
            "note": p.get("note") or "",
            "body": p.get("body") or "",
            "revision": revision,
        }
        if due["due_invalid"]:
            record["due_invalid"] = due["due_invalid"]
            finding("quests/bad-due", "warning",
                    f"quest '{p['name']}' has due "
                    f"'{due['due_invalid']}' — not a week number or ISO "
                    "date, so no timer is tracked", p["file"])
        if status_raw != status:
            record["status_raw"] = str(meta.get("status") or "")
        record["overdue"] = _is_overdue(record, current_week, today)
        nk = links.normalize_key(record["name"])
        if nk in seen_names:
            finding("quests/duplicate-name", "warning",
                    f"two quests are named '{record['name']}' "
                    f"({seen_names[nk]} and {record['file']}) — the name "
                    "is ambiguous; address them by page stem", record["file"])
        else:
            seen_names[nk] = record["file"]
        quests.append(record)

    quests.sort(key=lambda q: q["name"].lower())
    counts = {s: 0 for s in STATUSES}
    for q in quests:
        counts[q["status"]] += 1
    return {"quests": quests,
            "by_page": {q["page"]: q for q in quests},
            "findings": findings,
            "counts": counts}


def quest_view(name: str, page: str) -> dict:
    """One quest: the page + backlinks (v1 — matching engine events via
    place_ids/subjects are a stretch goal). Unknown page -> QuestNotFound
    (404). `page` is the lore/entities filename stem, as the lore-page view
    the panel opens uses."""
    doc = load(name)
    record = doc["by_page"].get(str(page or "").strip())
    if record is None:
        raise QuestNotFound(f"no such quest: {page}")
    try:
        backlinks = links.backlinks_for(name, record["file"])
    except spine.CampaignFileError:
        backlinks = []
    return {"quest": record, "backlinks": backlinks,
            "findings": doc["findings"], "counts": doc["counts"]}


# ------------------------------------------------------------------ mutation


def set_status(name: str, page: str, status: str,
               expected_page_revision: str | None = None) -> dict:
    """Flip `status` on the quest page's frontmatter — the ONE reviewed
    short write quests own. The page is re-read under the campaign lock
    (caller holds it) and merged, so body and unknown frontmatter keys are
    preserved; expected_page_revision (the revision the view rendered)
    makes an external Obsidian edit a 409 instead of a merge. Unknown
    status -> QuestError (422); unknown page -> QuestNotFound (404); a
    write that would change nothing is a noop, not a snapshot."""
    status = str(status or "").strip().lower()
    if status not in STATUSES:
        raise QuestError(
            f"status must be one of: {', '.join(STATUSES)} (got "
            f"{status or 'nothing'})")
    doc = load(name)
    record = doc["by_page"].get(str(page or "").strip())
    if record is None:
        raise QuestNotFound(f"no such quest: {page}")
    path = spine.campaign_dir(name) / Path(*record["file"].split("/"))
    if not path.is_file():
        raise QuestNotFound(f"no such entity page: {record['file']}")
    # one byte snapshot for BOTH the content and its baseline (see
    # places._page_snapshot): hashing after the parse could pair old text
    # with a newer revision and let publication accept a stale write over
    # an external edit
    raw, base_revision, newline = _page_snapshot(path, expected_page_revision)
    meta, body = _frontmatter_of(raw)
    meta = meta or {}
    if str(meta.get("status") or "").strip().lower() == status:
        return {"page": record["file"], "name": record["name"],
                "status": status, "state": "noop", "transaction": None}
    result = _locked_publish(
        name, [{"path": record["file"],
                "text": _changed_page_text(
                    raw, {**meta, "status": status}, body,
                    {"status": status}, newline)}],
        operation=f"quest {record['name']} status -> {status}",
        base_revisions={record["file"]: base_revision})
    return {"page": record["file"], "name": record["name"],
            "status": status, **result}


# ----------------------------------------------------------------- briefing


def briefing_section(name: str, *, since_week: int = 0,
                     current_week=None,
                     today: datetime.date | None = None) -> dict:
    """The quests section briefing.build composes in:

    overdue   `due` in the past (week or date) while the quest is still
              open (resolved and abandoned both owe no timer) — listed
              FIRST, always, whatever else qualifies
    active    status `active` quests whose place_ids/faction_ids intersect
              the places/factions changed in the covered week range (the
              events' place_ids and actors after since_week)
    hooks_not_shown   the hooked count as ONE honest line, never a list

    The section is independent of the briefing's `focus` filter: an open
    timer does not stop being open because the DM is reading about
    something else."""
    doc = load(name)
    state = spine.load_state(name)
    if current_week is None:
        current_week = spine.state_clock_week(state)
    today = today or datetime.date.today()

    touched_places: set[str] = set()
    touched_factions: set[str] = set()
    for ev in state.get("events") or []:
        if not isinstance(ev, dict):
            continue
        week = ev.get("week")
        if not isinstance(week, int) or week <= since_week:
            continue
        touched_places.update(_id_list(ev.get("place_ids")))
        actor = str(ev.get("actor") or "").strip()
        if actor and actor != "world":
            touched_factions.add(actor)

    def intersects(q: dict) -> bool:
        return bool(set(q["place_ids"]) & touched_places
                    or set(q["faction_ids"]) & touched_factions)

    # overdue recomputed here so a caller-supplied clock (tests, previews)
    # is honored exactly like load()'s own
    for q in doc["quests"]:
        q["overdue"] = _is_overdue(q, current_week, today)
    overdue = sorted((q for q in doc["quests"] if q["overdue"]),
                     key=lambda q: (q["due_week"] is None,
                                    q["due_week"] if q["due_week"]
                                    is not None else 0,
                                    q["due_date"] or ""))
    active = [q for q in doc["quests"]
              if q["status"] == "active" and not q["overdue"]
              and intersects(q)]
    hooked = sum(1 for q in doc["quests"] if q["status"] == "hooked")
    return {"overdue": overdue, "active": active,
            "hooks_not_shown": hooked, "counts": doc["counts"],
            "findings": doc["findings"]}


def due_label(record: dict) -> str:
    """The human form of a quest's timer: 'week 6' or '2026-10-08'."""
    if record.get("due_week") is not None:
        return f"week {record['due_week']}"
    if record.get("due_date"):
        return str(record["due_date"])
    return ""


# ---------------------------------------------------------------------- CLI


def _cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="studio/quests.py",
        description="Quests as first-class entities (read model + status)")
    ap.add_argument("campaign")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lst = sub.add_parser("list", help="every quest, grouped by status")
    lst.add_argument("--json", action="store_true")

    show = sub.add_parser("show", help="one quest: page + backlinks")
    show.add_argument("page", help="page stem (or the quest's name)")
    show.add_argument("--json", action="store_true")

    setp = sub.add_parser("set", help="flip one quest's status")
    setp.add_argument("page", help="page stem (or the quest's name)")
    setp.add_argument("status", help="one of: " + ", ".join(STATUSES))

    args = ap.parse_args(argv)

    def resolve(doc: dict, key: str) -> list[dict]:
        """The page stem is the key; a display-name fallback keeps
        `show 'The Troll's Tithe'` working like the panel's row — but a
        name shared by two quests (load() flagged it) is REFUSED, never
        guessed: `set` flipping the wrong quest is the one mistake this
        CLI must not make."""
        hit = doc["by_page"].get(key)
        if hit is not None:
            return [hit]
        nk = links.normalize_key(key)
        return [q for q in doc["quests"]
                if links.normalize_key(q["name"]) == nk]

    def pick(doc: dict, key: str):
        """resolve() -> one record, or (exit code, message) for 0/2+n."""
        matches = resolve(doc, key)
        if len(matches) > 1:
            return 2, ("'" + key + f"' names {len(matches)} quests — use a"
                       " page stem: "
                       + ", ".join(m["page"] for m in matches))
        if not matches:
            return 1, f"no such quest: {key}"
        return 0, matches[0]

    if args.cmd == "list":
        doc = load(args.campaign)
        if args.json:
            print(json.dumps(doc, indent=2, ensure_ascii=False))
            return 0
        c = doc["counts"]
        print(f"quests in {args.campaign} — {len(doc['quests'])} total "
              f"(hooked {c['hooked']}, active {c['active']}, "
              f"resolved {c['resolved']}, abandoned {c['abandoned']})")
        for status in STATUSES:
            group = [q for q in doc["quests"] if q["status"] == status]
            if not group:
                continue
            print(f"{status} ({len(group)})")
            for q in group:
                bits = [q["name"]]
                if q["given_by"]:
                    bits.append(f"by {q['given_by']}")
                if q["due_week"] is not None or q["due_date"]:
                    bits.append("due " + due_label(q))
                if q["overdue"]:
                    bits.append("OVERDUE")
                print(f"  {' — '.join(bits)}  ({q['page']})")
        for f in doc["findings"]:
            print(f"finding [{f['level']}] {f['rule']}: {f['message']}")
        return 0

    if args.cmd == "show":
        doc = load(args.campaign)
        code, record = pick(doc, args.page)
        if code:
            print(record, file=sys.stderr)
            return code
        view = quest_view(args.campaign, record["page"])
        if args.json:
            print(json.dumps(view, indent=2, ensure_ascii=False))
            return 0
        q = view["quest"]
        bits = [q["status"]]
        if q["given_by"]:
            bits.append(f"given by {q['given_by']}")
        if q["due_week"] is not None or q["due_date"]:
            bits.append("due " + due_label(q))
        if q["overdue"]:
            bits.append("OVERDUE")
        print(f"{q['name']} — {', '.join(bits)}")
        print(q["file"])
        if q["places"]:
            print("places: " + ", ".join(q["places"]))
        if q["factions"]:
            print("factions: " + ", ".join(q["factions"]))
        if view["backlinks"]:
            print("mentioned in:")
            for b in view["backlinks"]:
                print(f"  - {b['source_kind']} {b['source_file']}:"
                      f"{b['line']}")
        return 0

    # set
    doc = load(args.campaign)
    code, record = pick(doc, args.page)
    if code:
        print(record, file=sys.stderr)
        return code
    with spine.campaign_lock(args.campaign):
        out = set_status(args.campaign, record["page"], args.status)
        if out.get("transaction"):
            # the same finalize the route performs (finalize_publication):
            # snapshot_result + after_snapshot, so the transaction's
            # staging is pruned and the manifest records its snapshot —
            # canon.snapshot alone leaves both behind (ticket 09)
            snap = canon.snapshot_result(
                args.campaign,
                f"campaign/{args.campaign}: quest {out.get('name')} -> "
                f"{out.get('status')}")
            publication.after_snapshot(args.campaign, out["transaction"],
                                       snap)
    print(f"{out.get('name')}: status -> {out.get('status')} "
          f"({out.get('state')})")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
