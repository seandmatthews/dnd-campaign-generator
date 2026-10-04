"""lore/timeline.yaml — one owner for load, append, and byte-format.

Two writers mint timeline documents: review approval (pending lore entries)
and week acceptance (entries staged in the proposal bundle). Both used to
carry their own copy of the t-id allocation rules and the exact
yaml.safe_dump kwargs — but publication's revision checks hash the exact
bytes of this file, so the serialization is a cross-router contract that
must not drift. It lives here now, once.
"""

import re
from pathlib import Path

import spine
import yaml

T_ID_RE = re.compile(r"^t\d+$")
T_NUM_RE = re.compile(r"^t0*(\d+)$")


class TimelineBadFile(Exception):
    """lore/timeline.yaml does not parse or has an unsupported shape.
    Routes map this to a 422 naming the file."""


class TimelineConflict(Exception):
    """The entry is already in the timeline (exact title+text identity).
    Routes map this to a 409: re-approving after a mid-crash must not
    silently duplicate. An id COLLISION alone no longer refuses (ticket
    37): stage-time ids are advisory, so a colliding entry is renumbered
    to the next free t-number instead."""


def timeline_path(campaign_dir: Path) -> Path:
    return campaign_dir / "lore" / "timeline.yaml"


def load(campaign_dir: Path) -> tuple[dict | list | None, list]:
    """(doc, entries) — doc is None when the file is absent; a bare list and
    an {entries: [...]} mapping are both read and preserved."""
    path = timeline_path(campaign_dir)
    if not path.is_file():
        return None, []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        raise TimelineBadFile(f"lore/timeline.yaml does not parse: {e}")
    if isinstance(doc, list):
        return doc, doc
    if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
        return doc, doc["entries"]
    return doc, []


def existing_tnums(entries: list) -> set:
    out = set()
    for e in entries:
        if isinstance(e, dict):
            m = T_NUM_RE.match(str(e.get("id") or ""))
            if m:
                out.add(int(m.group(1)))
    return out


def append(doc, entries: list, chosen: list):
    """The new timeline document with `chosen` appended, keeping ^t\\d+$ ids.
    An entry whose own t-number is free keeps it; a missing OR colliding id
    gets the next free t-number — stage-time ids are advisory (loregen mints
    them against the timeline it read, but a week accepted in between can
    take those numbers first, ticket 37). A batch whose title+text is
    already in the timeline is refused (TimelineConflict) — that is the
    crash-between-writes recovery path: the append landed, the pending
    rewrite did not, and re-approving would silently duplicate.
    Returns the doc; nothing is written here."""
    existing = existing_tnums(entries)
    seen = {(str(e.get("title") or ""), str(e.get("text") or ""))
            for e in entries if isinstance(e, dict)}
    for entry in chosen:
        eid = str(entry.get("id") or "")
        if eid and T_ID_RE.match(eid) and int(eid[1:]) not in existing:
            existing.add(int(eid[1:]))  # also guards duplicates within the batch
            continue  # keep the entry's own t-id
        identity = (str(entry.get("title") or ""), str(entry.get("text") or ""))
        if identity != ("", "") and identity in seen:
            raise TimelineConflict(
                f"an entry titled {identity[0]!r} with this exact text "
                f"is already in lore/timeline.yaml — if a previous "
                f"approve crashed midway, delete it from the pending "
                f"file instead of re-approving")
        next_num = (max(existing) + 1) if existing else 1
        entry["id"] = f"t{next_num:04d}"
        existing.add(next_num)
        seen.add(identity)

    if doc is None:
        doc = {"entries": list(entries)}
    if isinstance(doc, list):
        doc.extend(chosen)
    elif isinstance(doc, dict):
        doc.setdefault("entries", []).extend(chosen)
    else:
        raise TimelineBadFile("lore/timeline.yaml has an unsupported shape")
    return doc


def dump(doc) -> str:
    """THE serialization for lore/timeline.yaml — publication's
    revision_of_bytes hashes exactly these bytes (spine.dump_yaml's exact
    output), so every writer of the file must produce them through here."""
    return spine.dump_yaml(doc)


# ----------------------------------------------- week-entry derivation
#
# The engine's week output becomes PROPOSED timeline entries (staged in the
# bundle, nothing lands in lore/pending until accept) — one owner for the
# derivation, shared by the week flow and anything else staging weeks.

# clause/sentence punctuation that may end a proposed timeline title
# (apostrophes and quotes deliberately excluded — possessives are not cuts)
_CLAUSE_PUNCT_RE = re.compile(r"[—–,;:.!?()\[\]]")


def propose_title(text: str, max_words: int = 7, min_words: int = 4) -> str:
    """One short title for a proposed timeline entry. Mechanical, per the
    design doc: the chronicle prose is already written and reviewable — the
    timeline only needs its skeleton. Take the first 4-7 words of the event
    text, cut at the first clause-punctuation mark once min_words are in
    (an em-dash or comma at word 1-3 does not cut; possessives never cut)."""
    head = (text or "").split()[:max_words]
    for i, word in enumerate(head):
        if i >= min_words and _CLAUSE_PUNCT_RE.search(word):
            cut = " ".join(head[:i]).strip(" \t—–-,;:.!?()[]\"'")
            if cut:
                return cut
    return " ".join(head).strip()


def build_week_entries(week: int, events: list) -> list:
    """One proposed timeline entry per NON-WORLD event of the proposed week
    (world events are scenery, not faction history). Pure: the entries are
    staged INTO THE PROPOSAL BUNDLE — nothing lands in lore/pending or the
    timeline until the whole week is accepted."""
    entries = []
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("week") != week:
            continue
        if str(ev.get("actor") or "").lower() == "world":
            continue
        text = str(ev.get("text") or "").strip()
        if not text:
            continue
        entries.append({
            "id": f"pw{week}-{len(entries) + 1}",
            "when": f"week {week}",
            "title": propose_title(text),
            "text": text,
            "source": f"sim:week-{week}",
        })
    return entries
