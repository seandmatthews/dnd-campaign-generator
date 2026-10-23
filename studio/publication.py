"""Transactional multi-file publication + revision checks for canon writes.

One approval = several file writes (timeline + pending file + entity pages).
Each write is atomic (tmp + os.replace), but the SEQUENCE is not: a crash
mid-approval used to leave a half-published week or half-approved batch.
This module makes the sequence recoverable:

  1. publish() stages everything FIRST — after-images and a manifest under
     <campaign>/.studio-transactions/<tx>/ — then replaces live files one by
     one, then verifies every target against its after-image before marking
     the transaction complete. A crash at any point leaves an open
     transaction on disk.
  2. recover_campaign() (called from server.py's lifespan, before serving)
     rolls every open transaction FORWARD: a target still at its before-state
     gets its after-image; a target already at its after-state is done; a
     target matching NEITHER is an external edit we never overwrite — the
     transaction is parked as a conflict and the campaign's publication-path
     writers answer 409 until the DM resolves it (apply the staged outcome,
     or discard the transaction and keep the external bytes). Applying
     finalizes the publication: the forced bytes are git-snapshotted, the
     transaction's bookkeeping completes, and the snapshot outcome rides
     in the resolve answer.
  3. Receipts make approvals idempotent across retries and restarts: a
     completed approval records its API result keyed by a caller-supplied
     idempotency key; the same key returns the recorded result instead of
     re-applying.

Revision contract (the anti-stale half):
  - file_revision(): SHA-256 of exact file bytes, or the ABSENT sentinel —
    never mtime. publish() re-checks caller-supplied base revisions under
    the campaign lock immediately before the first live write; a mismatch
    raises RevisionConflict and writes nothing. The recovery path is
    minimizing the check/write window, NOT a guarantee against editors that
    ignore application locks — exclusive cross-process editing is not
    guaranteed (documented; Obsidian holds no lock we can see).
  - fingerprint_dir(): membership+content hash of a directory — creating or
    deleting a file invalidates it even when every surviving file is
    unchanged. Generation records it for the sets it read (all entity
    pages); approval checks it.

Storage rules:
  - Everything lives in .studio-transactions/ at the campaign root — outside
    every lore/entity/pending glob, so no reader ever mistakes recovery data
    for canon. A .gitignore inside keeps the payloads out of the campaigns
    repo's snapshots (git add -A -- <campaign> must not commit them).
  - Retention is bounded (TX_PAYLOAD_CAP, ticket 58): a payload kept for the
    snapshot retry is pruned once history catches up, and the oldest
    retained ones are pruned past the cap — on a git-less install the
    retry never comes and unbounded growth would be the only alternative.
  - All target paths are validated into the campaign (no traversal, no
    symlink escape) and never inside .studio-transactions itself.
  - Temp files are unique per target (tempfile.mkstemp in the target's own
    directory), never one globally reused name.

Locking: publish()/resolve() must run under the caller's per-campaign lock
(review approve/delete take it blocking; submit_campaign_job holds it for
long jobs). This module adds no campaign lock of its own — nesting one under
the held lock would deadlock (Lock is not reentrant).

Canon writes come in exactly TWO tiers, by design:
  1. TRANSACTIONAL (this module): anything multi-file, anything carrying a
     proposal's base revisions, anything whose retry must be idempotent.
  2. LOCK-ONLY: single-file DM actions that append or flip one record
     (briefing.reveal/revoke, knowledge.reconcile, the spine's own typed
     writers). They hold the campaign lock, write via
     spine.write_text_atomic, and git-snapshot via canon — no transaction,
     no receipt. That is enough BECAUSE they touch one file and have no
     stale-input window worth re-checking; a second writer of the same tier
     is serialized by the lock. A new canon writer joins tier 2 only with
     those three properties, and tier 1 otherwise.
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

import spine

log = logging.getLogger("studio.publication")

TX_DIR_NAME = ".studio-transactions"
MANIFEST_SCHEMA = 1
RECEIPT_CAP = 200

# Ticket 58: retained payloads are BOUNDED. A transaction kept for the
# snapshot retry holds full before/after images, and on a git-less install
# (the release bundle ships no git) the state never leaves
# NO_SNAPSHOT_STATES — the retry can never come, so without a cap every
# accept, quest flip and knowledge record accumulates forever. The
# before-image's undo value is already gone the moment any later snapshot
# succeeds (the retry prunes it), so the oldest payloads are pure growth.
TX_PAYLOAD_CAP = 20

# Sentinel for "file does not exist" — deliberately not hex, so it can never
# collide with a sha256 digest.
ABSENT = "absent"

# Publication-path writers refuse a campaign with unresolved recovery
# conflicts (PublicationConflictError -> 409). Reads stay open: the summary
# and review payloads report the conflict so the DM can resolve it.
NO_SNAPSHOT_STATES = ("failed", "unavailable")

# The snapshot outcomes that finish a publication's bookkeeping — the
# complement of NO_SNAPSHOT_STATES: history recorded, or nothing differed.
SNAPSHOT_OK_STATES = ("committed", "no_change")


class PublicationError(Exception):
    """Publication failed mid-flight; the open transaction is left on disk
    for boot recovery. Never raised for caller-fixable conflicts."""


class RevisionConflict(Exception):
    """A base revision no longer matches (stale draft / external edit).
    Carries the affected campaign-relative paths — routes map this to a
    409 with a refresh/regenerate instruction. Nothing was written."""


class PublicationConflictError(Exception):
    """The campaign has unresolved recovery conflicts; publication-path
    writers must refuse until the DM resolves them."""


# ---------------------------------------------------------------- revisions


def file_revision(path: Path) -> str:
    """SHA-256 hex of the file's exact bytes, or ABSENT when it does not
    exist. Read errors propagate: guessing a revision would defeat the
    check."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ABSENT
    except IsADirectoryError:
        raise
    return hashlib.sha256(data).hexdigest()


def revision_of_bytes(text: str) -> str:
    """The revision a staged write will produce: sha256 over the exact bytes
    publication puts on disk — utf-8, LF newlines (write_bytes, no newline
    translation; a Windows text-mode write would put CRLF on disk and the
    post-write verification would hash different bytes than it predicted)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint_dir(path: Path) -> str:
    """Membership+content fingerprint of one directory: sha256 over the
    sorted 'name<TAB>revision' lines of its direct children (files only,
    revisions via file_revision). ABSENT when the directory itself is
    missing. Creating or deleting a child changes the fingerprint even when
    every surviving file is byte-identical."""
    if not path.is_dir():
        return ABSENT
    lines = sorted(
        f"{p.name}\t{file_revision(p)}" for p in path.iterdir() if p.is_file())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def fingerprint_dir_after(campaign_dir: Path, rel_dir: str,
                          writes: list) -> str:
    """fingerprint_dir as it will read once `writes` land: the batch's own
    files replace or create direct children (revision = the staged bytes')
    and deletes drop them; untouched children keep their on-disk revisions
    (the transaction is atomic, so no mid-state is ever observable). A
    draft's input_fingerprints must carry forward across its own approval —
    approving one entry stubs pages into the pinned lore/entities, and a
    stale fingerprint would 409 the next entry in the same draft. ABSENT
    when the directory does not exist and the batch creates nothing in it."""
    d = campaign_dir / rel_dir
    members: dict = {}
    if d.is_dir():
        members = {p.name: file_revision(p)
                   for p in d.iterdir() if p.is_file()}
    prefix = rel_dir.rstrip("/") + "/"
    for w in writes:
        rel = str(w.get("path") or "")
        rest = rel[len(prefix):] if rel.startswith(prefix) else None
        if rest is None or "/" in rest:
            continue  # outside the directory, or not a direct child
        if w.get("delete"):
            members.pop(rest, None)
        else:
            members[rest] = revision_of_bytes(w.get("text") or "")
    if not members and not d.is_dir():
        return ABSENT
    lines = sorted(f"{name}\t{rev}" for name, rev in members.items())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _safe_target(campaign_root: Path, rel: str) -> Path:
    """Validate one campaign-relative target path. Rejects empty/absolute
    paths, '..' segments, control characters, symlink escapes (resolve()),
    and anything inside the transaction area itself."""
    text = str(rel or "").strip().replace("\\", "/")
    if (not text or text.startswith("/") or ":" in text
            or ".." in text.split("/")
            or any(ord(ch) < 32 for ch in text)):
        raise PublicationError(f"unsafe publication target: {rel!r}")
    root = campaign_root.resolve()
    path = (campaign_root / text).resolve()
    try:
        inside = path.relative_to(root).as_posix()
    except ValueError:
        raise PublicationError(
            f"publication target escapes the campaign: {rel!r}")
    if inside.split("/", 1)[0] == TX_DIR_NAME:
        raise PublicationError(
            f"publication target is inside the transaction area: {rel!r}")
    return campaign_root / Path(*PurePosixPath(inside).parts)


# --------------------------------------------------------------- tx storage


def _tx_root(campaign_dir: Path) -> Path:
    root = campaign_dir / TX_DIR_NAME
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        # keep recovery payloads out of canon snapshots: git add -A -- <campaign>
        # would otherwise commit before/after images and churn the history
        (root / ".gitignore").write_text("*\n", encoding="utf-8")
    return root


def _tx_path(campaign_dir: Path, tx_id: str) -> Path:
    if (not tx_id or "/" in tx_id or "\\" in tx_id or ".." in tx_id
            or tx_id.startswith(".")):
        raise PublicationError(f"invalid transaction id: {tx_id!r}")
    return _tx_root(campaign_dir) / tx_id


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp's fd must be closed BEFORE the replace — an open handle on the
    # tmp file itself is exactly the sharing violation _replace retries for
    fd, name = tempfile.mkstemp(dir=str(path.parent),
                                prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    _replace(tmp, path)


def _replace(tmp: Path, path: Path) -> None:
    """One retry loop for the whole studio: the spine's (a concurrent
    reader holding the destination open makes os.replace raise
    PermissionError for an instant on Windows)."""
    spine._replace_with_retry(tmp, path)


def _write_image(tx_dir: Path, kind: str, rel: str, text: str) -> None:
    """One before/after image under tx_dir/kind/<rel> (posix rel path).
    Bytes, not text mode: the stored image must be byte-identical to what
    the live write produces."""
    path = tx_dir / kind / Path(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _read_image(tx_dir: Path, kind: str, rel: str) -> str:
    """A staged image read back EXACTLY as _write_image stored it: bytes +
    decode, never text mode — universal-newline reads fold CRLF into LF,
    and the re-applied file would no longer hash to the manifest's
    after-revision (recovery must reproduce the staged bytes byte for
    byte)."""
    return (tx_dir / kind / Path(*rel.split("/"))).read_bytes().decode(
        "utf-8")


def _tmp_in(target: Path) -> Path:
    """A unique temp file in the TARGET's directory (same volume -> the
    replace is atomic; unique -> two writers never collide on one name)."""
    fd, name = tempfile.mkstemp(dir=str(target.parent),
                                prefix=target.name + ".", suffix=".tmp")
    os.close(fd)
    return Path(name)


def _apply_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_in(target)
    try:
        tmp.write_bytes(text.encode("utf-8"))
        _replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _apply_delete(target: Path) -> None:
    try:
        target.unlink()
    except FileNotFoundError:
        pass  # already absent == the intended after-state


# ------------------------------------------------------------------- status


def _load_manifest(tx_dir: Path) -> dict:
    try:
        doc = json.loads((tx_dir / "manifest.json").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def transactions(name: str) -> list[dict]:
    """Every transaction record for a campaign (newest first), for the
    summary/review readers: id, operation, status, and for conflict-parked
    ones the affected files. An empty/absent area yields []."""
    d = spine.campaign_dir(name)
    root = d / TX_DIR_NAME
    out = []
    if root.is_dir():
        for tx_dir in sorted(root.iterdir(), reverse=True):
            if not tx_dir.is_dir() or tx_dir.name == "receipts":
                continue
            m = _load_manifest(tx_dir)
            if not m:
                continue
            out.append({
                "transaction": m.get("transaction") or tx_dir.name,
                "operation": str(m.get("operation") or "unknown"),
                "status": str(m.get("status") or "unknown"),
                "created": str(m.get("created") or ""),
                "conflicts": list(m.get("conflicts") or []),
                "snapshot": m.get("snapshot"),
                "idempotency_key": m.get("idempotency_key"),
                "auto": bool(m.get("auto")),
            })
    return out


def transaction_for_key(name: str, key: str) -> dict | None:
    """The COMPLETE transaction recorded under an idempotency key, if any —
    the crash-window answer for "was this already applied?": a publication
    that crashed after applying (receipt never written) and was recovered
    by boot is findable here, so a repeat can return the original result
    instead of hitting the duplicate guards."""
    if not key:
        return None
    for t in transactions(name):
        if t["status"] == "complete" and t.get("idempotency_key") == key:
            return t
    return None


def unresolved_conflicts(name: str) -> list[dict]:
    return [t for t in transactions(name) if t["status"] == "conflict"]


def ensure_publishable(name: str) -> None:
    """Publication-path writers call this first: an unresolved recovery
    conflict blocks new publications (the pending files and state the new
    write would build on are exactly what is in question), and so does an
    OPEN transaction — a publication that crashed mid-flight and has not
    been recovered. Building on half-published state converts the crash
    into a boot-time conflict that parks the whole campaign; refusing here
    keeps it a restart-away-from-fixed condition instead."""
    open_txs = [t for t in transactions(name) if t["status"] == "open"]
    if open_txs:
        raise PublicationConflictError(
            "an earlier publication crashed mid-flight and awaits "
            "recovery — restart the studio to recover it, then write "
            f"(transactions: {', '.join(t['transaction'] for t in open_txs)})")
    conflicts = unresolved_conflicts(name)
    if conflicts:
        files = sorted({f for c in conflicts for f in c["conflicts"]})
        raise PublicationConflictError(
            "recovery conflict for this campaign — a previous publication "
            "crashed and a file was edited externally; resolve it first "
            f"(transactions: {', '.join(c['transaction'] for c in conflicts)}"
            f"; affected: {', '.join(files)})")


# ------------------------------------------------------------------ publish


def publish(name: str, writes: list[dict], *, operation: str,
            base_revisions: dict | None = None,
            idempotency_key: str | None = None,
            auto: bool = False) -> dict:
    """Publish a set of file changes transactionally.

    writes: [{"path": campaign-rel posix path, "text": str}, ...] and
            [{"path": ..., "delete": True}, ...] for removals.
    base_revisions: {rel: revision} the caller verified the user actually
            viewed — re-checked here, under the campaign lock, immediately
            before the first live write. Mismatch -> RevisionConflict, no
            writes at all.
    auto: True when a job approved this publication itself (auto-accept,
            ticket 02) — recorded in the manifest as "auto": true so every
            auto-approval is identifiable in the publication record.

    Caller holds the campaign lock. Returns
    {"state": "published"|"noop", "transaction": tx_id|None}."""
    d = spine.campaign_dir(name)
    ensure_publishable(name)

    targets = []
    for w in writes or []:
        rel = str(w.get("path") or "")
        target = _safe_target(d, rel)
        delete = bool(w.get("delete"))
        text = None if delete else str(w.get("text"))
        if not delete and text is None:
            raise PublicationError(f"write for {rel} carries no text")
        targets.append({
            "path": target.relative_to(d.resolve()).as_posix(),
            "delete": delete,
            "after": ABSENT if delete else revision_of_bytes(text or ""),
            "before": file_revision(target),
            "_text": text,
        })

    # base-revision check, immediately before staging+writing: from here to
    # the last replace is the whole race window, and nothing user-visible
    # happens inside it
    def _show(rev: str) -> str:
        return rev[:12] if rev != ABSENT else "absent"

    for rel, expected in (base_revisions or {}).items():
        target = _safe_target(d, str(rel))
        current = file_revision(target)
        if current != expected:
            raise RevisionConflict(
                f"{target.relative_to(d.resolve()).as_posix()} changed on "
                f"disk (expected {_show(str(expected))}, found "
                f"{_show(current)})")

    if all(t["before"] == t["after"] for t in targets):
        return {"state": "noop", "transaction": None}

    tx_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" \
        + uuid.uuid4().hex[:8]
    tx_dir = _tx_path(d, tx_id)
    tx_dir.mkdir(parents=True)

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "transaction": tx_id,
        "operation": operation,
        "campaign": name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "open",
        # staged flips True once the images are on disk — a tx that died
        # before that applied NOTHING (staging precedes every live write),
        # so recovery drops it instead of parking the campaign
        "staged": False,
        "idempotency_key": idempotency_key,
        "targets": [{k: v for k, v in t.items() if not k.startswith("_")}
                    for t in targets],
        "conflicts": [],
        "snapshot": None,
    }
    if auto:
        # the job-as-approver marker: a publication record that says
        # "no human read this before it landed" stays greppable forever
        manifest["auto"] = True
    # manifest before anything else: an OSError during image staging must
    # not leave a manifest-less orphan that transactions() and recovery
    # both skip forever
    _write_json(tx_dir / "manifest.json", manifest)
    try:
        for t in targets:
            if t["_text"] is not None:
                _write_image(tx_dir, "after", t["path"], t["_text"])
            if t["before"] != ABSENT:
                # preserve the before-image: recovery decisions and the DM's
                # "what was here" both read the manifest's ground truth
                _write_image(tx_dir, "before", t["path"],
                             _read_live(d, t["path"]))
        manifest["staged"] = True
        _write_json(tx_dir / "manifest.json", manifest)
        # ---- staging done; from here the transaction WILL be applied ----
        for t in targets:
            if t["before"] != t["after"]:
                if t["delete"]:
                    _apply_delete(d / Path(*t["path"].split("/")))
                else:
                    _apply_text(d / Path(*t["path"].split("/")), t["_text"])
        # complete only when every live file matches its after-image
        for t in targets:
            current = file_revision(d / Path(*t["path"].split("/")))
            if current != t["after"]:
                raise PublicationError(
                    f"verification failed for {t['path']} after write "
                    f"(expected {t['after'][:12]}, found {current[:12]})")
    except Exception as e:
        # the manifest stays status=open: boot recovery completes or parks it
        log.error("publication %s failed mid-flight (%s); left for recovery",
                  tx_id, e)
        raise
    manifest["status"] = "complete"
    _write_json(tx_dir / "manifest.json", manifest)
    return {"state": "published", "transaction": tx_id}


def _read_live(d: Path, rel: str) -> str:
    """The live file's exact bytes as text — no universal-newline folding,
    so a before-image round-trips like the after-image does."""
    p = d / Path(*rel.split("/"))
    try:
        data = p.read_bytes()
    except FileNotFoundError:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


# ----------------------------------------------------------------- recovery


# Boot waits this long for a campaign lock held by a separately launched
# CLI mid-write (session_notes, places) — seconds at most; a holder past
# this is wedged, and that campaign's recovery defers to the next boot
# rather than hanging the server's startup behind it.
RECOVERY_LOCK_WAIT_S = 60.0


def recover_campaign(name: str) -> dict:
    """Boot-time repair for one campaign: roll open transactions forward.

    A target at its before-revision gets its staged after-image; already at
    after-revision stays; matching neither is an external edit — the
    transaction parks as a conflict, live bytes untouched. Complete
    transactions whose history snapshot failed get one snapshot retry
    (pruned on success, retained otherwise). Never raises for one bad
    transaction — it parks and the rest recover.

    Ticket 26 decision — recovery takes the campaign lock: it rewrites
    live files and snapshots history, the same writers the lock
    disciplines. Boot is the cheap case (nothing else in this process
    contends), and the historical lock-free version was only reachable
    with two server processes on one campaigns root, which the one-port
    bind already precludes — the lock closes even that. The wait is
    bounded (RECOVERY_LOCK_WAIT_S): a campaign still locked after it is
    left untouched, its transactions stay open, and the next boot
    recovers them (reported as {"skipped": true})."""
    out = {"recovered": [], "conflicts": [], "pruned": [], "dropped": [],
           "capped": []}
    root = spine.campaign_dir(name) / TX_DIR_NAME
    if not root.is_dir():
        return out
    lock = spine.campaign_lock(name)
    if not lock.acquire(timeout=RECOVERY_LOCK_WAIT_S):
        log.warning("campaign %s is locked by another writer — publication "
                    "recovery skipped this boot; open transactions stay "
                    "open for the next one", name)
        out["skipped"] = True
        return out
    try:
        return _recover_campaign_locked(name, root, out)
    finally:
        lock.release()


def _recover_campaign_locked(name: str, root: Path, out: dict) -> dict:
    """recover_campaign's roll-forward loop, run with the campaign lock
    held (the caller acquired it)."""
    for tx_dir in sorted(root.iterdir()):
        if not tx_dir.is_dir() or tx_dir.name == "receipts":
            continue
        m = _load_manifest(tx_dir)
        if not m or m.get("status") not in ("open", None):
            if m.get("status") == "complete":
                _retry_snapshot(name, tx_dir, m, out)
            continue
        tx_id = m.get("transaction") or tx_dir.name
        # staging never finished -> nothing was applied (staging precedes
        # every live write), so drop the orphan instead of parking the
        # campaign as a conflict it cannot resolve. Legacy open manifests
        # predate the staged flag: assume staged and recover as before.
        if not m.get("staged", True):
            shutil.rmtree(tx_dir, ignore_errors=True)
            log.info("dropped unstaged transaction %s for %s "
                     "(crashed before any live write)", tx_id, name)
            out["dropped"].append(tx_id)
            continue
        conflicts = []
        try:
            for t in m.get("targets") or []:
                target = _safe_target(spine.campaign_dir(name), t["path"])
                current = file_revision(target)
                if current == t["after"]:
                    continue  # already applied (crash between replaces)
                if current == t["before"]:
                    if t.get("delete"):
                        _apply_delete(target)
                    else:
                        text = _read_image(tx_dir, "after", t["path"])
                        # the staged image must hash to the manifest's
                        # after-revision BEFORE it may overwrite anything:
                        # a corrupt image parks as a conflict instead of
                        # writing bytes the transaction can never complete
                        # over
                        if revision_of_bytes(text) != t["after"]:
                            conflicts.append(t["path"])
                            continue
                        _apply_text(target, text)
                else:
                    conflicts.append(t["path"])
                    continue
                # completing requires the live bytes to BE the after-state
                if file_revision(target) != t["after"]:
                    conflicts.append(t["path"])
        except (PublicationError, OSError) as e:
            log.error("recovery of %s could not proceed (%s) — parked", tx_id, e)
            conflicts.append(f"(recovery error: {e})")
        if conflicts:
            m["status"] = "conflict"
            m["conflicts"] = conflicts
            _write_json(tx_dir / "manifest.json", m)
            log.warning("transaction %s parked as conflict for %s: %s",
                        tx_id, name, ", ".join(conflicts))
            out["conflicts"].append(tx_id)
        else:
            m["status"] = "complete"
            _write_json(tx_dir / "manifest.json", m)
            log.info("recovered transaction %s for %s (rolled forward)",
                     tx_id, name)
            out["recovered"].append(tx_id)
            _retry_snapshot(name, tx_dir, m, out)
    # ticket 58: boot is also where a git-less install's backlog gets
    # bounded — every complete transaction's snapshot retry just came back
    # unavailable, and nothing else prunes them
    out["capped"].extend(cap_retained_transactions(root.parent))
    return out


def _retry_snapshot(name: str, tx_dir: Path, manifest: dict, out: dict) -> None:
    """A completed transaction whose git snapshot failed keeps its payload
    until history catches up; boot retries once."""
    if not manifest.get("snapshot") \
            or manifest["snapshot"].get("state") not in NO_SNAPSHOT_STATES:
        return
    import canon  # deferred: canon imports spine only; avoids any cycle
    snap = canon.snapshot_result(
        name, str(manifest.get("operation") or "campaign publication")
        + " (snapshot retry)")
    if snap["state"] in SNAPSHOT_OK_STATES:
        manifest["snapshot"] = snap
        _write_json(tx_dir / "manifest.json", manifest)
        _prune(tx_dir)
        out["pruned"].append(manifest.get("transaction"))


def resolve(name: str, tx_id: str, resolution: str) -> dict:
    """DM decision for a conflict-parked transaction: 'apply' forces the
    staged outcome over the external edit (an explicit choice — recovery
    never does this on its own); 'discard' abandons the transaction and
    keeps the live bytes. Apply finalizes like any completed publication —
    the git snapshot is taken, recorded in the manifest, and returned — so
    the DM-forced outcome lands in the undo history instead of sitting on
    disk uncommitted. Caller holds the campaign lock."""
    d = spine.campaign_dir(name)
    tx_dir = _tx_path(d, tx_id)
    if not tx_dir.is_dir():
        raise PublicationError(f"no such transaction: {tx_id}")
    m = _load_manifest(tx_dir)
    if m.get("status") != "conflict":
        raise PublicationError(
            f"transaction {tx_id} is {m.get('status')!r}, not a conflict")
    if resolution == "discard":
        _prune(tx_dir)
        log.info("transaction %s discarded for %s (live bytes stand)",
                 tx_id, name)
        return {"resolved": tx_id, "resolution": "discard"}
    if resolution != "apply":
        raise PublicationError(
            f"resolution must be 'apply' or 'discard', got {resolution!r}")
    for t in m.get("targets") or []:
        target = _safe_target(d, t["path"])
        if t.get("delete"):
            _apply_delete(target)
        else:
            # same gate as recovery: a staged image that does not hash to
            # the manifest's after-revision never touches the live bytes
            text = _read_image(tx_dir, "after", t["path"])
            if revision_of_bytes(text) != t["after"]:
                raise PublicationError(
                    f"staged image for {t['path']} does not match the "
                    "manifest — the transaction cannot be applied; discard "
                    "it to keep the live bytes")
            _apply_text(target, text)
        current = file_revision(target)
        if current != t["after"]:
            raise PublicationError(
                f"verification failed for {t['path']} after apply "
                f"(expected {t['after'][:12]}, found {current[:12]}) — the "
                "transaction stays parked; discard it or try again")
    import canon  # deferred: canon imports spine; publication must not
    m["status"] = "complete"
    m["conflicts"] = []
    m["snapshot"] = canon.snapshot_result(
        name, f"campaign/{name}: recovery apply ({tx_id})")
    _write_json(tx_dir / "manifest.json", m)
    if m["snapshot"].get("state") in SNAPSHOT_OK_STATES:
        _prune(tx_dir)
    else:
        log.warning("recovery apply %s for %s: history snapshot %s — "
                    "payload retained for the snapshot retry",
                    tx_id, name, m["snapshot"].get("state"))
    log.info("transaction %s applied over external edits for %s", tx_id, name)
    return {"resolved": tx_id, "resolution": "apply",
            "snapshot": m["snapshot"]}


def _prune(tx_dir: Path) -> None:
    import shutil
    shutil.rmtree(tx_dir, ignore_errors=True)


def cap_retained_transactions(campaign_dir: Path) -> list:
    """Prune retained-payload transactions beyond TX_PAYLOAD_CAP, oldest
    first (tx ids are timestamp-prefixed, so name order IS time order).
    Only COMPLETE transactions whose snapshot state is failed/unavailable
    are candidates — exactly the class after_snapshot retains for the
    snapshot retry. Open and conflict-parked transactions are live
    recovery state and are never touched. Returns the pruned ids.

    A pruned transaction may still be referenced by a receipt (receipts
    are capped separately): receipt_with_snapshot then simply cannot
    backfill the snapshot field, and repeat-accept idempotency falls back
    to the routes' duplicate guards — both documented degradations."""
    root = campaign_dir / TX_DIR_NAME
    if not root.is_dir():
        return []
    candidates = []
    for tx_dir in root.iterdir():
        if not tx_dir.is_dir() or tx_dir.name == "receipts":
            continue
        m = _load_manifest(tx_dir)
        snap = m.get("snapshot")
        if (m.get("status") == "complete" and isinstance(snap, dict)
                and snap.get("state") in NO_SNAPSHOT_STATES):
            candidates.append(tx_dir)
    candidates.sort(key=lambda p: p.name, reverse=True)
    pruned = []
    for tx_dir in candidates[TX_PAYLOAD_CAP:]:
        _prune(tx_dir)
        pruned.append(tx_dir.name)
    if pruned:
        log.info("pruned %d retained transaction payload(s) beyond the cap "
                 "of %d, oldest first: %s", len(pruned), TX_PAYLOAD_CAP,
                 ", ".join(pruned))
    return pruned


# ----------------------------------------------------------------- receipts


def _receipt_path(name: str, key: str) -> Path:
    if not key or any(ch not in "0123456789abcdef" for ch in key.lower()) \
            or len(key) > 64:
        raise PublicationError("idempotency key must be a hex string")
    return _tx_root(spine.campaign_dir(name)) / "receipts" / f"{key.lower()}.json"


def receipt(name: str, key: str) -> dict | None:
    """The recorded API result for a completed approval, or None."""
    try:
        path = _receipt_path(name, key)
    except (PublicationError, spine.CampaignNotFound):
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def receipt_with_snapshot(name: str, key: str) -> dict | None:
    """receipt(), with the snapshot backfilled from the recorded transaction
    when the receipt predates that field — the complete idempotent-repeat
    answer for the PROPOSAL accept flow (proposal_flows), its only caller.
    accept_locked re-records its receipt WITH the snapshot once known
    (ticket 28), so the backfill only serves receipts written before that
    re-record (crash between finalize and the re-record, or an old receipt)
    whose transaction is still on disk. Review approvals (routers/review)
    read publication.receipt directly over their own idempotency keys —
    two receipt flows by design, since they approve different things; this
    helper is not shared with them (ticket 16 corrected the docstring that
    claimed it was)."""
    prior = receipt(name, key)
    if prior is None:
        return None
    if "snapshot" not in prior:
        snap = transaction_snapshot(name, prior.get("transaction"))
        if snap is not None:
            prior = {**prior, "snapshot": snap}
    return prior


def record_receipt(name: str, key: str, result: dict) -> None:
    """Record one approval's result under its idempotency key. A repeat with
    the same key returns the recorded result instead of re-applying — the
    crash-between-publish-and-record window still falls back to the routes'
    existing duplicate guards (id collision, title+text identity)."""
    path = _receipt_path(name, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, result)
    _cap_receipts(path.parent)


def _cap_receipts(receipts: Path) -> None:
    try:
        receipts_list = sorted(receipts.glob("*.json"),
                               key=lambda p: p.stat().st_mtime)
    except OSError:
        return  # a receipt deleted under us: capping is best effort
    for old in receipts_list[:-RECEIPT_CAP]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass


def transaction_snapshot(name: str, tx_id: str | None) -> dict | None:
    """The snapshot bookkeeping recorded for a published transaction (its
    manifest's `snapshot`), or None. Read-only: replayed accepts use it to
    answer with the same snapshot field the live path returned."""
    if not tx_id:
        return None
    m = _load_manifest(spine.campaign_dir(name) / TX_DIR_NAME / tx_id)
    snap = m.get("snapshot") if isinstance(m, dict) else None
    return snap if isinstance(snap, dict) else None


def after_snapshot(name: str, tx_id: str | None, snap: dict) -> None:
    """The route's snapshot bookkeeping after a published transaction:
    committed/no_change prunes the staging; failed/unavailable retains it
    (recovery material for the snapshot retry), records why, and caps the
    retained set (ticket 58) — on a git-less install the retry never
    comes, and unbounded retention is pure growth."""
    if not tx_id:
        return
    d = spine.campaign_dir(name)
    tx_dir = d / TX_DIR_NAME / tx_id
    if not tx_dir.is_dir():
        return
    m = _load_manifest(tx_dir)
    if not m:
        return
    m["snapshot"] = snap
    _write_json(tx_dir / "manifest.json", m)
    if snap.get("state") in SNAPSHOT_OK_STATES:
        _prune(tx_dir)
    else:
        log.warning("publication %s: history snapshot %s — payload retained "
                    "for the snapshot retry", tx_id, snap.get("state"))
    cap_retained_transactions(d)
