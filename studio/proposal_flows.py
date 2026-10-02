"""The proposal lifecycle as a SERVICE — accept, discard, and the staging
helpers every proposal kind shares.

This module exists because the accept path is not routing: it is
idempotency keys, receipt repair, crash-window recovery, multi-file
write-set assembly, session bookkeeping callbacks, chronicle linkify, and
git snapshots. It used to live in routers/simulation.py, and the CLI
(session_notes.py) had to import that ROUTER and catch fastapi's
HTTPException to reuse it — the layering inversion that birthed this
module. Everything here raises ProposalFlowError (status + detail); the
router maps nothing, the server has one handler, the CLI catches one type.
"""

import contextlib
import hashlib
import json
import logging
import re
from pathlib import Path

import canon
import job_history
import jobs
import links
import proposals
import publication
import sessions
import spine
import timeline

_log = logging.getLogger("studio.proposal_flows")

# proposal kinds that change ONLY simulation/state.yaml — no chronicles, no
# timeline entries. The accept path treats everything else as a full week.
STATE_ONLY_KINDS = frozenset({"session_effects", "goal_updates",
                              "project_changes"})


class ProposalFlowError(Exception):
    """A caller-fixable (4xx) or mid-flight (5xx) flow failure. `status` is
    the HTTP status the server handler answers with; the CLI prints the
    detail. No FastAPI types cross this module."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ------------------------------------------------------------- the flows


def accept(name: str, pid: str) -> dict:
    """Publish the WHOLE pending proposal (the route entry point): takes
    the campaign lock fast-fail, then runs accept_locked."""
    with _lock(name, "accepting the proposal"):
        return accept_locked(name, pid)


def accept_locked(name: str, pid: str, *, auto: bool = False) -> dict:
    """The accept flow proper — the CALLER holds the campaign lock (the
    route takes it fast-fail; an auto-accepting job runs under the lock it
    already holds, ticket 02). With auto=True the publication record
    carries "auto": true — the job itself is the approver. Idempotent,
    transactional, revision-validated, exactly like the manual accept."""
    # a stable key for this exact accept: a repeat (double click, retry,
    # restart) returns the recorded result instead of applying twice
    key = hashlib.sha256(
        json.dumps(["accept-proposal", name, pid]).encode("utf-8")).hexdigest()
    _ensure_publishable(name)
    prior = publication.receipt_with_snapshot(name, key)
    if prior:
        # the crash window AFTER the receipt: publish landed, the status
        # flip did not. Repair it from the receipt, or a proposal that
        # is actually canon stays "pending" and blocks every new week.
        try:
            b = proposals.load(name, pid)
            if b["manifest"].get("status") == "pending":
                proposals.set_status(name, pid, "accepted",
                                     decided_note="status repaired from "
                                                  "the accept receipt")
            if b["manifest"].get("kind") == "session_changes":
                # reconstruct the draft/decision bookkeeping too: the
                # UI status is rebuilt from the receipt, never left
                # half-finished by a crash between publish and repair
                sessions.on_accepted(name, b["manifest"], b)
        except proposals.ProposalError:
            pass  # bundle gone or damaged: the receipt still answers
        return {**prior, "idempotent": True}
    try:
        bundle = proposals.load(name, pid)
    except proposals.ProposalError as e:
        raise ProposalFlowError(404, str(e))
    manifest = bundle["manifest"]
    if manifest.get("status") != "pending":
        raise ProposalFlowError(
            409,
            f"proposal {pid} is {manifest.get('status')!r}, "
            "not pending — refresh and check its state")
    prior_tx = publication.transaction_for_key(name, key)
    if prior_tx is not None:
        # the crash window: this accept was applied and then recovered
        # by boot (receipt never written). The staged outcome IS the
        # original result — finish the bookkeeping and answer once.
        result = accept_result(manifest, bundle, prior_tx["transaction"])
        publication.record_receipt(name, key, result)
        proposals.set_status(name, pid, "accepted",
                             decided_note="published via crash recovery")
        if manifest.get("kind") == "session_changes":
            sessions.on_accepted(name, manifest, bundle)
        snap = _finalize(name, prior_tx["transaction"],
                         accept_message(name, manifest, None))
        # same re-record as the live path (ticket 28): _finalize prunes a
        # committed transaction, so the receipt itself must carry the
        # snapshot for the NEXT replay to answer with the same shape
        publication.record_receipt(name, key, {**result, "snapshot": snap})
        return {**result, "snapshot": snap, "idempotent": True}
    d = spine.campaign_dir(name)

    base = dict(manifest.get("base_revisions") or {})
    writes = [{
        "path": "simulation/state.yaml",
        "text": bundle["state_text"],
    }]
    if manifest.get("kind") == "session_changes":
        # the combined transaction: state after-image (effects already
        # applied by the engine at prepare time) + the knowledge and
        # disclosure after-images + the session decision record — one
        # publication, one receipt, recover together
        extra = bundle.get("extra") or {}
        for key_, rel in (("knowledge", "simulation/knowledge.yaml"),
                          ("disclosure",
                           "simulation/party-knowledge.yaml")):
            if extra.get(key_) is not None:
                writes.append({
                    "path": rel,
                    "text": spine.dump_yaml(extra[key_]),
                })
        decision = extra.get("decision") or {}
        writes.append({
            "path": f"sessions/{manifest.get('session_id')}"
                    f"/decisions/{pid}.yaml",
            "text": sessions.decision_text(decision),
        })
        chron = None
    elif manifest.get("kind") in STATE_ONLY_KINDS:
        chron = None
    else:
        entries = bundle["entries"]
        try:
            tdoc, tentries = timeline.load(d)
            new_timeline = timeline.append(tdoc, tentries, entries)
        except timeline.TimelineConflict as e:
            raise ProposalFlowError(409, str(e))
        except timeline.TimelineBadFile as e:
            raise ProposalFlowError(422, str(e))
        writes.append({
            "path": "lore/timeline.yaml",
            "text": timeline.dump(new_timeline),
        })
        for fn in bundle["chronicles"]:
            rel = f"simulation/chronicle/{fn}"
            writes.append({"path": rel, "text": bundle["chronicles"][fn]})
            base[rel] = publication.ABSENT  # create-only: a file that
            # appeared in the meantime is an external edit -> conflict
        chron = manifest.get("chronicle") \
            or chronicle_meta_from_texts(bundle["chronicles"])
    try:
        pub = publication.publish(
            name, writes,
            operation=accept_operation(manifest)
                      + (" (auto-accepted)" if auto else ""),
            base_revisions=base, idempotency_key=key, auto=auto)
    except publication.RevisionConflict as e:
        raise ProposalFlowError(
            409,
            f"stale proposal: {e} — the campaign changed since "
            f"this was generated. Discard it and prepare a fresh "
            f"one from the new base. Nothing was written.")
    except publication.PublicationConflictError as e:
        raise ProposalFlowError(409, str(e))
    except Exception as e:  # noqa: BLE001 — mid-flight failure
        raise ProposalFlowError(
            500,
            f"the proposal could not be published ({e}) — the staged "
            "transaction is kept and will be recovered automatically "
            "when the studio restarts")

    result = accept_result(manifest, bundle, pub["transaction"])
    if auto:
        result["auto"] = True
    if pub["state"] == "noop":
        result["noop"] = True
    if manifest.get("kind") == "session_changes":
        # canon landed: freeze the draft's selected suggestions and
        # record the decision — best-effort; the receipt repairs it on
        # idempotent replay. The status rides in the response (ticket 11)
        # so a failed draft write is visible, not just logged.
        result["draft_update"] = sessions.on_accepted(name, manifest,
                                                      bundle)
    publication.record_receipt(name, key, result)
    # the week HAS landed and the receipt is recorded: a set_status
    # failure here must not surface as a 500 for a publish that
    # succeeded — the receipt repairs the status on retry
    try:
        proposals.set_status(name, pid, "accepted",
                             decided_note="published; see the receipt"
                                          + (" (auto-accepted)" if auto
                                             else ""))
    except proposals.ProposalError as e:
        _log.error("accept of %s published but could not mark the "
                   "bundle accepted (%s) — the receipt repairs it on "
                   "retry", pid, e)
        result["status_flip_failed"] = str(e)
    msg = accept_message(name, manifest, chron)
    if auto:
        msg += " (auto-accepted)"
    # vault stage 4 advisory decoration after publication, as before
    for fn in bundle["chronicles"]:
        try:
            if links.linkify_chronicle(name, fn):
                _log.info("linked lore names in chronicle/%s", fn)
        except Exception as e:  # noqa: BLE001 — advisory, never blocking
            _log.warning("chronicle linkify skipped for %s: %s", fn, e)
    snap = _finalize(name, pub["transaction"], msg)
    result["snapshot"] = snap
    # re-record the receipt WITH the snapshot (ticket 28): the record above
    # is the crash-window anchor written before the snapshot exists, and a
    # committed transaction is pruned by after_snapshot — receipt_with_
    # snapshot's manifest backfill then finds nothing, and a replay of a
    # fully-completed accept answered without the snapshot key the live
    # accept returned
    publication.record_receipt(name, key, result)
    return result


def discard(name: str, pid: str) -> dict:
    """Discard a proposal: flips the bundle's status only. The live campaign
    is untouched (it was never touched — generation staged everything), and
    the bundle stays on disk as an audit receipt. Rejected content is never
    fed into future prompts."""
    with _lock(name, "discarding the proposal"):
        try:
            manifest = proposals.set_status(
                name, pid, "discarded",
                decided_note="discarded by the DM; no canon "
                             "change ever happened")
        except proposals.ProposalError as e:
            raise ProposalFlowError(409, str(e))
        out = {"discarded": pid}
        if manifest.get("kind") == "session_changes":
            # reopen the prepared suggestions so the excluded/rest of the
            # session can be prepared again against a new base; the
            # status rides in the response like the accept's (ticket 11)
            out["draft_update"] = sessions.on_discarded(name, manifest)
        return out


# ------------------------------------------------------ result construction


def accept_result(manifest: dict, bundle: dict, tx_id: str | None) -> dict:
    """The API result for an accept — used both live and when reconstructing
    a recovered accept's answer."""
    result = {
        "accepted": manifest.get("proposal_id"),
        "week": manifest.get("proposed_week"),
        "kind": manifest.get("kind") or "simulated_week",
        "transaction": tx_id,
    }
    if manifest.get("kind") in STATE_ONLY_KINDS:
        diff = bundle["diff"]
        result["events_added"] = len(diff.get("events_added") or [])
        result["resources_changed"] = len(diff.get("resources") or {})
        result["goals_changed"] = len(diff.get("fields") or {})
        if manifest.get("kind") == "project_changes":
            result["operations"] = len(bundle.get("changes") or [])
            result["projects_added"] = len(diff.get("projects_added") or [])
            result["projects_changed"] = len(diff.get("projects_changed")
                                             or {})
    elif manifest.get("kind") == "session_changes":
        diff = bundle["diff"]
        result["session_id"] = manifest.get("session_id")
        result["events_added"] = len(diff.get("events_added") or [])
        result["resources_changed"] = len(diff.get("resources") or {})
        result["goals_changed"] = len(diff.get("fields") or {})
        result["facts_added"] = len(diff.get("facts_added") or [])
        result["facts_superseded"] = len(diff.get("facts_superseded") or [])
        result["disclosed"] = len(diff.get("disclosed") or [])
        result["suggestions"] = manifest.get("selected_suggestion_ids") or []
    else:
        result["chronicles"] = sorted(bundle["chronicles"])
        result["timeline_entries"] = len(bundle["entries"])
    return result


def accept_operation(manifest: dict) -> str:
    kind = manifest.get("kind")
    if kind == "session_effects":
        # `(x or {})`: an explicit null in a hand-edited manifest means the
        # key EXISTS with value None — .get('run_record', {}) returns None
        return (f"accept session effects ("
                f"{(manifest.get('run_record') or {}).get('source') or 'session'})")
    if kind == "goal_updates":
        return "accept goal updates from the ledger"
    if kind == "project_changes":
        return "accept project changes (create/edit/pause/resume/abandon)"
    if kind == "session_changes":
        return ("accept session changes (session "
                f"{(manifest.get('run_record') or {}).get('session_id')})")
    return f"accept week {manifest.get('proposed_week')} proposal"


def accept_message(name: str, manifest: dict, chron) -> str:
    kind = manifest.get("kind")
    if kind == "session_effects":
        source = (manifest.get("run_record") or {}).get("source") or "session"
        return f"campaign/{name}: session effects applied ({source})"
    if kind == "goal_updates":
        return f"campaign/{name}: goal updates applied (goal review)"
    if kind == "project_changes":
        return f"campaign/{name}: project changes applied"
    if kind == "session_changes":
        return (f"campaign/{name}: session changes applied (session "
                f"{(manifest.get('run_record') or {}).get('session_id')})")
    msg = f"campaign/{name}: week {manifest.get('proposed_week')}"
    if chron and chron.get("title"):
        msg += f" — {chron['title']}"
    return msg


def chronicle_meta_from_texts(texts: dict) -> dict | None:
    """The primary chronicle of a bundle's STAGED chronicles: the engine
    home's scratch copies may be long gone (another campaign's week or a
    forecast reuses it), so acceptance must read the bundle it is about to
    publish. A bundle is one run's output — usually exactly one chronicle.
    For a legacy rerun bundle carrying two, plain name-max picks the
    ORIGINAL over the rewrite ('-2' sorts before '.'): rank by (week
    prefix, rerun suffix) and take the newest rewrite, the run this bundle
    staged."""
    if not texts:
        return None

    def _rank(fn: str):
        m = re.match(r"(\d{4})-.*?(?:-(\d+))?\.md$", fn)
        return (int(m.group(1)) if m else 0,
                int(m.group(2)) if (m and m.group(2)) else 0, fn)

    fn = max(texts, key=_rank)
    title = fn
    for ln in str(texts[fn] or "").splitlines()[:1]:
        m = re.match(r"#\s*Week\s+\d+\s*[—–-]\s*(.+?)\s*$", ln)
        if m:
            title = m.group(1)
    week_m = re.match(r"(\d{4})", fn)
    return {"week": int(week_m.group(1)) if week_m else None,
            "title": title, "file": f"simulation/chronicle/{fn}"}


# -------------------------------------------------------- staging helpers


def effect_id(effect: dict) -> str:
    """Stable effect id: content-derived when the DM did not supply one, so
    re-submitting the same session cannot mint a second identity."""
    if effect.get("id"):
        return str(effect["id"])
    payload = json.dumps(effect, sort_keys=True, ensure_ascii=False)
    return "eff-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def capture_state(state_path: Path) -> tuple[str, str]:
    """The state text and its revision from ONE read. Two separate reads
    (text, then revision) let an external hand edit land in between and pin
    an after-image to revisions it was never derived from — the staleness
    check would then pass at accept time and publish over the edit."""
    data = state_path.read_bytes()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


def recheck_for_staging(name: str, *, base_revision: str, what: str) -> None:
    """The re-check every staging flow makes under the staging lock, because
    the pre-route check raced the moment the request thread didn't hold it:
    no pending proposal appeared, and the state the engine validated against
    is still canon. Raises ProposalFlowError(409); `what` names the flow in
    the message ('this session was being prepared', ...)."""
    racing = proposals.find_pending(name)
    if racing:
        raise ProposalFlowError(
            409,
            f"a proposal appeared while {what} was being prepared "
            f"({racing.get('proposal_id')}) — accept or discard it first; "
            "nothing was staged")
    current = publication.file_revision(
        spine.campaign_dir(name) / "simulation" / "state.yaml")
    if current != base_revision:
        raise ProposalFlowError(
            409,
            "simulation/state.yaml changed while "
            f"{what} was being prepared (another proposal was accepted?) "
            "— prepare again against the current state; nothing was staged")


# ------------------------------------------------------------- internals


def _lock(name: str, action: str):
    """The campaign lock for a flow step; fast-fail 409, never blocking
    (a week job may hold the lock for half an hour). Delegates to
    spine.lock_nowait — the one implementation — and maps CampaignBusy to
    the flow's error type."""

    @contextlib.contextmanager
    def cm():
        try:
            with spine.lock_nowait(name, action):
                yield
        except spine.CampaignBusy as e:
            # ticket 13: the accept/discard 409s carry the same
            # what-is-running / how-long line every other busy-409 has
            # (from the rolling history) — not just the static guess
            line = job_history.blocked_by_line(
                jobs.running_jobs(campaign=name))
            raise ProposalFlowError(409, str(e) + line) from e
    return cm()


def _ensure_publishable(name: str) -> None:
    try:
        publication.ensure_publishable(name)
    except publication.PublicationConflictError as e:
        raise ProposalFlowError(409, str(e))


def _finalize(name: str, transaction_id: str | None, message: str) -> dict:
    """The canonical post-publication snapshot (routers.common owns the
    route-flavored twin; this is the library one)."""
    snap = canon.snapshot_result(name, message)
    publication.after_snapshot(name, transaction_id, snap)
    return snap
