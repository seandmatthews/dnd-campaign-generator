"""Pre-resolution consistency checks over faction proposals.

One-sentence mechanic: a proposal may only assert facts its faction has a
knowledge path to. Warnings are advisory — printed for the DM; nothing is
auto-rejected. Fully deterministic: no LLM calls, no network, no randomness.
"""

import re

# --- shared text normalization ----------------------------------------------

_NUMWORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight",
             "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
             "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
             "twenty"]

# Function words and machinery words ("week", number words) never count as
# distinctive content in any overlap test.
_STOP = {
    "the", "a", "an", "and", "or", "but", "nor", "of", "to", "in", "on", "at",
    "by", "for", "with", "from", "into", "over", "under", "through", "toward",
    "until", "while", "when", "as", "so", "if", "then", "than", "there",
    "here", "not", "no", "any", "all", "each", "every", "some", "none",
    "another", "own", "also", "now", "about", "after", "before", "during",
    "since", "because", "who", "whom", "which", "what", "how", "is", "are",
    "was", "were", "be", "been", "being", "am", "do", "does", "did", "done",
    "has", "have", "had", "will", "would", "can", "could", "shall", "should",
    "may", "might", "must", "it", "its", "this", "that", "these", "those",
    "their", "them", "they", "he", "she", "his", "her", "we", "our", "you",
    "your", "i", "my", "me", "week", "last",
} | set(_NUMWORDS)


def _fold(word):
    """Lowercase one word and fold naive plurals (tanneries→tannery)."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _words(text):
    """All folded word tokens of the text."""
    return [_fold(w) for w in re.findall(r"[A-Za-z]+", text)]


def _content(text):
    """Distinctive content tokens: folded words of length>3, not stopwords."""
    return {t for t in _words(text) if len(t) > 3 and t not in _STOP}


def _overlap(tokens, text):
    """The one overlap bar, used by every check: at least 2 distinctive
    content tokens shared between a token set and a text."""
    return len(tokens & _content(text)) >= 2


def _sentences(text):
    return re.split(r"(?<=[.!?])\s+", text)


# --- week references ---------------------------------------------------------

_WEEK_NUM = re.compile(
    r"\bweek[\s-]*(\d{1,2}|" + "|".join(sorted(_NUMWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_WEEK_LAST = re.compile(r"\blast\s+week\b", re.IGNORECASE)


def _week_references(sentence, clock_week):
    """Week numbers the sentence asserts facts about; 'last week' is the
    just-completed week (meta.clock.week)."""
    refs = []
    for m in _WEEK_NUM.finditer(sentence):
        g = m.group(1).lower()
        refs.append(int(g) if g.isdigit() else _NUMWORDS.index(g) + 1)
    if _WEEK_LAST.search(sentence):
        refs.append(clock_week)
    return refs


# --- kind: unknown_actor -----------------------------------------------------

# Generic city-book words that are never faction names. Per-campaign
# override lives in state.yaml under `generic_words: [...]`; this constant
# is the fallback for states that predate that key.
_DEFAULT_GENERIC = {"council", "harbor", "manor", "doge", "estate", "watcher",
                    "watch", "city", "crowd", "priest", "strikebreaker", "week"}

_JOINERS = {"of", "the", "de"}


def _known_name_tokens(state):
    """Tokens that name something real or generic: every faction's name and
    id, the city, its districts, and generic city-book words (state.yaml's
    `generic_words` or the built-in fallback)."""
    toks = set(state.get("generic_words") or _DEFAULT_GENERIC)
    city = state.get("city", {})
    toks |= _content(city.get("name", ""))
    for d in city.get("districts", []):
        toks |= _content(d)
    for f in state.get("factions", []):
        toks |= _content(f.get("name", ""))
        for part in str(f.get("id", "")).split("_"):
            toks |= _content(part)
    return toks


def _capitalized_phrases(text):
    """Maximal Title-case runs (joined by 'of'/'the'/'de'); a lone capitalized
    first word of a sentence is not treated as a name."""
    out = []
    for sent in _sentences(text):
        words = re.findall(r"[A-Za-z][A-Za-z']*", sent)
        i, n = 0, len(words)
        while i < n:
            head = words[i][0].isupper() and (
                i > 0 or (i + 1 < n and words[i + 1][0].isupper())
            )
            if not head:
                i += 1
                continue
            j = i + 1
            while j < n and (
                words[j][0].isupper()
                or (words[j].lower() in _JOINERS
                    and j + 1 < n and words[j + 1][0].isupper())
            ):
                j += 1
            out.append(" ".join(words[i:j]))
            i = j
    return out


def _unknown_actors(proposal, state):
    """Name-like phrases in the action text must match a real faction's
    distinctive name/id tokens, the city book, or a landmark word."""
    known = _known_name_tokens(state)
    warns = []
    for phrase in _capitalized_phrases(proposal.get("action", "")):
        toks = _content(phrase)
        if toks and not (toks & known):
            warns.append({
                "kind": "unknown_actor",
                "detail": f"mentions '{phrase}', which matches no faction in state",
            })
    return warns


# --- kind: unwitnessed_event -------------------------------------------------

_OUTCOME_VERBS = {"succeeded", "failed", "went", "won", "lost", "given",
                  "awarded", "attached", "collapsed", "held", "broke"}


def _unwitnessed_events(proposal, state, faction):
    """A sentence citing a week plus an outcome verb asserts a past event; it
    must overlap some recorded event of that week (the ledger is public
    chronicle) or some knows entry (self-knowledge counts)."""
    clock = state.get("meta", {}).get("clock", {}).get("week", 0)
    by_week = {}
    for e in state.get("events", []):
        by_week.setdefault(e.get("week"), []).append(e.get("text", ""))
    warns = []
    for sent in _sentences(proposal.get("action", "")):
        if not (_OUTCOME_VERBS & set(_words(sent))):
            continue
        clause = _content(sent)
        for week in _week_references(sent, clock):
            witnessed = any(_overlap(clause, t) for t in by_week.get(week, []))
            known = any(_overlap(clause, k) for k in faction.get("knows", []))
            if not (witnessed or known):
                warns.append({
                    "kind": "unwitnessed_event",
                    "detail": f"week {week} claim '{sent.strip()}' matches no "
                              f"recorded event of that week and nothing the "
                              f"faction knows",
                })
    return warns


# --- kind: foreign_secret ----------------------------------------------------


def _foreign_secrets(proposal, state, faction):
    """Repeating the distinctive tokens of ANOTHER faction's secret warns,
    unless a knows entry already overlaps that secret (then it is known, not
    leaked); the faction's own secret never warns."""
    action = _content(proposal.get("action", ""))
    warns = []
    for other in state.get("factions", []):
        if other.get("id") == faction.get("id"):
            continue
        secret = _content(other.get("secret", ""))
        if len(secret) < 2 or len(secret & action) < 2:
            continue
        if any(_overlap(secret, k) for k in faction.get("knows", [])):
            continue
        warns.append({
            "kind": "foreign_secret",
            "detail": f"action text overlaps "
                      f"{other.get('name', other.get('id'))}'s secret",
        })
    return warns


# --- entry point --------------------------------------------------------------


def check(proposal, state, faction_id):
    """Warn (never reject) about facts a proposal asserts without a knowledge
    path: unknown actors, unwitnessed past events, leaked foreign secrets.

    Warnings are advisory — printed for the DM; nothing is auto-rejected.
    Returns [{kind, detail}] sorted by kind then detail; [] means clean.
    """
    factions = {f.get("id"): f for f in state.get("factions", [])}
    if faction_id not in factions:
        raise KeyError(f"unknown faction id: {faction_id}")
    faction = factions[faction_id]
    warns = (
        _unknown_actors(proposal, state)
        + _unwitnessed_events(proposal, state, faction)
        + _foreign_secrets(proposal, state, faction)
    )
    return sorted(warns, key=lambda w: (w["kind"], w["detail"]))
