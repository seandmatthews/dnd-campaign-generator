"""Agent activation scheduler.

One-sentence mechanic: you act if the world touched you last week or you
are at someone's throat; otherwise you hold.

Contract: the scheduler runs BEFORE a new week is resolved, so "last week"
means the events logged at state["meta"]["clock"]["week"] — the most recent
resolved week. Call activation(state) before advancing the clock.
"""

import re

_VS_RE = re.compile(r"\(contested vs (\w+)\)")
_WORD_RE = re.compile(r"[a-z0-9_]+")
_ARTICLES = {"the", "of", "a", "an"}
_IDLE_CAP = 3


def _name_tokens(faction):
    """Distinctive mention tokens: the faction id plus its name words,
    minus articles and single letters."""
    words = set(_WORD_RE.findall(faction["name"].lower()))
    words.add(faction["id"])
    return {w for w in words if len(w) > 1 and w not in _ARTICLES}


def _words(text):
    return set(_WORD_RE.findall(text.lower()))


def _vs_target(event):
    m = _VS_RE.search(event.get("text", ""))
    return m.group(1) if m else None


def _touched(events, fid, tokens):
    """The +2 test: vs target of another faction's contested event, or
    named in any event text."""
    for ev in events:
        if ev.get("actor") != fid and _vs_target(ev) == fid:
            return True
        if _words(ev.get("text", "")) & tokens:
            return True
    return False


def _present(events, fid, tokens):
    """Idle-breaker: actor, vs target, or text-mentioned this week."""
    for ev in events:
        if ev.get("actor") == fid or _vs_target(ev) == fid:
            return True
        if _words(ev.get("text", "")) & tokens:
            return True
    return False


def activation(state):
    """Map every faction id to {score, reasons, act} for the coming week.

    "Last week" is meta.clock.week (see module docstring). Additive
    components, each logged once: +2 targeted last week; +1 hostility
    (outgoing or incoming); +1 pressure (any resource "none"); -1 per
    consecutive week absent from all events, up to 3. Founding week
    (clock week 1): no history — hostility/pressure plus +2 "founding
    week". act at score >= 2.
    """
    week = state["meta"]["clock"]["week"]
    factions = state["factions"]
    by_week = {}
    for ev in state.get("events") or []:
        by_week.setdefault(ev.get("week"), []).append(ev)
    last_week = by_week.get(week, [])
    tokens = {f["id"]: _name_tokens(f) for f in factions}

    out = {}
    for f in factions:
        fid = f["id"]
        score, reasons = 0, []
        if week == 1:
            score += 2
            reasons.append("founding week")
        elif _touched(last_week, fid, tokens[fid]):
            score += 2
            reasons.append("targeted last week")
        if any(v == "hostile" for v in f.get("disposition", {}).values()) or any(
            o.get("disposition", {}).get(fid) == "hostile"
            for o in factions
            if o["id"] != fid
        ):
            score += 1
            reasons.append("hostility")
        if "none" in f.get("resources", {}).values():
            score += 1
            reasons.append("pressure")
        if week != 1:
            quiet, w = 0, week
            while (
                quiet < _IDLE_CAP
                and w >= 1
                and not _present(by_week.get(w, []), fid, tokens[fid])
            ):
                quiet += 1
                w -= 1
            if quiet:
                score -= quiet
                reasons.append(f"quiet for {quiet} week(s)")
        out[fid] = {"score": score, "reasons": reasons, "act": score >= 2}
    return out


def holds(state, activation_result):
    """Ids that sit the week out (act False), in roster order — tick skips
    their LLM calls."""
    roster = [f["id"] for f in state["factions"]]
    return [fid for fid in roster if not activation_result[fid]["act"]]
