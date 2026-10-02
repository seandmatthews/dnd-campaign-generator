"""Next session prep — one Obsidian file per session (docs/tickets/
07-next-session-prep.md).

One click compiles what the GM brings to the table — the DM briefing's
events, the freshest rumors, the open quests, the public facts still
waiting to be revealed, and the unresolved decisions — into ONE markdown
page in the vault: lore/prep/<YYYY-MM-DD>-next-session.md. It is a
compile, not a generation: deterministic from the briefing
(briefing.build) + the quests read model + the state's rumors. No LLM
pass, no budget, no confirm gate beyond the normal campaign lock.

Rules that keep it cheap and safe:
- Regeneration never overwrites: the same date's second file gets -2
  (next-free name, like the pending lore files). The GM deletes what they
  do not want — in Obsidian; the file is disposable by design.
- Every item links back to its source of record: chronicles as relative
  markdown links (they live outside lore/), entity pages as wiki-links
  (the vault's relationship model), fact source files as relative links.
- An empty campaign produces the honest skeleton — every section present,
  each saying nothing is recorded yet. Nothing is fabricated.

CLI (the same compile the route uses):
    python studio/prep.py <campaign> [--since-week N] [--date YYYY-MM-DD]
        [--stdout]
"""

import datetime
import re
import sys
from pathlib import PurePosixPath

import briefing
import canon
import knowledge
import quests
import spine

PREP_DIR = "lore/prep"
RUMOR_CAP = 3

# the briefing's status suffixes (briefing.render_markdown), reused so the
# prep page labels pending material with the same words the briefing does
_STATUS_SUFFIX = {"pending": " — PROPOSED, NOT CANON",
                  "needs_attention": " — NEEDS YOUR ATTENTION"}


# ----------------------------------------------------------------- compile


def build(name: str, since_week: int = 0,
          today: datetime.date | None = None) -> dict:
    """The prep document: frontmatter fields + the five sections, with
    every link already resolved. Deterministic: same canon + same date,
    same bytes (markdown() below renders exactly what is on disk) — the
    date pins the quests' overdue check too, so a regeneration across a
    midnight that flips a due date reproduces the original file (ticket
    14)."""
    today = today or datetime.date.today()
    b = briefing.build(name, since_week=since_week, today=today)
    state = spine.load_state(name)
    chronicles = {c["week"]: c for c in spine.chronicles(name)}
    current_week = spine.state_clock_week(state)
    meta = spine.load_campaign_yaml(name)
    unrevealed, knowledge_findings = _unrevealed(name)
    # the briefing's own read of the same file already carries the finding
    # when the store is broken — say it once, not once per reader
    seen = {(f.get("rule"), f.get("source"))
            for f in b.get("knowledge_findings") or []}
    knowledge_findings = list(b.get("knowledge_findings") or []) + [
        f for f in knowledge_findings
        if (f.get("rule"), f.get("source")) not in seen]
    return {
        "campaign": name,
        "title": str(meta.get("title") or name),
        "date": today.isoformat(),
        # the covered range never runs backwards (P3-23): since_week at or
        # above the clock is an over-narrow range, not an error — the
        # filter honestly shows nothing (no change has happened after a
        # week that does not exist yet), and the page says the range is
        # empty instead of printing "covers weeks 11–3". CLAMP, not 422:
        # the query is answerable, and build() is the one place route and
        # CLI both pass through
        "covers_weeks": [since_week + 1, current_week]
                        if isinstance(current_week, int)
                        and since_week < current_week else [],
        "current_week": current_week if isinstance(current_week, int)
                        else None,
        "since_week": since_week,
        "events": _events(b, chronicles),
        "events_omitted": b.get("omitted") or 0,
        "rumors": _rumors(state, chronicles),
        "quests": _quests(b),
        "unrevealed": unrevealed,
        "knowledge_findings": knowledge_findings,
        "decisions": _decisions(b),
    }


def _events(b: dict, chronicles: dict) -> list:
    """Section 1 — the briefing's event items (the cap already applied):
    each with its faction page as a wiki-link and its chronicle as a
    relative link, when those records exist. Missing records mean NO link,
    never a fabricated one."""
    out = []
    for item in b.get("items") or []:
        if item.get("kind") != "event":
            continue
        page = next((r for r in item.get("refs") or []
                     if r.get("type") == "page"), None)
        chron = next((r for r in item.get("refs") or []
                      if r.get("type") == "chronicle"), None)
        out.append({
            "week": item.get("week"),
            "actor": item.get("actor"),
            "text": item.get("title"),
            "page": _wiki_link(page),
            "chronicle": _chronicle_link(chron) if chron else None,
        })
    return out


def _rumors(state: dict, chronicles: dict) -> list:
    """Section 2 — the most recent week's rumors (top RUMOR_CAP, stable
    order by id then claim): read from the state's rumor list, not the
    briefing's capped slice — the freshest hook does not stop being fresh
    because an older week's events crowded it out of the briefing."""
    rumors = [r for r in state.get("rumors") or [] if isinstance(r, dict)]
    weeks = [r.get("born_week") for r in rumors]
    weeks = [w for w in weeks if isinstance(w, int)]
    if not weeks:
        return []
    latest = max(weeks)
    fresh = sorted(
        (r for r in rumors if r.get("born_week") == latest),
        key=lambda r: (str(r.get("id") or ""), str(r.get("claim") or "")))
    out = []
    for r in fresh[:RUMOR_CAP]:
        c = chronicles.get(latest)
        out.append({
            "born_week": latest,
            "claim": str(r.get("claim") or ""),
            "fidelity": str(r.get("fidelity") or ""),
            "chronicle": _chronicle_md(c) if c else None,
        })
    return out


def _quests(b: dict) -> dict:
    """Section 3 — ticket 06's briefing section, verbatim (overdue timers
    first, active quests that touched the covered range, the hooked count
    as one honest line), each quest wiki-linked to its page. The findings
    ride along (R5): when the ONLY quest page is unreadable the section is
    empty but the error finding says so — the page must never print its
    'No quest pages yet' advice over a quest that exists but cannot be
    read."""
    q = b.get("quests") or {}

    def rec(r: dict) -> dict:
        return {"name": r["name"], "page": r["page"], "status": r["status"],
                "given_by": r.get("given_by") or "",
                "due": quests.due_label(r),
                "places": r.get("places") or [],
                "factions": r.get("factions") or []}

    return {"overdue": [rec(r) for r in q.get("overdue") or []],
            "active": [rec(r) for r in q.get("active") or []],
            "hooks_not_shown": q.get("hooks_not_shown") or 0,
            "error": q.get("error"),
            "findings": q.get("findings") or []}


def _unrevealed(name: str) -> tuple[list, list]:
    """Section 4 — current PUBLIC facts the party has not been told. The
    reveal is the discovery; this is the GM's reminder list, not player
    material. Each fact links its source file when it has one (the fact
    source file is the containing doc of record); a fact without a source
    file carries no link — none is invented. Returns (rows, findings): a
    hand-mangled knowledge.yaml costs this section alone — rows degrade to
    [] and one error finding names the file (P3-17) — instead of failing
    the whole compile."""
    try:
        facts = knowledge.load(name).get("facts", [])
    except spine.CampaignFileError as e:
        return [], [{"rule": "knowledge/unreadable", "level": "error",
                     "message": str(e), "source": knowledge.KNOWLEDGE_FILE}]
    revealed = {r.get("fact_id") for r in
                briefing.load_disclosure(name).get("revealed", [])}
    out = []
    for f in facts:
        if f.get("status") not in (None, "current"):
            continue
        if f.get("visibility") != "public" or f.get("id") in revealed:
            continue
        out.append({"id": f.get("id"), "text": f.get("text"),
                    "week": f.get("week"),
                    "source_file": f.get("source_file") or "",
                    "source_link": _vault_link(f.get("source_file") or "")})
    return out, []


def _decisions(b: dict) -> list:
    """Section 5 — the briefing's unresolved decisions (pending proposal,
    pending reviews, stale facts), carrying the briefing's own NOT-canon /
    needs-attention labels verbatim. Read from the briefing's UNCAPPED
    `decisions` key (ticket 14): its capped `items` slice sorts week-None
    decision items last, so a busy campaign silently lost every decision
    here — and the page printed a false 'nothing awaits'."""
    return [{"kind": i.get("kind"), "week": i.get("week"),
             "title": i.get("title"), "detail": i.get("detail"),
             "status": i.get("status")}
            for i in b.get("decisions") or []]


# -------------------------------------------------------------------- links


def _wiki_link(page_ref: dict | None) -> str | None:
    """An entity page as a wiki-link ([[Page]] — display names, the vault's
    relationship model). A frontmatter name that differs from the filename
    stem keeps both: [[Stem|name]]."""
    if not page_ref or not page_ref.get("file"):
        return None
    stem = PurePosixPath(page_ref["file"]).stem
    label = str(page_ref.get("label") or stem)
    return f"[[{stem}|{label}]]" if label != stem else f"[[{stem}]]"


def _chronicle_link(chron_ref: dict | None) -> str | None:
    """A chronicle as a relative markdown link — chronicles live outside
    lore/, and relative links reach them from the prep page (the ticket's
    `[week 1 — …](../../simulation/chronicle/0001-….md)`)."""
    if not chron_ref or not chron_ref.get("file"):
        return None
    return _md_link(str(chron_ref.get("label") or chron_ref["file"]),
                    _vault_rel(chron_ref["file"]))


def _chronicle_md(c: dict | None) -> str | None:
    if not c:
        return None
    return _md_link(f"week {c['week']} — {c['title']}", _vault_rel(c["file"]))


def _vault_link(campaign_path: str) -> str | None:
    """Any campaign-relative file as a link from the prep page, or None for
    an empty path."""
    if not campaign_path:
        return None
    return _md_link(campaign_path, _vault_rel(campaign_path))


def _vault_rel(campaign_path: str) -> str:
    """A campaign-relative path as it is addressed FROM lore/prep/: one `..`
    out of prep into lore/, two out to the campaign root. Always
    forward-slashes, so the link works in Obsidian on every platform."""
    parts = PurePosixPath(campaign_path).parts
    body = "/".join(parts[1:]) if parts and parts[0] == "lore" \
        else "/".join(parts)
    return ("../" if parts and parts[0] == "lore" else "../../") + body


def _md_link(label: str, target: str) -> str:
    """A markdown link whose target is safe for Obsidian: paths with spaces
    or parens ride in angle brackets (CommonMark), everything else plain —
    matching the engine's slug-form chronicle filenames."""
    url = f"<{target}>" if re.search(r"[\s()]", target) else target
    return f"[{label}]({url})"


# ----------------------------------------------------------------- markdown


def markdown(doc: dict) -> str:
    """The file's exact bytes — frontmatter + the five sections. The GET
    route serves the same string, so the printed copy cannot drift from
    the vault page. Every section heading is always present: an empty
    campaign yields the honest skeleton, a section that has nothing says
    so instead of vanishing (or worse, being invented)."""
    covers = ", ".join(str(w) for w in doc["covers_weeks"])
    lines = ["---",
             "kind: prep",
             f"date: {doc['date']}",
             f"covers_weeks: [{covers}]",
             "---",
             "",
             f"# Next session prep — {doc['title']} — {doc['date']}",
             ""]
    if doc["covers_weeks"]:
        a, b = doc["covers_weeks"]
        lines.append(f"_Compiled from the campaign canon — covers weeks "
                     f"{a}–{b}. Disposable by design: a re-run writes a "
                     "new file, it never overwrites this one._")
    elif isinstance(doc.get("current_week"), int):
        # P3-23: since_week at or above the clock — an over-narrow range,
        # clamped to "covers nothing" rather than printed as an inverted
        # pair ("covers weeks 11–3" on a week-3 clock)
        lines.append(f"_Compiled from the campaign canon — the range starts "
                     f"after the world clock (week {doc['current_week']}), "
                     f"so it covers no weeks: nothing has happened after "
                     f"week {doc['since_week']}. Disposable by design: a "
                     "re-run writes a new file, it never overwrites this "
                     "one._")
    else:
        lines.append("_Compiled from the campaign canon. The world clock "
                     "has not started, so there is no week range to "
                     "cover yet. Disposable by design: a re-run writes a "
                     "new file, it never overwrites this one._")

    lines += ["", "## Where we left off", ""]
    if doc["events"]:
        for e in doc["events"]:
            bits = []
            if e["week"] is not None:
                bits.append(f"week {e['week']}")
            if e["actor"]:
                bits.append(e["page"] or e["actor"])
            line = f"- {e['text']}"
            if bits:
                line += f" ({', '.join(bits)})"
            if e["chronicle"]:
                line += f" — {e['chronicle']}"
            lines.append(line)
        if doc.get("events_omitted"):
            # the briefing caps its own view; the prep page never lies by
            # omission — it says what it left out (ticket 14)
            n = doc["events_omitted"]
            lines.append(f"_{n} older briefing item{'' if n == 1 else 's'} "
                         "not shown — narrow the range (a higher "
                         "since-week) or read the full DM briefing._")
    else:
        lines.append("_No approved changes recorded yet._")

    lines += ["", "## Rumors worth dropping", ""]
    if doc["rumors"]:
        week = doc["rumors"][0]["born_week"]
        lines.append(f"_The freshest week's rumors (week {week}) — drop "
                     "one or two, let the party chase the rest._")
        lines.append("")
        for r in doc["rumors"]:
            fidelity = f" ({r['fidelity']})" if r["fidelity"] else ""
            tail = f" — {r['chronicle']}" if r["chronicle"] else ""
            lines.append(f"- \"{r['claim']}\"{fidelity}{tail}")
    else:
        lines.append("_No rumors in the world yet._")

    lines += ["", "## Active quests", ""]
    q = doc["quests"]
    if q.get("error"):
        lines.append(f"_Quests could not be read: {q['error']}_")
    else:
        # error-level findings ride along (R5, ticket 23): when the only
        # quest page cannot be read, the section is empty but the finding
        # says so — the "No quest pages yet" advice must never print over
        # a quest that exists but cannot be read
        errors = [f for f in q.get("findings") or []
                  if (f or {}).get("level") == "error"]
        for f in errors:
            lines.append(f"- ⚠ Quest finding — {f.get('message')} "
                         f"(`{f.get('source')}`)")
        if not (q["overdue"] or q["active"] or q["hooks_not_shown"]
                or errors):
            lines.append("_No quest pages yet — a `kind: quest` entity "
                         "page starts one (World Building tab, Quests "
                         "card)._")
        for rec in q["overdue"]:
            due = f", due {rec['due']}" if rec["due"] else ""
            lines.append(f"- **OVERDUE** [[{rec['page']}|{rec['name']}]] "
                         f"(status {rec['status']}{due})")
        for rec in q["active"]:
            where = ", ".join(rec["places"]) or ", ".join(rec["factions"])
            patron = f", given by {rec['given_by']}" if rec["given_by"] \
                else ""
            at = f" at {where}" if where else ""
            lines.append(f"- [[{rec['page']}|{rec['name']}]] — active"
                         f"{patron}{at}")
        hooks = q["hooks_not_shown"] or 0
        if hooks:
            lines.append(f"_{hooks} hook{'s' if hooks != 1 else ''} not "
                         "shown._")

    lines += ["", "## Waiting to be revealed", ""]
    knowledge_error = next(
        (f for f in doc.get("knowledge_findings") or []
         if (f or {}).get("level") == "error"), None)
    if knowledge_error:
        # P3-17: the fact store is unreadable — the honest section says
        # so instead of "no public facts waiting" over facts that may exist
        lines.append(f"_The fact store could not be read: "
                     f"{knowledge_error['message']}_")
    else:
        lines.append("_Public in the world is not party-known — the reveal "
                     "is the discovery. The GM's reminder list, not player "
                     "material._")
        lines.append("")
        if doc["unrevealed"]:
            for f in doc["unrevealed"]:
                tail = f" (source: {f['source_link']})" if f["source_link"] \
                    else ""
                lines.append(f"- {f['text']}{tail}")
        else:
            lines.append("_No public facts waiting — everything public has "
                         "been revealed to the party (or nothing public is "
                         "recorded yet)._")

    lines += ["", "## Open decisions", ""]
    if doc["decisions"]:
        for d in doc["decisions"]:
            when = f" (week {d['week']})" if d.get("week") is not None \
                else ""
            detail = f" — {d['detail']}" if d.get("detail") else ""
            suffix = _STATUS_SUFFIX.get(d.get("status"), "")
            lines.append(f"- **{d['kind']}**{when}: {d['title']}"
                         f"{suffix}{detail}")
    else:
        lines.append("_Nothing awaiting a decision — no pending "
                     "proposals, reviews, or stale facts._")

    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- write


def next_path(d, date_iso: str):
    """lore/prep/<date>-next-session.md at the next free N: the same
    date's second file is -2, its third -3 … (the pending lore files'
    lowest-free-N pattern). Regeneration NEVER overwrites — the GM deletes
    what they do not want, in Obsidian."""
    stem = f"{date_iso}-next-session"
    candidate = d / f"{stem}.md"
    n = 1
    while candidate.exists():
        n += 1
        candidate = d / f"{stem}-{n}.md"
    return candidate


def write(name: str, since_week: int = 0,
          today: datetime.date | None = None) -> dict:
    """Compile + write the next-free prep file (atomic, like every spine
    write). LOCK-ONLY canon writer (publication.py's tier 2): the caller
    holds the campaign lock and takes the canon snapshot — the reveal
    route's exact posture. Returns the campaign-relative path."""
    spine.require_campaign(name)
    doc = build(name, since_week=since_week, today=today)
    path = next_path(spine.campaign_dir(name) / "lore" / "prep",
                     doc["date"])
    spine.write_text_atomic(path, markdown(doc))
    return {"file": path.relative_to(spine.campaign_dir(name)).as_posix(),
            "doc": doc}


# ---------------------------------------------------------------------- CLI


def _cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="studio/prep.py",
        description="Compile next-session prep into one vault file "
                    "(lore/prep/<date>-next-session.md)")
    ap.add_argument("campaign")
    ap.add_argument("--since-week", type=int, default=0,
                    help="cover changes after this week (default 0)")
    ap.add_argument("--date", default=None,
                    help="ISO date for the file name (default: today)")
    ap.add_argument("--stdout", action="store_true",
                    help="print the markdown instead of writing a file")
    args = ap.parse_args(argv)
    today = datetime.date.fromisoformat(args.date) if args.date else None
    if args.stdout:
        doc = build(args.campaign, since_week=args.since_week, today=today)
        print(markdown(doc), end="")
        return 0
    with spine.campaign_lock(args.campaign):
        out = write(args.campaign, since_week=args.since_week, today=today)
        canon.snapshot(args.campaign,
                       f"campaign/{args.campaign}: next-session prep "
                       f"{out['file']}")
    print(out["file"])
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
