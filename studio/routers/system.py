"""System: health, doctor (M5 — wired to studio/doctor.py), jobs poll, and
the campaign text search (H).

Search scans the campaign's human-facing text files line by line,
case-insensitively — one route, no index, no database: a campaign is a
folder of files, so grep-shaped behavior is the honest shape.
"""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

import doctor
import engines
import jobs
from routers.common import require_campaign

router = APIRouter()

# search tuning: the response cap and the per-line display trim
SEARCH_CAP = 50
SEARCH_LINE_MAX = 200
# a "heading-ish" previous line worth showing as context: markdown headings,
# YAML keys introducing a block ("rumors:"), and list-entry ids ("- id: t0003")
_SEARCH_CONTEXT_RE = re.compile(r"^(#{1,6}\s\s*\S.*|.*:)\s*$|^\s*-\s*id\s*:")


@router.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0",
            "week_run_in_progress": engines.week_flag_path().is_file()}


@router.get("/api/doctor")
def get_doctor(quick: bool = False):
    # doctor.py is stdlib-only and runs its probes (engine CLIs + one tiny
    # live LLM probe) per request; tests monkeypatch doctor.report.
    # quick=1 skips the live LLM probe (presence-only key check) so a page
    # load can poll it without spending tokens or waiting on the API.
    if quick:
        return doctor.report(live_llm=False)
    return doctor.report()


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


# ------------------------------------------------------------------- search


def _search_files(root: Path) -> list:
    """(absolute path, campaign-relative posix path, kind) in scan order —
    the order decides what survives the cap: lore, style, chronicles,
    pending, geography, campaign.yaml."""
    out: list = []

    def add(rel: str, kind: str) -> None:
        p = root / rel
        if p.is_file():
            out.append((p, rel, kind))

    lore = root / "lore"
    if lore.is_dir():
        for p in sorted(lore.glob("*.yaml")):
            kind = "pending" if p.name.startswith("pending-") else \
                ("timeline" if p.name == "timeline.yaml" else "entities")
            add(f"lore/{p.name}", kind)
        style = lore / "style"
        if style.is_dir():
            for p in sorted(style.iterdir()):
                if p.is_file():
                    add(f"lore/style/{p.name}", "style")
    chron = root / "simulation" / "chronicle"
    if chron.is_dir():
        for p in sorted(chron.glob("*.md")):
            add(f"simulation/chronicle/{p.name}", "chronicle")
    sim = root / "simulation"
    if sim.is_dir():
        for p in sorted(sim.glob("pending-*.yaml")):
            add(f"simulation/{p.name}", "pending")
    add("world/geography.yaml", "geography")
    add("campaign.yaml", "campaign")
    return out


def _trim(text: str) -> str:
    text = text.strip()
    if len(text) > SEARCH_LINE_MAX:
        text = text[: SEARCH_LINE_MAX - 1].rstrip() + "…"
    return text


def _context_of(prev_line: str) -> str:
    """The previous non-empty line, but only when it looks like a
    title/heading (otherwise there is nothing useful to show)."""
    s = prev_line.strip()
    if s and _SEARCH_CONTEXT_RE.match(s):
        return _trim(s)
    return ""


@router.get("/api/campaign/{name}/search")
def search_campaign(name: str, q: str):
    root = require_campaign(name)
    echo = (q or "").strip()
    needle = echo.lower()
    if not needle:
        raise HTTPException(status_code=422,
                            detail="q: a non-empty search string is required")
    results = []
    for path, rel, kind in _search_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue  # an unreadable file is skipped, never a failed search
        prev = ""
        for lineno, line in enumerate(lines, 1):
            if needle in line.lower():
                results.append({
                    "file": rel,
                    "kind": kind,
                    "line": lineno,
                    "text": _trim(line),
                    "context": _context_of(prev),
                })
                if len(results) >= SEARCH_CAP:
                    return {"q": echo, "results": results}
            if line.strip():
                prev = line
    return {"q": echo, "results": results}
