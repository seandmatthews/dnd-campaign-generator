"""Session-notes CLI: import, extract, prepare, accept, discard.

The same service layer the Sessions tab uses (studio/sessions.py + the
shared proposal accept/discard routes) — no second publication mechanism,
no second validation. Notes and drafts are DM material under
sessions/<session-id>/; acceptance is the only thing that touches canon.

  python studio/session_notes.py import <campaign> --title "..." \
      --notes-file notes.md [--week N] [--place east-quay]...
  python studio/session_notes.py list <campaign>
  python studio/session_notes.py show <campaign> <session-id>
  python studio/session_notes.py extract <campaign> <session-id> \
      --confirm-spend            # metered LLM run — explicit confirmation
  python studio/session_notes.py prepare <campaign> <session-id> \
      --select suggestion-xxxxxxxx [--select ...] | --all [--draft <id>]
  python studio/session_notes.py accept <campaign> <proposal-id>
  python studio/session_notes.py discard <campaign> <proposal-id>

Supports manual drafting without any LLM (skip extract; prepare drafts
built by hand through the studio editor or `show` + a YAML editor).
Standalone agentic-simulator ingestion (src/ingest.py, src/effects.py)
is untouched and independent of Studio.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import sessions as session_store
import spine


def cmd_import(args) -> int:
    if args.notes_file:
        notes = pathlib.Path(args.notes_file).read_text(encoding="utf-8")
    elif args.notes:
        notes = args.notes
    else:
        print("error: give --notes-file PATH or --notes TEXT", file=sys.stderr)
        return 2
    with spine.campaign_lock(args.campaign):
        doc = session_store.create(
            args.campaign, title=args.title, notes=notes,
            event_week=args.week, place_ids=args.place or [],
            future_reason=args.future_reason or "")
    print(f"{doc['session_id']}  week {doc['event_week']}  "
          f"revision {doc['revision']}")
    return 0


def cmd_list(args) -> int:
    rows = session_store.list_sessions(args.campaign)
    if not rows:
        print("(no sessions)")
        return 0
    for r in rows:
        print(f"{r['session_id']}  week {r['event_week']}  "
              f"notes x{r['notes_revisions']}  drafts "
              f"{len(r['drafts'])}  decisions {len(r['decisions'])}  "
              f"{r['title']}")
    return 0


def cmd_show(args) -> int:
    detail = session_store.session_detail(args.campaign, args.session_id)
    print(spine.dump_yaml(detail))
    return 0


def cmd_extract(args) -> int:
    try:
        import llm_client
        cfg = llm_client.resolve_config()
        if not cfg.get("key"):
            print("error: no LLM API key found (llm.env / LLM_API_KEY) — "
                  "extraction needs one; add suggestions manually instead",
                  file=sys.stderr)
            return 1
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with spine.campaign_lock(args.campaign):
        result = session_store.run_extraction(
            args.campaign, args.session_id,
            log=lambda line: print(f"[extract] {line}"))
    print(f"draft {result['draft_id']}: {result['suggestions']} "
          f"suggestion(s), {result['needs_attention']} need attention")
    for f in result["findings"]:
        print(f"  finding: {f}")
    if not result["suggestions"]:
        print("nothing proposed — add suggestions manually with `show` + "
              "the studio editor")
    return 0


def _resolve_selection(args, session_id: str) -> tuple:
    """(selected_ids, draft_id, expected_revision) from --all/--select."""
    doc = session_store.load(args.campaign, session_id)
    drafts = doc.get("drafts") or []
    if not drafts:
        raise session_store.SessionError(
            "this session has no draft — run extract or add suggestions "
            "in the studio editor first")
    draft_id = args.draft or drafts[-1]
    draft = session_store.load_draft(args.campaign, session_id, draft_id)
    if args.all:
        selected = [s["id"] for s in draft.get("suggestions") or []
                    if s.get("status") == "open"]
    elif args.select:
        selected = list(args.select)
    else:
        raise session_store.SessionError(
            "choose suggestions with --select ID ... (repeatable) or --all")
    return selected, draft_id, draft.get("revision")


def cmd_prepare(args) -> int:
    selected, draft_id, expected = _resolve_selection(args, args.session_id)
    # prepare takes the campaign lock itself (twice: capture + staging) —
    # do NOT hold it here (threading.Lock is not reentrant)
    result = session_store.prepare(
        args.campaign, args.session_id, selected_ids=selected,
        expected_draft_revision=expected, draft_id=draft_id)
    print(f"staged proposal {result['proposal_id']} "
          f"({len(result['selected'])} selected suggestion(s)) — nothing "
          "in the campaign has changed; accept or discard it")
    return 0


def cmd_accept(args) -> int:
    # the SAME accept path as the studio's button: validation, the one
    # transactional publication, the receipt, and the session bookkeeping
    from fastapi import HTTPException
    from routers.simulation import accept_proposal
    try:
        result = accept_proposal(args.campaign, args.proposal_id)
    except HTTPException as e:
        print(f"error: {e.detail}", file=sys.stderr)
        return 1
    print(f"accepted {result.get('accepted')} "
          f"(kind {result.get('kind')}, transaction {result.get('transaction')})")
    for k in ("events_added", "resources_changed", "goals_changed",
              "facts_added", "facts_superseded", "disclosed"):
        if k in result:
            print(f"  {k}: {result[k]}")
    return 0


def cmd_discard(args) -> int:
    from fastapi import HTTPException
    from routers.simulation import discard_proposal
    try:
        result = discard_proposal(args.campaign, args.proposal_id)
    except HTTPException as e:
        print(f"error: {e.detail}", file=sys.stderr)
        return 1
    print(f"discarded {result.get('discarded')} — no canon change ever "
          "happened")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="session_notes.py",
        description="Session notes -> reviewed campaign changes")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_campaign(sp):
        sp.add_argument("campaign")

    sp = sub.add_parser("import", help="save a session + first notes "
                                       "revision")
    with_campaign(sp)
    sp.add_argument("--title", required=True)
    sp.add_argument("--notes-file")
    sp.add_argument("--notes")
    sp.add_argument("--week", type=int, default=None)
    sp.add_argument("--place", action="append", default=[],
                    help="place id (repeatable)")
    sp.add_argument("--future-reason", default="",
                    help="required when --week is ahead of the clock")
    sp.set_defaults(fn=cmd_import)

    sp = sub.add_parser("list", help="list sessions")
    with_campaign(sp)
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("show", help="dump one session's full detail")
    with_campaign(sp)
    sp.add_argument("session_id")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("extract", help="run a bounded metered extraction")
    with_campaign(sp)
    sp.add_argument("session_id")
    sp.add_argument("--confirm-spend", action="store_true",
                    help="required: extraction spends provider credits")
    sp.set_defaults(fn=cmd_extract)

    sp = sub.add_parser("prepare", help="stage selected suggestions as one "
                                        "proposal")
    with_campaign(sp)
    sp.add_argument("session_id")
    sp.add_argument("--select", action="append", default=[],
                    help="suggestion id (repeatable)")
    sp.add_argument("--all", action="store_true",
                    help="select every open suggestion")
    sp.add_argument("--draft", help="draft id (default: the latest)")
    sp.set_defaults(fn=cmd_prepare)

    sp = sub.add_parser("accept", help="accept a prepared proposal")
    with_campaign(sp)
    sp.add_argument("proposal_id")
    sp.set_defaults(fn=cmd_accept)

    sp = sub.add_parser("discard", help="discard a prepared proposal")
    with_campaign(sp)
    sp.add_argument("proposal_id")
    sp.set_defaults(fn=cmd_discard)

    args = p.parse_args(argv)
    if args.cmd == "extract" and not args.confirm_spend:
        print("error: extract spends provider credits — re-run with "
              "--confirm-spend", file=sys.stderr)
        return 2
    try:
        return args.fn(args)
    except session_store.SessionConflict as e:
        print(f"conflict: {e}", file=sys.stderr)
        return 1
    except session_store.SessionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
