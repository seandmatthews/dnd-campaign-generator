"""DM briefing + player disclosure (Phase 6): deterministic, no LLM pass.

The DM briefing answers "what do I need on screen at the table?": approved
changes in a week range, affected factions, unresolved decisions (stale
facts, pending reviews, a pending proposal — always LABELED as pending,
never as canon), and links to the supporting records. It is capped: the
first ten items with an honest "N more not shown" count — never a silent
truncation of history.

The player handout is the separate, deliberate step: a DM reveal moves a
fact's approved text into the party-disclosure record, and the handout
contains ONLY what was revealed. An empty disclosure set produces an empty
handout with an explanation — never a fallback to the DM briefing. Private
goals, mechanical tables, faction-only knowledge, pending proposals, and
file titles that would leak secrets have no path into this export. Public
in-world is not automatically party-known: the reveal IS the discovery.
"""

import datetime
import logging

import knowledge
import spine

log = logging.getLogger("studio.briefing")

BRIEFING_CAP = 10
DISCLOSURE_FILE = "simulation/party-knowledge.yaml"
DISCLOSURE_SCHEMA = 1


# ------------------------------------------------------------------ briefing


def _page_ref(name: str, actor: str):
    """The entity page for an actor, when one exists — wiki-link label only
    (the file name IS the page title, which is public canon by design)."""
    try:
        for p in spine.entity_pages(name):
            if p["id"] == actor or p["name"].lower() == str(actor).lower():
                return {"type": "page", "file": p["file"], "label": p["name"]}
    except Exception:  # noqa: BLE001 — refs are best effort
        return None
    return None


def _chronicle_ref(name: str, week):
    for c in spine.chronicles(name):
        if c["week"] == week:
            return {"type": "chronicle", "file": c["file"],
                    "label": f"week {week} — {c['title']}"}
    return None


def build(name: str, since_week: int = 0, focus=None) -> dict:
    """The DM briefing as structured items + markdown. Deterministic: same
    campaign state, same briefing."""
    state = spine.load_state(name)
    clock = (state.get("meta") or {}).get("clock") or {}
    current_week = clock.get("week")
    focus = [str(f).strip() for f in (focus or []) if str(f).strip()]
    items = []

    def relevant(text, actors):
        if not focus:
            return True
        low = str(text).lower()
        return any(a in low or a in actors for a in
                   [f.lower() for f in focus])

    # 1. approved changes: the week's events (the mechanical record)
    for ev in state.get("events") or []:
        week = ev.get("week")
        if not isinstance(week, int) or week <= since_week:
            continue
        actor = str(ev.get("actor") or "world")
        if not relevant(ev.get("text"), {actor}):
            continue
        refs = [r for r in (_page_ref(name, actor),
                            _chronicle_ref(name, week)) if r]
        items.append({"kind": "event", "week": week, "actor": actor,
                      "title": ev.get("text"), "status": "canon",
                      "refs": refs})

    # 2. rumors born in the range
    for r in state.get("rumors") or []:
        born = r.get("born_week")
        if not isinstance(born, int) or born <= since_week:
            continue
        if not relevant(r.get("claim"), set()):
            continue
        items.append({"kind": "rumor", "week": born, "actor": None,
                      "title": r.get("claim"),
                      "detail": f"fidelity {r.get('fidelity')}",
                      "status": "canon", "refs": []})

    # 3. unresolved DM decisions — always labeled pending, never canon
    import proposals as proposal_store
    pending = proposal_store.find_pending(name)
    if pending:
        items.append({
            "kind": "proposal", "week": pending.get("proposed_week"),
            "actor": None,
            "title": f"proposal {pending.get('proposal_id')} awaiting review "
                     f"(base week {pending.get('base_week')} -> "
                     f"{pending.get('proposed_week')})",
            "detail": "proposed, NOT canon — the world has not advanced",
            "status": "pending", "refs": [],
        })
    stale = [f for f in knowledge.with_staleness(name, knowledge.load(name))
             if f.get("stale")]
    for f in stale:
        items.append({"kind": "stale_fact", "week": None, "actor": None,
                      "title": f.get("text"),
                      "detail": "source changed since approval — reconcile",
                      "status": "needs_attention", "refs": []})
    pend_count = spine.count_pending(name)
    if pend_count:
        items.append({"kind": "pending_reviews", "week": None, "actor": None,
                      "title": f"{pend_count} pending review file(s) in the "
                               "Review tab",
                      "status": "needs_attention", "refs": []})

    items.sort(key=lambda i: (i.get("week") is None,
                              -(i.get("week") or 0)))
    total = len(items)
    shown = items[:BRIEFING_CAP]
    doc = {
        "campaign": name,
        "since_week": since_week,
        "current_week": current_week,
        "focus": focus,
        "total": total,
        "shown": len(shown),
        "omitted": max(0, total - len(shown)),
        "items": shown,
    }
    # the quests section (tickets/06-quests-first-class.md): overdue
    # timers first, active quests intersecting the covered range, the
    # hooked count as one honest line. Deferred import: quests borrows the
    # raw-page byte contract from places.py, and places imports this
    # module — a top-level import here would make the triangle a cycle.
    # A vault page that does not parse degrades to a section error, the
    # same way _page_ref survives one — one bad hand-edit must not take
    # the whole briefing down.
    import quests as quest_store
    try:
        doc["quests"] = quest_store.briefing_section(
            name, since_week=since_week, current_week=current_week)
    except spine.CampaignFileError as e:
        doc["quests"] = {"overdue": [], "active": [], "hooks_not_shown": 0,
                         "counts": {}, "findings": [], "error": str(e)}
    doc["markdown"] = render_markdown(name, doc)
    return doc


def render_markdown(name: str, doc: dict) -> str:
    """Durable Markdown for the same items — the browser view and the
    on-disk/handout copy share one renderer so they cannot diverge."""
    lines = [f"# DM briefing — {name}",
             ""]
    if doc.get("current_week") is not None:
        lines.append(f"World clock: week {doc['current_week']}. "
                     f"Covering changes after week {doc.get('since_week')}.")
    if doc.get("focus"):
        lines.append(f"Focus: {', '.join(doc['focus'])}.")
    lines.append("")
    lines.append(f"{doc['total']} item(s)" +
                 (f" — showing {doc['shown']}, {doc['omitted']} more not "
                  "shown (raise the cap or narrow the range)" if doc["omitted"]
                  else ""))
    lines.append("")
    for item in doc["items"]:
        when = f" (week {item['week']})" if item.get("week") is not None else ""
        status = {"canon": "", "pending": " — PROPOSED, NOT CANON",
                  "needs_attention": " — NEEDS YOUR ATTENTION"}.get(
            item.get("status"), "")
        actor = f" [{item['actor']}]" if item.get("actor") else ""
        lines.append(f"- **{item.get('kind')}**{when}{actor}: "
                     f"{item.get('title')}{status}")
        for ref in item.get("refs") or []:
            lines.append(f"  - see {ref['type']}: `{ref['file']}` "
                         f"({ref['label']})")
    _quests_markdown(doc, lines)
    if not doc["items"] and not _quests_present(doc):
        lines.append("_Nothing to report in this range._")
    return "\n".join(lines) + "\n"


def _quests_present(doc: dict) -> bool:
    q = doc.get("quests") or {}
    return bool(q.get("overdue") or q.get("active")
                or q.get("hooks_not_shown") or q.get("error"))


def _quests_markdown(doc: dict, lines: list) -> None:
    """The quests section of the briefing (quests.briefing_section): overdue
    timers FIRST — a timer the GM forgot is the one that kills the campaign
    — then the active quests touching the covered range, then the hooked
    count as one line. Rendered even when the week range has no items: an
    open timer does not care what week it is."""
    if not _quests_present(doc):
        return
    import quests as quest_store
    q = doc["quests"]
    lines.append("")
    lines.append("## Quests (open story)")
    if q.get("error"):
        lines.append(f"- _Quests could not be read: {q['error']}_")
        return
    for rec in q.get("overdue") or []:
        lines.append(f"- **OVERDUE** {rec['name']} (status {rec['status']},"
                     f" due {quest_store.due_label(rec)}) — "
                     f"`{rec['file']}`")
    for rec in q.get("active") or []:
        where = ", ".join(rec["places"]) if rec["places"] else \
            ", ".join(rec["factions"])
        patron = f", given by {rec['given_by']}" if rec["given_by"] else ""
        lines.append(f"- {rec['name']}{patron} — active"
                     + (f" at {where}" if where else "")
                     + f" — `{rec['file']}`")
    hooks = q.get("hooks_not_shown") or 0
    if hooks:
        lines.append(f"_{hooks} hook{'s' if hooks != 1 else ''} not "
                     "shown._")


# ---------------------------------------------------------------- disclosure


def _disclosure_path(name: str):
    return spine.campaign_dir(name) / DISCLOSURE_FILE


def load_disclosure(name: str) -> dict:
    doc = spine._safe_load(_disclosure_path(name))
    if not isinstance(doc, dict) or not isinstance(doc.get("revealed"), list):
        doc = {"schema_version": DISCLOSURE_SCHEMA, "revealed": []}
    return doc


def _save_disclosure(name: str, doc: dict) -> None:
    spine.write_text_atomic(_disclosure_path(name), spine.dump_yaml(doc))


def disclosure_after_image(store: dict, facts: list, entries: list) -> dict:
    """PURE document transform: return a NEW disclosure store with the
    given reveal entries appended. entries: {fact_id, week?} — each fact
    must exist, be current, and be PUBLIC in `facts` (the approved store's
    fact list): faction-restricted knowledge is not the party's just
    because the DM likes the idea. Duplicates of an already-revealed fact
    are dropped (revealing twice discloses nothing new). Raises ValueError
    naming the fact when a reveal is impossible — the caller decides
    whether that refuses a whole batch (session prepare) or one request
    (the reveal route)."""
    import copy
    by_id = {f.get("id"): f for f in (facts or []) if isinstance(f, dict)}
    out = copy.deepcopy(store or {"schema_version": DISCLOSURE_SCHEMA,
                                  "revealed": []})
    out.setdefault("revealed", [])
    have = {r.get("fact_id") for r in out["revealed"]}
    for entry in entries or []:
        fid = str((entry or {}).get("fact_id") or "")
        fact = by_id.get(fid)
        if fact is None:
            raise ValueError(f"no such fact: {fid}")
        if fact.get("visibility") != "public" or \
                fact.get("status") not in (None, "current"):
            raise ValueError(
                "only current public facts can be revealed to the party — "
                f"this fact is {fact.get('visibility')!r}/"
                f"{fact.get('status') or 'current'} ({fid})")
        if fid in have:
            continue  # already disclosed — idempotent
        out["revealed"].append({
            "fact_id": fid,
            "text": fact.get("text"),
            "revealed_week": entry.get("week"),
            "revealed": datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds"),
        })
        have.add(fid)
    return out


def reveal(name: str, fact_id: str, week: int | None = None) -> dict:
    """Move ONE current public fact into the party-disclosure record. Only
    `public` facts can be revealed — faction-restricted knowledge is not
    the party's just because the DM likes the idea (that's a deliberate
    second decision via a new fact). LOCK-ONLY canon writer (publication.py's
    tier 2): caller holds the campaign lock; this writes one file via the
    spine's atomic writer and the route git-snapshots."""
    doc = knowledge.load(name)
    store = disclosure_after_image(
        load_disclosure(name), doc.get("facts", []),
        [{"fact_id": fact_id, "week": week}])
    _save_disclosure(name, store)
    return store


def revoke(name: str, fact_id: str) -> dict:
    """LOCK-ONLY canon writer (publication.py's tier 2), like reveal."""
    store = load_disclosure(name)
    store["revealed"] = [r for r in store["revealed"]
                         if r.get("fact_id") != fact_id]
    _save_disclosure(name, store)
    return store


def handout_markdown(name: str) -> str:
    """The exportable party handout: ONLY the revealed facts' text. No
    mechanics, no faction-only knowledge, no pending proposals, no file
    paths or titles beyond what the revealed text itself says."""
    meta = spine.load_campaign_yaml(name)
    store = load_disclosure(name)
    lines = [f"# {meta.get('title') or name} — what the party knows", ""]
    if not store["revealed"]:
        lines.append("_Nothing has been disclosed to the party yet. Reveal "
                     "facts from the campaign's knowledge records to build "
                     "this handout._")
    for r in store["revealed"]:
        lines.append(f"- {r.get('text')}")
        if r.get("revealed_week") is not None:
            lines.append(f"  (known since week {r['revealed_week']})")
    lines.append("")
    lines.append("_DM note: this handout lists only deliberately disclosed "
                 "facts. It is safe to share on its own — sharing your "
                 "campaign folder is not._")
    return "\n".join(lines) + "\n"
