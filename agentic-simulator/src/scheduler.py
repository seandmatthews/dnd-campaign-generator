"""Agent activation scheduler.

One-sentence mechanic: you act if the world touched you last week or you
are at someone's throat; otherwise you hold.

Contract: the scheduler runs BEFORE a new week is resolved, so "last week"
means the events logged at state["meta"]["clock"]["week"] — the most recent
resolved week. Call activation(state) before advancing the clock.
"""

import re

import events
import projects
from store import place_words

_WORD_RE = re.compile(r"[a-z0-9_]+")
_ARTICLES = {"the", "of", "a", "an"}
_IDLE_CAP = 3


def _name_tokens(faction, place_words_set=frozenset()):
    """Distinctive mention tokens: the faction id plus its name words,
    minus articles, single letters, and words that name any place or one
    of its districts — a word that names both a place and a faction (the
    Reliquary quarter vs the Reliquary) must not activate the faction on a
    mere mention of the place."""
    words = set(_WORD_RE.findall(faction["name"].lower()))
    words.add(faction["id"])
    return {w for w in words if len(w) > 1 and w not in _ARTICLES} - place_words_set


def _city_words(state):
    """Words that name any place or any of its districts (store.place_words,
    article-stripped) — multi-place campaigns subtract the whole set."""
    return place_words(state) - _ARTICLES


def _words(text):
    return set(_WORD_RE.findall(text.lower()))


def _vs_target(event):
    return events.vs_of(event)


def _in_week(evs, fid, tokens, include_actor):
    """The shared walk: vs target (of another faction's event unless
    include_actor), text-mention, and — when include_actor — being the
    actor. _touched and _present are this predicate with the actor term
    on/off."""
    for ev in evs:
        if _vs_target(ev) == fid and (include_actor or ev.get("actor") != fid):
            return True
        if include_actor and ev.get("actor") == fid:
            return True
        if _words(ev.get("text", "")) & tokens:
            return True
    return False


def _touched(events_list, fid, tokens):
    """The +2 test: vs target of another faction's contested event, or
    named in any event text."""
    return _in_week(events_list, fid, tokens, include_actor=False)


def _present(events_list, fid, tokens):
    """Idle-breaker: actor, vs target, or text-mentioned this week."""
    return _in_week(events_list, fid, tokens, include_actor=True)


def activation(state):
    """Map every faction id to {score, reasons, act} for the coming week.

    "Last week" is meta.clock.week (see module docstring). Additive
    components, each logged once: +2 targeted last week; +1 hostility
    (outgoing or incoming); +1 pressure (any resource "none"); -1 per
    consecutive week absent from all events, up to 3. Founding week
    (clock week 0 — the studio's fresh-sandbox start: no history at all)
    gets hostility/pressure plus +2 "founding week"; from clock week 1 on
    the normal targeted/idle mechanics take over. act at score >= 2.

    Project override: a faction whose eligible active project (requirements
    met, counter below target) is mid-flight acts even when the score would
    hold it — an undertaking does not idle because the week is quiet. The
    score and its reasons still stand beside it in the record.
    """
    week = state["meta"]["clock"]["week"]
    factions = state["factions"]
    project_act = projects.activation_overrides(state)
    by_week = {}
    for ev in state.get("events") or []:
        by_week.setdefault(ev.get("week"), []).append(ev)
    last_week = by_week.get(week, [])
    city_words = _city_words(state)
    tokens = {f["id"]: _name_tokens(f, city_words) for f in factions}

    out = {}
    for f in factions:
        fid = f["id"]
        score, reasons = 0, []
        if week == 0:
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
        if week > 0:
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
        act = score >= 2
        if fid in project_act:
            act = True
            reasons.append("active project")
        out[fid] = {"score": score, "reasons": reasons, "act": act}
    return out


def holds(state, activation_result):
    """Ids that sit the week out (act False), in roster order — tick skips
    their LLM calls."""
    roster = [f["id"] for f in state["factions"]]
    return [fid for fid in roster if not activation_result[fid]["act"]]
