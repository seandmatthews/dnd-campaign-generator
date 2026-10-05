"""The stub LLM provider for the first-DM E2E (ticket 36).

A tiny OpenAI-compatible /chat/completions responder — stdlib only, no
dependencies — that sniffs each prompt and answers with the canned body
that flow expects:

  - simdesign   (studio/simdesign.py)     -> the sandbox-design JSON
  - loregen     (studio/loregen.py)       -> 3 pending-entry YAML blocks
  - canon-audit (studio/loregen.py)       -> [] (no findings)
  - week run    (agentic-simulator llm.py)-> one valid proposal YAML per
                                             faction, then chronicle prose
                                             ending "TITLE: ..."
  - extraction  (studio/sessions.py)      -> two suggestions YAML

Everything else gets a plain "ok." — the call meter counts it, nothing
parses it. The point is determinism and speed: the whole first-DM path
runs on this in seconds, costs nothing, and never flakes on a real
provider. The studio and its engine subprocesses both reach it through
LLM_BASE_URL/LLM_MODEL/LLM_API_KEY in their environment (engines._run
passes the parent env down).

Usage: python llm_stub.py [--port 8399]
"""

import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The week chronicle prompt (narrate_week, the LAST LLM call of a week
# run) sleeps so the spec has a wide "job is running" window for its
# 409 / queued-cancel probes. Gate on the chronicle's TITLE-line
# instruction — "You are the chronicler of …" alone would also match
# loregen's backstory and missing-pages prompts. Override for quicker
# local loops.
CHRONICLE_DELAY_S = float(os.environ.get("E2E_LLM_DELAY_S", "12"))

# ---------------------------------------------------------------- bodies

DESIGN_JSON = {
    "premise": "The toll bridge burned; wardens and ferrymen blame each "
               "other while the crown's charter weighs both claims.",
    "factions": [
        {"name": "The Bridge Wardens", "kind": "toll brotherhood",
         "goal": "Rebuild the bridge and keep collecting the toll",
         "resources": {"coin": "medium", "muscle": "low",
                       "legitimacy": "high"},
         "place": "Harnost"},
        {"name": "The Ferrymen", "kind": "boatmen guild",
         "goal": "Keep the crossing broken — every passenger is a fare",
         "resources": {"coin": "low", "muscle": "medium",
                       "legitimacy": "low"},
         "place": "Harnost"},
    ],
    "projects": [
        {"faction": "The Bridge Wardens", "title": "Raise the Ash Crossing",
         "objective": "A walkable bridge where the old one stood",
         "target": 4},
    ],
    "secrets": [
        {"faction": "The Ferrymen",
         "text": "The ferry league paid the arsonist, and the ledgers "
                 "would prove it."},
    ],
}

def _ground_names(prompt: str) -> list:
    """Single-token proper names the prompt's geography carries ("name: X"
    values). Loregen's hallucination check only flags MULTI-word
    capitalized phrases that resolve to nothing known, so entries built
    from these single names — with no invented multi-word phrases — pass
    against ANY world, the CI fixture's and a generated one alike."""
    out = []
    for m in re.finditer(r"name:\s*([A-Z][A-Za-z0-9'-]+)\s*$", prompt,
                         re.MULTILINE):
        n = m.group(1)
        if n not in out:
            out.append(n)
    return out


def _entries_yaml(prompt: str) -> str:
    names = _ground_names(prompt)
    a = names[0] if len(names) > 0 else "Harnost"
    b = names[1] if len(names) > 1 else a
    c = names[2] if len(names) > 2 else b
    return f"""- when: before week 1
  title: {a}
  text: Long before the fire, {a} grew rich on the crossing toll while
    the boatmen of {b} counted fares in the cold, and the resentment
    never once needed help to keep itself warm.
  new_names: []
- when: before week 1
  title: {b}
  text: The boatmen kept the ferry alive through two flood winters, and
    the elders of {b} still argue about which of the two towns broke
    the toll charter first.
  new_names: []
- when: before week 1
  title: {c}
  text: The fire began near the {c} landing, and no ledger in {a} or
    {b} ever owned the cause; the wardens posted a reward, the river
    kept the rest.
  new_names: []
"""

PROPOSAL_ACTIONS = [
    "patrol the crossing road through the night watch",
    "court the charter clerks over the toll ledger",
    "buy ferry concessions at the winter market",
    "watch the wharf gates for unlicensed fares",
]


def _proposal_yaml(prompt: str) -> str:
    """A valid minimal proposal, with the action text VARIED per faction —
    two factions filing the identical action give the week bundle two
    timeline entries with the same title+text, and the week accept's
    duplicate-identity guard refuses the whole publish (observed live
    2026-10-04; see the ticket file for the product-side note). Assigned
    sequentially per distinct faction name — hash() % N collides 1 run
    in 4 (observed), which is exactly the failure above."""
    m = re.search(r"name:\s*([A-Z][^\n]+)", prompt)
    name = m.group(1).strip() if m else ""
    with threading.Lock():
        seq = Handler.usage.setdefault("faction_actions", {})
        n = seq.get(name, 0)
        seq[name] = n + 1
    # every proposal text is UNIQUE (base action + pass number): a faction
    # legitimately repeating an action across weeks must not produce two
    # identical timeline entries — the week accept's duplicate-identity
    # guard refuses the whole publish on exact repeats (observed live
    # 2026-10-04, week 3 vs week 1; see the ticket file)
    action = (PROPOSAL_ACTIONS[n % len(PROPOSAL_ACTIONS)]
              + " \u2014 pass " + str(n + 1))
    return ("```yaml\n"
            "action: " + action + "\n"
            "target: world\n"
            "resources_committed: {}\n"
            "risk_acceptance: medium\n"
            "```")

CHRONICLE_PROSE = """The week passed on the crossing road. The wardens kept their
patrols, the ferrymen kept their boats, and the charred pilings kept
their silence. Nobody crossed the Vane who did not have to.

TITLE: The Quiet Crossing
"""

def _faction_from_roster(prompt: str):
    """The first faction NAME from the prompt's "Faction roster:" block
    (lines like "- The Bridge Wardens (resources: ...)"). resource_change
    suggestions must name a FACTION — a place name 422s as unresolved."""
    block = re.search(r"Faction roster:\n((?:  - .*\n?)+)", prompt)
    if block:
        m = re.search(r"-\s*(.+?)\s*\(resources:", block.group(1))
        if m:
            return m.group(1).strip()
    return None


def _extraction_yaml(prompt: str) -> str:
    """history_event + knowledge_fact + resource_change. The resource
    change is the point: it sets requires_confirmation=["resource_grade"],
    which is what makes Prepare refuse until the DM ticks the exact-grade
    checkbox (the spec's refusal probe)."""
    names = _ground_names(prompt)
    fac = _faction_from_roster(prompt) or (names[0] if names else "Harnost")
    return f"""suggestions:
  - kind: history_event
    actor: world
    text: The party argued the toll question through the night at the
      {fac} ferry house.
    places: [{fac}]
    evidence:
      - lines: 1-1
        quote: the party argued the toll question through the night
  - kind: knowledge_fact
    text: The ferry league's ledgers were moved to the salt warehouse.
    subjects: [{fac}]
    places: [{fac}]
    evidence:
      - lines: 2-2
        quote: ledgers moved to the salt warehouse
  - kind: resource_change
    faction: {fac}
    resource: legitimacy
    after: medium
    reason: Owning the toll argument overnight made the league look
      official.
    evidence:
      - lines: 2-2
        quote: ledgers moved to the salt warehouse
"""


def _reply_for(prompt: str) -> str:
    """One canned body per flow, chosen by substring — the prompts are
    distinct enough that one marker each is unambiguous."""
    if "sandbox designer" in prompt:
        return json.dumps(DESIGN_JSON)
    if "timeline entries" in prompt or (
            "TASK: write 3 to 6 timeline entries" in prompt):
        return "```yaml\n" + _entries_yaml(prompt) + "```"
    if "Return the JSON ONLY" in prompt and "canon" in prompt:
        return "[]"
    if "chronicler of" in prompt and 'TITLE:' in prompt:
        return CHRONICLE_PROSE
    if "Reply with the YAML block ONLY" in prompt or (
            "proposal" in prompt and "action:" not in prompt):
        return _proposal_yaml(prompt)
    if "session notes" in prompt and "suggestions:" in prompt:
        return "```yaml\n" + _extraction_yaml(prompt) + "```"
    return "ok."


# --------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    usage: dict = {}

    def log_message(self, fmt, *args):  # quieter runs
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/health"):
            self._send(200, b'{"ok": true}')
            return
        self._send(404, b'{"error": "not found"}')

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, b'{"error": "not found"}')
            return
        print(f"[stub] POST {self.path}", flush=True)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, b'{"error": "bad json"}')
            return
        messages = req.get("messages") or []
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        print(f"[stub] prompt {len(prompt)} chars; "
              f"replying", flush=True)
        if 'End with a single line "TITLE:' in prompt and CHRONICLE_DELAY_S > 0:
            time.sleep(CHRONICLE_DELAY_S)
        text = _reply_for(prompt)
        with threading.Lock():
            Handler.usage["calls"] = Handler.usage.get("calls", 0) + 1
        body = json.dumps({
            "id": "stub-1", "object": "chat.completion", "model": "e2e-stub",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                      "total_tokens": 150},
        }).encode("utf-8")
        self._send(200, body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8399)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    # "POST /usage" is not wired — read the counter via /health's extra
    # field instead, so the suite can assert the meter moved if it wants
    server.serve_forever()


if __name__ == "__main__":
    main()
