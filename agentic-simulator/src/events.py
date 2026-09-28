"""Event records: one shape, two layers.

A resolved proposal becomes an event with a human-readable `text` line AND
structured fields — `action`, `outcome`, `vs`, `contested`, `margin`. Prose
readers read the text; code reads the fields and never re-parses the prose.
(Weeks recorded before structured fields existed carry only `text`; every
helper here falls back to parsing it, so old state.yaml files keep working.
The regexes are the legacy path, not the interface.)
"""

import re

_CONTEST = re.compile(r"\(contested vs (\w+)\)")
_MARGIN = re.compile(r"margin ([+-]\d+)")
_OUTCOME = re.compile(r"\s+—\s+(?:succeeded|failed)\b.*$", re.S)


def action_text(ev):
    """The proposal's action sentence, outcome suffix stripped."""
    if "action" in ev:
        return ev["action"]
    return _OUTCOME.sub("", ev.get("text", ""))


def claim_text(ev):
    """What a rumor may quote: the action, never the resolution — a failed
    covert action does not travel with its margin attached. Project events
    carry `public_action` when the work must not announce itself: a
    restricted project's event is born with a project-free line, and the
    rumor quotes that instead of the action text."""
    if ev.get("public_action"):
        return str(ev["public_action"])
    return ev["action"] if "action" in ev else ev.get("text", "")


def vs_of(ev):
    """The faction id this event was contested against, or None."""
    if "vs" in ev:
        return ev["vs"]
    m = _CONTEST.search(ev.get("text", ""))
    return m.group(1) if m else None


def margin_of(ev):
    """The recorded margin, or None when the event carries none."""
    if "margin" in ev:
        return ev["margin"]
    m = _MARGIN.search(ev.get("text", ""))
    return int(m.group(1)) if m else None


def contested(ev):
    if "contested" in ev:
        return bool(ev["contested"])
    return _CONTEST.search(ev.get("text", "")) is not None


def succeeded(ev):
    """True/False when the outcome is recorded or parsable; None when the
    event asserts no outcome at all (hand-ingested session events)."""
    if "outcome" in ev:
        return ev["outcome"] == "succeeded"
    text = ev.get("text", "")
    if "— failed" in text:
        return False
    if "— succeeded" in text:
        return True
    return None
