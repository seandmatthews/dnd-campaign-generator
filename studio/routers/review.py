"""Review tab API (M4) — the one queue for everything awaiting a decision.

GET unions every pending ENTRY across lore/pending-*.yaml and
simulation/pending-week-*.yaml into items, and runs verification's
structural layer (check.py, subprocess list-form args — the parent path
contains `&`) ONCE per pending file per request. Findings that cite a line
inside an entry's span attach to that entry; the rest become synthetic
{"kind": "finding"} items. The whole GET keeps a soft time budget: if the
checks blow it, remaining findings are skipped and noted — the queue must
stay fast, findings are advisory, never a gate.

approve/delete move entries through the pending-file mechanics: approved
entries append to lore/timeline.yaml (source kept, ^t\\d+$ ids preserved,
provisional ids get the next free t-number), the pending file is rewritten
without them and deleted when empty. Nothing lands unreviewed (guardrail 3):
these two routes are the ONLY writers from pending into the spine.

Approval is transactional (studio/publication.py): the timeline write, the
pending rewrite, page drafts and stub pages are ONE publication — staged
first, then applied, then verified; a crash leaves an open transaction that
boot recovery rolls forward. Each request carries the revision of the
pending file the user actually viewed (plus, for generated drafts, the
source revisions and directory fingerprints the generator recorded in the
doc); a mismatch answers 409 with the affected paths and writes nothing.
Repeated submissions return the recorded result (receipts) instead of
applying twice. Git snapshot outcomes are reported in the response, not
just logged.

All mutations run under the SHARED per-campaign lock (spine.campaign_lock,
blocking — an in-flight week/generation job finishes first).
"""

import hashlib
import json
import re
import time
from pathlib import Path, PurePosixPath

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import canon
import engines
import links
import publication
import spine
from routers.common import require_campaign
from routers.common import ensure_publishable_409

router = APIRouter()

PENDING_PATTERNS = (("lore", "pending-*.yaml"),
                    ("simulation", "pending-week-*.yaml"))
T_ID_RE = re.compile(r"^t\d+$")
T_NUM_RE = re.compile(r"^t0*(\d+)$")
_ENTRY_START_RE = re.compile(r"^- id:\s*(\S+)")

CHECK_TIMEOUT_S = 60     # per check.py run
FINDINGS_BUDGET_S = 1.5  # soft budget for all check.py runs in one GET
VAULT_FINDINGS_CAP = 20  # dangling/collision rows shown per GET


class ReviewBody(BaseModel):
    file: str
    ids: list[str] | None = None
    all: bool | None = None
    # revision of the pending file the user actually viewed (GET review
    # returns it per file). Absent = legacy client: the apply still runs the
    # other guards, but a content change since the view cannot be detected.
    expected_revision: str | None = None


# ------------------------------------------------------------------ plumbing


def _pending_file(campaign_dir: Path, rel: str) -> tuple[Path, str]:
    """Validate that rel names a pending file inside THIS campaign.

    Returns the resolved path and its campaign-relative posix form. Anything
    that escapes the campaign (.., absolute paths, non-pending names) is a
    4xx — approve/delete must never write outside the pending trees.
    """
    text = str(rel or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ".." in text.split("/"):
        raise HTTPException(status_code=422,
                            detail=f"not a pending file of this campaign: {rel!r}")
    if any(ord(ch) < 32 for ch in text):  # NUL/control chars: resolve() raises raw ValueError
        raise HTTPException(status_code=422,
                            detail=f"not a pending file of this campaign: {rel!r}")
    root = campaign_dir.resolve()
    path = (campaign_dir / text).resolve()
    try:
        inside = path.relative_to(root).as_posix()
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"not a pending file of this campaign: {rel!r}")
    if not any(PurePosixPath(inside).match(f"{sub}/{pat}")
               for sub, pat in PENDING_PATTERNS):
        raise HTTPException(status_code=422,
                            detail=f"not a pending file of this campaign: {rel!r}")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no such pending file: {inside}")
    return path, inside


def _load_pending(path: Path, inside: str) -> dict:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        raise HTTPException(status_code=422,
                            detail=f"{inside} does not parse as YAML: {e}")
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise HTTPException(status_code=422,
                            detail=f"{inside} has no entries list")
    return doc


def _select(doc: dict, body: ReviewBody) -> list:
    """The chosen entries (indices), refusing unknown ids with a 404."""
    entries = doc["entries"]
    if body.all:
        return list(range(len(entries)))
    wanted = [str(i) for i in (body.ids or [])]
    if not wanted:
        raise HTTPException(status_code=422, detail="need ids or all:true")
    found = {str(e.get("id")): k for k, e in enumerate(entries)
             if isinstance(e, dict) and e.get("id") is not None}
    missing = [i for i in wanted if i not in found]
    if missing:
        raise HTTPException(status_code=404,
                            detail=f"no such pending id(s): {', '.join(missing)}")
    seen, chosen = set(), []
    for i in wanted:
        k = found[i]
        if k not in seen:
            seen.add(k)
            chosen.append(k)
    return chosen


# ------------------------------------------------------------------ findings


def _structural_findings(path: Path, inside: str, deadline, notes: list):
    """check.py on one pending file — once per file per request via
    engines.run_check (the shared invocation). Skipped with a note when the
    request blows its budget; never blocks approval."""
    if deadline is not None and time.monotonic() > deadline:
        notes.append(f"findings skipped for {inside}: the queue took too long "
                     "to assemble — refresh to retry")
        return None
    try:
        return engines.run_check(path, timeout=CHECK_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — advisory, never blocking
        notes.append(f"structural check failed for {inside}: {e}")
    return None


def _entry_spans(text: str) -> dict:
    """{entry id: (start line, end line)} from the raw pending YAML — the
    loose file/line match used to attach findings to the entries they cite."""
    spans: dict = {}
    current, start = None, 0
    for n, line in enumerate(text.splitlines(), start=1):
        m = _ENTRY_START_RE.match(line)
        if m:
            if current is not None:
                spans[current] = (start, n - 1)
            current, start = m.group(1), n
    if current is not None:
        spans[current] = (start, float("inf"))
    return spans


def _finding_item(f, inside: str) -> dict:
    rule = str(f.get("rule") or "finding")
    line = f.get("line")
    note = str(f.get("note") or "")
    quote = str(f.get("quote") or "").strip()
    body = note + (f"\n\n{quote}" if quote else "")
    return {
        "id": f"finding:{inside}:{line}:{rule}",
        "kind": "finding",
        "source": "verification/check.py",
        "title": rule,
        "body": body,
        "level": str(f.get("level") or "suggestion"),
        "line": line,
        "findings": [],
        "file": inside,
    }


def _split_findings(findings, inside: str, spans: dict) -> tuple[dict, list]:
    """(findings per entry id, synthetic finding items for the rest)."""
    per_entry: dict = {}
    loose: list = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        line = f.get("line")
        hit = next((eid for eid, (a, b) in spans.items()
                    if isinstance(line, int) and a <= line <= b), None)
        if hit is not None:
            per_entry.setdefault(hit, []).append({
                "rule": str(f.get("rule") or "finding"),
                "level": str(f.get("level") or "suggestion"),
                "note": str(f.get("note") or ""),
                "line": line,
            })
        else:
            loose.append(_finding_item(f, inside))
    return per_entry, loose


# ----------------------------------------------------------------------- GET


@router.get("/api/campaign/{name}/review")
def get_review(name: str):
    d = require_campaign(name)
    started = time.monotonic()
    deadline = started + FINDINGS_BUDGET_S
    items: list = []
    files: list = []
    notes: list = []

    for sub, pattern in PENDING_PATTERNS:
        base = d / sub
        if not base.is_dir():
            continue
        for path in sorted(base.glob(pattern)):
            inside = f"{sub}/{path.name}"
            kind = "lore-entry" if sub == "lore" else "timeline-approval"
            try:
                text = path.read_text(encoding="utf-8")
                doc = yaml.safe_load(text)
            except (yaml.YAMLError, OSError) as e:
                items.append({
                    "id": f"unparseable:{inside}", "kind": "finding",
                    "source": "review", "title": f"{inside} does not parse",
                    "body": str(e), "findings": [], "file": inside,
                })
                continue
            if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
                continue
            entries = doc["entries"]
            if not entries:
                continue

            spans = _entry_spans(text)
            findings = _structural_findings(path, inside, deadline, notes)
            per_entry, loose = _split_findings(findings or [], inside, spans)
            items.extend(loose)

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                eid = str(entry.get("id"))
                is_page = isinstance(entry.get("page"), dict)
                items.append({
                    "id": eid,
                    "kind": "lore-page" if is_page else kind,
                    "source": str(entry.get("source") or "unknown"),
                    "title": str(entry.get("title") or "(untitled)"),
                    "body": str(entry.get("text") or ""),
                    "when": entry.get("when"),
                    "findings": per_entry.get(eid, []),
                    "file": inside,
                })

            files.append({
                "file": inside,
                "kind": kind,
                "created": doc.get("created"),
                "focus": doc.get("focus"),
                "week": doc.get("week"),
                "entries": len(entries),
                "findings": sum(len(v) for v in per_entry.values()) + len(loose),
                # send this back with approve/delete: the request is refused
                # (409, nothing written) when the file changed since this view
                "revision": publication.file_revision(path),
                # pending-week files predate proposal-based weeks: they
                # describe weeks the OLD implementation already committed
                "legacy": sub == "simulation",
            })

    conflicts = publication.unresolved_conflicts(name)
    if conflicts:
        affected = sorted({f for c in conflicts for f in c["conflicts"]})
        notes.append(
            "recovery conflict: a previous approval crashed and %s was "
            "edited externally — resolve it before approving more "
            "(affected: %s)" % (
                ", ".join(c["transaction"] for c in conflicts),
                ", ".join(affected) or "no files listed"))
    if any(f.get("legacy") for f in files):
        notes.append(
            "pending-week files are LEGACY timeline-only reviews from before "
            "proposal-based weeks: the weeks they describe were already "
            "committed by the old implementation — approving only writes "
            "timeline entries (never re-applies effects), and deleting one "
            "does not undo a week")
    out = {"items": items, "files": files}
    if notes:
        out["notes"] = notes

    # Vault findings (dangling wiki-links, name/alias collisions) join the
    # queue as synthetic items — advisory, and capped so a hand-massacred
    # vault cannot bury the pending entries under hundreds of rows.
    try:
        vault = links.findings(name)
    except Exception as e:  # noqa: BLE001 — advisory, never blocking
        notes.append(f"vault findings skipped: {e}")
        vault = []
    for f in vault[:VAULT_FINDINGS_CAP]:
        item = _finding_item(f, "vault")
        item["source"] = "links"
        items.append(item)
    if len(vault) > VAULT_FINDINGS_CAP:
        notes.append(f"vault findings capped: {len(vault) - VAULT_FINDINGS_CAP} "
                     "more not shown — fix the links, or run "
                     "python studio/doctor.py for the full count")
    return out


# ------------------------------------------------------------------ mutate


def _timeline_path(campaign_dir: Path) -> Path:
    return campaign_dir / "lore" / "timeline.yaml"


def _load_timeline(campaign_dir: Path):
    """(doc, entries list) — doc is None when the file is absent; a bare
    list and an {entries: [...]} mapping are both read and preserved."""
    path = _timeline_path(campaign_dir)
    if not path.is_file():
        return None, []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        raise HTTPException(status_code=422,
                            detail=f"lore/timeline.yaml does not parse: {e}")
    if isinstance(doc, list):
        return doc, doc
    if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
        return doc, doc["entries"]
    return doc, []


def _existing_tnums(entries: list) -> set:
    out = set()
    for e in entries:
        if isinstance(e, dict):
            m = T_NUM_RE.match(str(e.get("id") or ""))
            if m:
                out.add(int(m.group(1)))
    return out


def _append_approved(campaign_dir: Path, chosen: list) -> dict:
    """The new lore/timeline.yaml document with `chosen` appended, keeping
    ^t\\d+$ ids and assigning the next free t-number to provisional ones. A
    batch whose title+text is already in the timeline is refused (409) —
    that is the crash-between-writes recovery path: the append landed, the
    pending rewrite did not, and re-approving would silently duplicate.
    Returns the doc; the caller publishes it (no write here)."""
    doc, entries = _load_timeline(campaign_dir)
    existing = _existing_tnums(entries)
    seen = {(str(e.get("title") or ""), str(e.get("text") or ""))
            for e in entries if isinstance(e, dict)}
    for entry in chosen:
        eid = str(entry.get("id") or "")
        if eid and T_ID_RE.match(eid):
            if int(eid[1:]) in existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"id {eid} already exists in lore/timeline.yaml")
            existing.add(int(eid[1:]))  # also guards duplicates within the batch
            continue  # keep the entry's own t-id
        identity = (str(entry.get("title") or ""), str(entry.get("text") or ""))
        if identity != ("", "") and identity in seen:
            raise HTTPException(
                status_code=409,
                detail=f"an entry titled {identity[0]!r} with this exact text "
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
        raise HTTPException(status_code=422,
                            detail="lore/timeline.yaml has an unsupported shape")
    return doc


def _page_rel(entry: dict) -> str:
    """The campaign-relative path an entry's page draft would create."""
    filename = spine.entity_page_filename(str(entry.get("title") or ""))
    return f"lore/entities/{filename}"


def _page_write(name: str, entry: dict) -> tuple[str, str]:
    """(rel path, exact page text) for one approved page draft. The caller
    pins the path's absence in the publication's base revisions, so a page
    created between the plan and the publish aborts the whole approval
    (nothing written) instead of clobbering a hand page."""
    page = entry.get("page") or {}
    text = spine.entity_page_text(
        str(entry.get("title") or ""),
        {"kind": str(page.get("kind") or ""),
         "name": str(entry.get("title") or ""),
         "aliases": [str(a) for a in (page.get("aliases") or [])]},
        str(entry.get("text") or ""))
    return _page_rel(entry), text


def _stub_writes(name: str, entry: dict) -> list[tuple[str, str]]:
    """Vault stage 4: approved entries stub their validated new_names as
    pages (kind 'unknown') — the graph grows with the story instead of
    lagging it. Same absence-pinning contract as page drafts: an existing
    page is never clobbered (here: skipped, the hand page wins)."""
    title = str(entry.get("title") or "")
    when = str(entry.get("when") or "")
    out = []
    for new in entry.get("new_names") or []:
        new = str(new).strip()
        if not new:
            continue
        text = spine.entity_page_text(
            new, {"kind": "unknown", "name": new},
            f"(Stub page — first named in '{title}' ({when}). Add lore "
            "here, or in Obsidian.)")
        out.append((f"lore/entities/{spine.entity_page_filename(new)}", text))
    return out


def _idempotency_key(name: str, body: ReviewBody, op: str,
                     content_fallback: str) -> str:
    """A stable key for this exact approval request: repeating it (double
    click, retry, restart) returns the recorded result instead of applying
    twice. The viewed revision is part of the key — approving freshly
    viewed content after the file changed is a NEW operation, not a
    repeat; legacy clients (no revision) fall back to the current file
    bytes so a blind repeat with changed content is not swallowed."""
    payload = json.dumps(
        [name, op, body.file, body.ids, bool(body.all),
         body.expected_revision or content_fallback],
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prior_result(name: str, body: ReviewBody, op: str,
                  content_fallback: str):
    """The recorded result for this exact request, when one exists. With a
    viewed revision the receipt is reachable even after the pending file is
    gone (a full approval deletes it) — that retry must answer, not 404."""
    if body.expected_revision:
        prior = publication.receipt(
            name, _idempotency_key(name, body, op, content_fallback))
        if prior:
            return {**prior, "idempotent": True}
    return None


def _receipt_key(name: str, body: ReviewBody, op: str,
                 content_fallback: str) -> str:
    return _idempotency_key(name, body, op, content_fallback)


def _revision_conflict_409(e: publication.RevisionConflict) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"stale review: {e} — refresh the review queue, re-read the "
               f"draft, and submit again. Nothing was written.")


@router.post("/api/campaign/{name}/review/approve")
def post_approve(name: str, body: ReviewBody):
    d = require_campaign(name)
    with spine.campaign_lock(name):  # blocking: an in-flight job finishes first
        ensure_publishable_409(name)
        prior = _prior_result(name, body, "approve", "")
        if prior:
            return prior
        path, inside = _pending_file(d, body.file)
        try:
            key = _receipt_key(name, body, "approve",
                               publication.file_revision(path))
            prior = publication.receipt(name, key)
            if prior:
                return {**prior, "idempotent": True}
            doc = _load_pending(path, inside)
            chosen_idx = _select(doc, body)
            chosen = [dict(doc["entries"][k]) for k in chosen_idx]
            pages = [e for e in chosen if isinstance(e.get("page"), dict)]
            entries = [e for e in chosen if not isinstance(e.get("page"), dict)]

            writes: list[dict] = []
            base: dict = {}
            if body.expected_revision:
                base[inside] = body.expected_revision
            # a generated draft pins what its generator read (source files'
            # byte revisions + directory fingerprints); a stale source
            # refuses the whole approval so the DM regenerates instead of
            # approving prose derived from superseded canon
            for rel, rev in (doc.get("base_revisions") or {}).items():
                base[str(rel)] = str(rev)
            for rel, fp in (doc.get("input_fingerprints") or {}).items():
                current = publication.fingerprint_dir(d / rel)
                if current != fp:
                    raise publication.RevisionConflict(
                        f"{rel} membership changed since this draft was "
                        f"generated (a page was created or deleted)")

            written, skipped, stubbed = 0, 0, 0
            for e in pages:
                try:
                    rel = _page_rel(e)
                except spine.CampaignError:
                    skipped += 1  # a title no filename can be made from:
                    continue      # the draft is discarded, the hand wins
                if (d / rel).exists():
                    skipped += 1  # the hand page wins, the draft is discarded
                    continue
                rel_written, text = _page_write(name, e)
                writes.append({"path": rel_written, "text": text})
                base[rel_written] = publication.ABSENT  # create-only, race-safe
                written += 1
            new_timeline = None
            if entries:
                new_timeline = _append_approved(d, entries)
                writes.append({
                    "path": "lore/timeline.yaml",
                    "text": yaml.safe_dump(new_timeline, sort_keys=False,
                                           allow_unicode=True, width=100)})
                for e in entries:
                    try:
                        stubs = _stub_writes(name, e)
                    except spine.CampaignError:
                        stubs = []
                    for rel, text in stubs:
                        if (d / rel).exists() \
                                or any(w["path"] == rel for w in writes):
                            continue  # a hand page or this batch's own
                                      # page draft wins — never double-write
                        writes.append({"path": rel, "text": text})
                        base[rel] = publication.ABSENT
                        stubbed += 1
            keep = [e for k, e in enumerate(doc["entries"])
                    if k not in set(chosen_idx)]
            if keep:
                out_doc = {k: v for k, v in doc.items() if k != "entries"}
                out_doc["entries"] = keep
                writes.append({
                    "path": inside,
                    "text": yaml.safe_dump(out_doc, sort_keys=False,
                                           allow_unicode=True, width=100)})
            else:
                writes.append({"path": inside, "delete": True})

            pub = publication.publish(
                name, writes,
                operation=f"approve {len(chosen)} pending entries",
                base_revisions=base, idempotency_key=key)
        except publication.RevisionConflict as e:
            raise _revision_conflict_409(e)
        except publication.PublicationConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except HTTPException:
            raise  # deliberate 4xx from _select/_append_approved etc.
        except Exception as e:  # noqa: BLE001 — mid-flight publication failure
            raise HTTPException(
                status_code=500,
                detail=f"the approval could not be published ({e}) — the "
                       f"staged transaction is kept and will be recovered "
                       f"automatically when the studio restarts")

        result = {"approved": len(chosen), "entries": len(entries),
                  "pages": written, "pages_skipped": skipped, "stubs": stubbed,
                  "transaction": pub["transaction"]}
        if pub["state"] == "noop":
            result["noop"] = True
        publication.record_receipt(name, key, result)
        msg = f"campaign/{name}: approve {len(chosen)} pending entries"
        if written or skipped:
            msg += f" (+{written} page(s)" + \
                (f", {skipped} skipped" if skipped else "") + ")"
        elif stubbed:
            msg += f" (+{stubbed} stub page(s))"
        snap = canon.snapshot_result(name, msg)
        publication.after_snapshot(name, pub["transaction"], snap)
        result["snapshot"] = snap
        # "approved" counts everything chosen (entries + page drafts) so the
        # pre-vault contract ("approved: N") still reads true
        return result


@router.post("/api/campaign/{name}/review/delete")
def post_delete(name: str, body: ReviewBody):
    d = require_campaign(name)
    with spine.campaign_lock(name):
        ensure_publishable_409(name)
        prior = _prior_result(name, body, "delete", "")
        if prior:
            return prior
        path, inside = _pending_file(d, body.file)
        try:
            key = _receipt_key(name, body, "delete",
                               publication.file_revision(path))
            prior = publication.receipt(name, key)
            if prior:
                return {**prior, "idempotent": True}
            doc = _load_pending(path, inside)
            chosen_idx = _select(doc, body)
            keep = [e for k, e in enumerate(doc["entries"])
                    if k not in set(chosen_idx)]
            base: dict = {}
            if body.expected_revision:
                base[inside] = body.expected_revision
            if keep:
                out_doc = {k: v for k, v in doc.items() if k != "entries"}
                out_doc["entries"] = keep
                writes = [{"path": inside, "text": yaml.safe_dump(
                    out_doc, sort_keys=False, allow_unicode=True, width=100)}]
            else:
                writes = [{"path": inside, "delete": True}]
            pub = publication.publish(
                name, writes,
                operation=f"delete {len(chosen_idx)} pending entries",
                base_revisions=base, idempotency_key=key)
        except publication.RevisionConflict as e:
            raise _revision_conflict_409(e)
        except publication.PublicationConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — mid-flight publication failure
            raise HTTPException(
                status_code=500,
                detail=f"the delete could not be published ({e}) — the "
                       f"staged transaction is kept and will be recovered "
                       f"automatically when the studio restarts")
        result = {"deleted": len(chosen_idx),
                  "transaction": pub["transaction"]}
        if pub["state"] == "noop":
            result["noop"] = True
        publication.record_receipt(name, key, result)
        snap = canon.snapshot_result(
            name, f"campaign/{name}: delete {len(chosen_idx)} pending entries")
        publication.after_snapshot(name, pub["transaction"], snap)
        result["snapshot"] = snap
        return result
