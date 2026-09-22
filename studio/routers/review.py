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

All mutations run under the SHARED per-campaign lock (spine.campaign_lock,
blocking — an in-flight week/generation job finishes first), and each
success commits a canon snapshot ("campaign/<name>: approve/delete N
pending entries").
"""

import json
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import canon
import engines
import spine
from routers.common import require_campaign

router = APIRouter()

PENDING_PATTERNS = (("lore", "pending-*.yaml"),
                    ("simulation", "pending-week-*.yaml"))
T_ID_RE = re.compile(r"^t\d+$")
T_NUM_RE = re.compile(r"^t0*(\d+)$")
_ENTRY_START_RE = re.compile(r"^- id:\s*(\S+)")

CHECK_TIMEOUT_S = 60     # per check.py run
FINDINGS_BUDGET_S = 1.5  # soft budget for all check.py runs in one GET


class ReviewBody(BaseModel):
    file: str
    ids: list[str] | None = None
    all: bool | None = None


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
    """check.py on one pending file — once per file per request, subprocess
    list-form args (the parent path contains `&`), cwd=verification. Skipped
    with a note when the request blows its budget; never blocks approval."""
    if deadline is not None and time.monotonic() > deadline:
        notes.append(f"findings skipped for {inside}: the queue took too long "
                     "to assemble — refresh to retry")
        return None
    out_dir = Path(tempfile.mkdtemp(prefix="studio-review-"))
    try:
        rc, _lines = engines.run_py(
            engines.VERIFICATION, "check.py", str(path),
            "--out", str(out_dir), timeout=CHECK_TIMEOUT_S)
        findings_file = out_dir / "findings.json"
        if findings_file.is_file():
            return json.loads(findings_file.read_text(encoding="utf-8"))
        notes.append(f"check.py produced no findings.json for {inside} (rc={rc})")
    except Exception as e:  # noqa: BLE001 — advisory, never blocking
        notes.append(f"structural check failed for {inside}: {e}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
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
                items.append({
                    "id": eid,
                    "kind": kind,
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
            })

    out = {"items": items, "files": files}
    if notes:
        out["notes"] = notes
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
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
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


def _append_approved(campaign_dir: Path, chosen: list) -> None:
    """Append entries into lore/timeline.yaml, keeping ^t\\d+$ ids and
    assigning the next free t-number to provisional ones."""
    doc, entries = _load_timeline(campaign_dir)
    existing = _existing_tnums(entries)
    for entry in chosen:
        eid = str(entry.get("id") or "")
        if eid and T_ID_RE.match(eid):
            if int(eid[1:]) in existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"id {eid} already exists in lore/timeline.yaml")
            existing.add(int(eid[1:]))  # also guards duplicates within the batch
            continue  # keep the entry's own t-id
        next_num = (max(existing) + 1) if existing else 1
        entry["id"] = f"t{next_num:04d}"
        existing.add(next_num)

    if doc is None:
        doc = {"entries": list(entries)}
    if isinstance(doc, list):
        doc.extend(chosen)
    elif isinstance(doc, dict):
        doc.setdefault("entries", []).extend(chosen)
    else:
        raise HTTPException(status_code=422,
                            detail="lore/timeline.yaml has an unsupported shape")
    _timeline_path(campaign_dir).parent.mkdir(parents=True, exist_ok=True)
    _timeline_path(campaign_dir).write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8")


def _rewrite_pending(path: Path, doc: dict, keep: list) -> None:
    if keep:
        out = {k: v for k, v in doc.items() if k != "entries"}
        out["entries"] = keep
        path.write_text(
            yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8")
    else:
        path.unlink()


@router.post("/api/campaign/{name}/review/approve")
def post_approve(name: str, body: ReviewBody):
    d = require_campaign(name)
    with spine.campaign_lock(name):  # blocking: an in-flight job finishes first
        path, inside = _pending_file(d, body.file)
        doc = _load_pending(path, inside)
        chosen_idx = _select(doc, body)
        chosen = [dict(doc["entries"][k]) for k in chosen_idx]
        _append_approved(d, chosen)
        keep = [e for k, e in enumerate(doc["entries"]) if k not in set(chosen_idx)]
        _rewrite_pending(path, doc, keep)
        canon.snapshot(name,
                       f"campaign/{name}: approve {len(chosen)} pending entries")
        return {"approved": len(chosen)}


@router.post("/api/campaign/{name}/review/delete")
def post_delete(name: str, body: ReviewBody):
    d = require_campaign(name)
    with spine.campaign_lock(name):
        path, inside = _pending_file(d, body.file)
        doc = _load_pending(path, inside)
        chosen_idx = _select(doc, body)
        keep = [e for k, e in enumerate(doc["entries"]) if k not in set(chosen_idx)]
        _rewrite_pending(path, doc, keep)
        canon.snapshot(name,
                       f"campaign/{name}: delete {len(chosen_idx)} pending entries")
        return {"deleted": len(chosen_idx)}
