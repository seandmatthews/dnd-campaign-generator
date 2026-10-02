"""Rolling job-duration history — the honest wait estimate for 409s.

Not every blocked request can join the queue (a second campaign's week
contending for the shared engine home; a short interactive writer like an
approve), and a bare "retry when it finishes" leaves the GM polling with
no idea whether to wait or go make coffee. This module keeps a SMALL
rolling history of finished job durations per kind in each campaign —
git-ignored, next to the publication transactions dir — and answers one
question in table-plannable units: "typically 8-15 minutes".

Estimates are labeled as estimates, everywhere; the job log remains the
source of truth. Weeks and forecasts scale by roster size (a 5-faction
week costs roughly 5 solo runs); the scaling factor is clamped so one
roster change cannot produce a silly extrapolation.
"""

import datetime
import json
import statistics
import threading

import spine

# same pattern as publication.TX_DIR_NAME: a dot-directory inside the
# campaign with its own .gitignore ("*") so the campaigns repo's history
# stays canon-only
DIR_NAME = ".studio-jobs"
FILE_NAME = "history.json"
WINDOW = 6            # median over the last few finished runs of a kind
LOW_FACTOR = 0.7      # the honest band around the median
HIGH_FACTOR = 1.5
ROSTER_SCALED = frozenset({"sim-week", "forecast"})
SCALE_MIN, SCALE_MAX = 0.5, 2.0

_KIND_LABELS = {
    "sim-week": "a week run",
    "forecast": "a forecast run",
    "world-gen": "a world generation",
    "world-map": "a world map render",
    "map-city": "a city map",
    "map-site": "a site map",
    "lore-entries": "a backstory generation",
    "lore-missing-pages": "a missing-pages generation",
    "goal-review": "a goal review",
    "session-extract": "a session extract",
}

_WRITE_LOCK = threading.Lock()


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, f"a {kind} job")


def _root(name: str):
    d = spine.campaign_dir(name) / DIR_NAME
    gitignore = d / ".gitignore"
    if not gitignore.exists():
        d.mkdir(parents=True, exist_ok=True)
        gitignore.write_text("*\n", encoding="utf-8")
    return d


def _load(name: str) -> dict:
    try:
        doc = json.loads(
            (_root(name) / FILE_NAME).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def roster_of(name: str) -> int | None:
    """The sandbox's faction count, best-effort — None outside campaigns
    without a simulation (the scaling just doesn't apply there)."""
    try:
        state = spine.load_state(name) or {}
        n = len(state.get("factions") or [])
        return n if n else None
    except Exception:  # noqa: BLE001 — roster is decoration, never a gate
        return None


def record(name: str, kind: str, duration_s: float) -> None:
    """Append one finished run's duration (best-effort: history is an
    optimization, and a failure here must never read as a job failure)."""
    try:
        with _WRITE_LOCK:
            doc = _load(name)
            entries = doc.get(kind)
            if not isinstance(entries, list):
                entries = []
            entries.append({
                "s": round(float(duration_s), 1),
                "roster": roster_of(name) if kind in ROSTER_SCALED else None,
                "at": datetime.datetime.now(
                    datetime.timezone.utc).isoformat(timespec="seconds"),
            })
            doc[kind] = entries[-WINDOW:]
            spine.write_text_atomic(_root(name) / FILE_NAME,
                                    json.dumps(doc, indent=2,
                                               ensure_ascii=False))
    except Exception:  # noqa: BLE001 — best-effort by contract
        pass


def _scaled_seconds(entry: dict, kind: str, roster_now: int | None) -> float:
    s = entry.get("s")
    if not isinstance(s, (int, float)) or s <= 0:
        return 0.0
    if kind not in ROSTER_SCALED:
        return float(s)
    roster_then = entry.get("roster")
    if not roster_now or not isinstance(roster_then, int) or roster_then <= 0:
        return float(s)
    factor = max(SCALE_MIN, min(SCALE_MAX, roster_now / roster_then))
    return float(s) * factor


def estimate(name: str, kind: str) -> dict | None:
    """The estimate for a kind from this campaign's history, or None when
    nothing has finished yet (an honest "no history" beats a made-up
    number). Shape: {median_s, low_s, high_s, samples, roster}."""
    entries = _load(name).get(kind)
    if not isinstance(entries, list) or not entries:
        return None
    roster_now = roster_of(name) if kind in ROSTER_SCALED else None
    durations = [d for d in (_scaled_seconds(e, kind, roster_now)
                             for e in entries
                             if isinstance(e, dict)) if d > 0]
    if not durations:
        return None
    med = statistics.median(durations)
    return {
        "median_s": round(med, 1),
        "low_s": round(med * LOW_FACTOR, 1),
        "high_s": round(med * HIGH_FACTOR, 1),
        "samples": len(durations),
        "roster": roster_now,
        "estimated": True,
    }


def _human(seconds: float) -> str:
    """One human unit, rounded the way a table plans: seconds under two
    minutes, whole minutes above."""
    if seconds < 120:
        return f"{max(1, round(seconds))} seconds"
    return f"{round(seconds / 60)} minutes"


def band_text(est: dict) -> str:
    """"typically 8-15 minutes" — the band, in one phrase."""
    low, high = est.get("low_s"), est.get("high_s")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return ""
    low_s, high_s = _human(low), _human(high)
    if low_s == high_s:
        return f"typically about {low_s}"
    return f"typically {low_s}-{high_s}"


def blocked_line(kind: str, est: dict | None) -> str:
    """The one-line "what is running and how long it takes" a 409 carries:
    what holds the campaign, how long it usually takes, and the honest
    label. With no history yet, say that instead of inventing a number."""
    roster = f" ({est['roster']}-faction roster)" \
        if est and est.get("roster") else ""
    if est:
        return (f"Blocked by: {kind_label(kind)}{roster} — "
                f"{band_text(est)} (an estimate from past runs; the job "
                "log is the source of truth)")
    return (f"Blocked by: {kind_label(kind)} — no duration history yet "
            "(the first runs set the estimate; watch the job log)")


def blocked_by_line(running: list) -> str:
    """"Blocked by: <what is running> — <how long it usually takes>", from
    the running job's kind and ITS campaign's rolling history. Lives here
    (a service) rather than routers/common so every busy-409 author —
    routers/common.busy_409, the engine-home 409s, and
    proposal_flows._lock — shares ONE implementation (ticket 13; the
    proposal accept/discard 409s used to carry only the static guess).
    The first running job is the story; the rest are noise in a one-lock
    world. Empty when nothing in the registry matches (the holder is an
    external CLI — the honest message is then the plain busy one, not a
    guess)."""
    if not running:
        return ""
    first = running[0]
    est = None
    try:
        est = estimate(first.get("campaign") or "", first["kind"])
    except Exception:  # noqa: BLE001 — the estimate is decoration
        est = None
    return " " + blocked_line(first["kind"], est)
