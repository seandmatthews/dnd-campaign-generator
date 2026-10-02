"""Ticket 08: the rumor → handout pipeline's frontend affordances.

The handoff payload itself is a pure module (js/handoff.js, its shape
tested by studio/tests/js/test_handoff.cjs under node — the fast-tier
"prefill payload shape" suite); these checks pin the WIRING the ticket
names: a "Tell the party" affordance on the rumor rows (the Simulation
tab's rumors board and the proposed-rumors list beside the chronicle), the
Knowledge tab's record form carrying and consuming the prefill
(text/visibility/source/week), and the module loading before its users.
Served-bytes assertions only — no engine calls, no writes; the recorded
rumor-fact's behavior (chronicle source_file, staleness, reconcile) is
covered in test_knowledge.py.
"""

from conftest import client  # noqa: F401


def _js(client, name):
    r = client.get(f"/js/{name}")
    assert r.status_code == 200, r.text
    return r.text


def test_sim_tab_carries_tell_the_party_on_both_rumor_lists(client):
    sim = _js(client, "sim.js")
    assert "Tell the party" in sim
    # both rumor lists: the rumors board rows and the proposal panel's
    # proposed rumors beside the proposed chronicle
    assert sim.count('"btn rumor-tell"') >= 2
    assert "rumorHandoff" in sim       # the payload builder
    assert "factPrefill" in sim        # parking it for the Knowledge tab


def test_knowledge_form_carries_and_consumes_the_prefill(client):
    js = _js(client, "knowledge.js")
    assert "fact-week" in js           # the week field the prefill fills
    assert "factPrefill" in js         # the parked payload, consumed here
    assert "fact-prefill-note" in js   # the says-what-happened note
    assert "still its own step" in js  # the two-step rule, restated
    assert "week:" in js               # the record body carries the week


def test_handoff_module_is_served_and_loads_before_its_users(client):
    html = client.get("/").text
    assert 'src="js/handoff.js"' in html
    # plain scripts: load order is the contract — the module must be
    # defined before sim.js (producer) and knowledge.js (consumer) run
    assert html.index("js/handoff.js") < html.index("js/sim.js")
    assert html.index("js/handoff.js") < html.index("js/knowledge.js")
