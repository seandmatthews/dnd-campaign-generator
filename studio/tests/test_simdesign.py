"""Premise-to-sandbox design (ticket 03): validation mapping, the bounded
re-ask, fed-or-refused, and the no-LLM create path the design prefills.

llm_client.complete is monkeypatched throughout — no test hits the network.
The ENGINE project check is real (src/projects.py --check via subprocess,
local and free): it is the validator the ticket names for suggested
projects, per the CLI-equivalence rule.
"""

import json

import llm_client
import simdesign
import spine
import yaml

CAMP = "designvale"
DESIGN_URL = f"/api/campaign/{CAMP}/simulation/design"
PREMISE = ("The toll bridge burned the night the wardens raised the toll, "
           "and the ferry league is suspected of lighting it.")

GEO_YAML = """\
nations:
  - id: shadia-1
    name: Shadia
    kind: Free City
major_settlements:
  - name: Provis
    nation: Shadia
    population_thousands: 12.3
"""

DESIGN_DOC = {
    "premise": "The toll bridge burned the night the wardens raised the toll.",
    "factions": [
        {"name": "The Bridge Wardens", "kind": "toll brotherhood",
         "goal": "rebuild the bridge and raise the toll higher than ever",
         "resources": {"coin": "medium", "muscle": "low",
                       "legitimacy": "high"},
         "place": "Provis"},
        {"name": "The Ferry League", "kind": "boatmen guild",
         "goal": "keep the crossing ferry-only until the crown recharters it",
         "resources": {"coin": "low", "muscle": "medium"}},
    ],
    "projects": [
        {"faction": "The Bridge Wardens", "title": "Raise the Sealed Toll",
         "objective": "The new bridge opens with the toll sealed to the "
                      "wardens' charter.", "target": 6},
    ],
    "secrets": [
        {"faction": "The Ferry League",
         "text": "The league's harbormaster arranged the fire — the ferry "
                 "monopoly was dying anyway."},
    ],
}


def _fresh(tmp_spine_root, geo=True):
    spine.create_campaign(CAMP, "Design Vale")
    if geo:
        (tmp_spine_root / CAMP / "world" / "geography.yaml").write_text(
            GEO_YAML, encoding="utf-8")


def _fake_llm(monkeypatch, replies):
    """Patch llm_client.complete with a scripted list of replies (last one
    repeats); each call also bumps the real per-run meter the way the real
    client's _post_json would, so `usage` in the response is checkable.
    Returns the list of prompts received."""
    calls = []

    def fake(prompt, **kwargs):
        calls.append(prompt)
        budget = llm_client._budget()
        budget["calls"] += 1
        budget["tokens"] += 10
        return replies[min(len(calls), len(replies)) - 1]

    monkeypatch.setattr(llm_client, "complete", fake)
    return calls


# three legal-count factions where one has no goal: the count validator
# passes, so the surfaced words are the GOAL validator's
GOALLESS = json.dumps({
    "premise": "x",
    "factions": [{"name": "First", "goal": "hold the bridge"},
                 {"name": "Second", "goal": "burn the bridge"},
                 {"name": "Lonely"}],
})


# ------------------------------------------------- fed or refused (guardrail)


def test_design_422_without_world_and_places(client, tmp_spine_root,
                                             monkeypatch):
    _fresh(tmp_spine_root, geo=False)
    calls = _fake_llm(monkeypatch, [])
    r = client.post(DESIGN_URL, json={"premise": PREMISE})
    assert r.status_code == 422
    assert "fed or refused" in r.json()["error"]
    assert calls == [], "refused before any model call"


def test_design_runs_on_places_alone(client, tmp_spine_root, monkeypatch):
    """The 'or places' arm: no world file, but the DM named places — the
    grounding exists, so the pass runs."""
    _fresh(tmp_spine_root, geo=False)
    calls = _fake_llm(monkeypatch, [json.dumps(DESIGN_DOC)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis", "Dunmore"]})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert "Provis" in calls[0] and "Dunmore" in calls[0]


def test_design_422_on_empty_premise(client, tmp_spine_root):
    _fresh(tmp_spine_root)
    r = client.post(DESIGN_URL, json={"premise": "   "})
    assert r.status_code == 422
    assert "premise" in r.json()["error"]


# ------------------------------------------------ happy path + form shaping


def test_design_returns_form_shaped_payload(client, tmp_spine_root,
                                            monkeypatch):
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [json.dumps(DESIGN_DOC)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 200, r.text
    body = r.json()
    design = body["design"]
    # one metered pass
    assert len(calls) == 1
    assert body["usage"]["calls"] == 1
    # the premise follows the form's rule
    assert design["premise"].startswith("The toll bridge burned")
    assert len(design["premise"]) <= simdesign.PREMISE_MAX
    # factions are exactly what the create form accepts
    factions = design["factions"]
    assert [f["name"] for f in factions] == ["The Bridge Wardens",
                                             "The Ferry League"]
    assert factions[0]["resources"] == {"coin": "medium", "muscle": "low",
                                        "legitimacy": "high"}
    # the validator's default filled the missing legitimacy
    assert factions[1]["resources"]["legitimacy"] == "low"
    assert factions[0]["place"] == "Provis"
    # secrets NEVER ride in the faction docs
    assert all("secret" not in f for f in factions)
    assert design["secrets"] == [
        {"faction": "The Ferry League",
         "text": "The league's harbormaster arranged the fire — the ferry "
                 "monopoly was dying anyway."}]
    # the suggested project hint came back (the engine normalized it)
    assert design["projects"][0]["faction"] == "The Bridge Wardens"
    assert design["projects"][0]["target"] == 6
    # the grounding fed the prompt (nations + named places)
    assert "Shadia" in calls[0] and "Provis" in calls[0]


def test_design_clears_unknown_faction_place_with_a_note(client,
                                                         tmp_spine_root,
                                                         monkeypatch):
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["factions"][0]["place"] = "Atlantis"
    _fake_llm(monkeypatch, [json.dumps(doc)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 200, r.text
    design = r.json()["design"]
    assert "place" not in design["factions"][0], \
        "a place the sandbox will not have must not prefill the form"
    assert any("Atlantis" in n for n in r.json()["notes"])


# ------------------------------------------------- validation mapping + 502


def test_design_reasks_once_then_502_with_validator_words(client,
                                                          tmp_spine_root,
                                                          monkeypatch):
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [GOALLESS])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 502, r.text
    # bounded: exactly one re-ask, then the validators' words surface
    assert len(calls) == 2
    assert "failed validation twice" in r.json()["error"]
    assert "needs a goal" in r.json()["error"]


def test_design_reask_gets_the_validators_words(client, tmp_spine_root,
                                                monkeypatch):
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [GOALLESS, json.dumps(DESIGN_DOC)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 200, r.text
    assert len(calls) == 2
    # the re-ask carries the first attempt's diagnosis so the model can fix it
    assert "failed validation" in calls[1]
    assert "needs a goal" in calls[1]
    assert r.json()["usage"]["calls"] == 2


def test_design_project_target_validated_by_engine(client, tmp_spine_root,
                                                   monkeypatch):
    """The engine's project normalizer is the authority: target 9 is
    outside 2-8 and its refusal words surface."""
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["projects"][0]["target"] = 9
    _fake_llm(monkeypatch, [json.dumps(doc)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 502, r.text
    assert "2-8" in r.json()["error"]


def test_design_project_for_unknown_faction_is_a_validation_failure(
        client, tmp_spine_root, monkeypatch):
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["projects"][0]["faction"] = "The Ghost Guild"
    _fake_llm(monkeypatch, [json.dumps(doc)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 502
    assert "does not define" in r.json()["error"]


def test_design_secret_for_unknown_faction_is_a_validation_failure(
        client, tmp_spine_root, monkeypatch):
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["secrets"][0]["faction"] = "The Ghost Guild"
    _fake_llm(monkeypatch, [json.dumps(doc)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 502
    assert "does not define" in r.json()["error"]


def test_design_premise_validated_like_the_forms(client, tmp_spine_root,
                                                 monkeypatch):
    """A 900-char design premise fails the form's own rule, and the form's
    words surface (the same 422 message the create endpoint answers with)."""
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["premise"] = "tension " * 120
    _fake_llm(monkeypatch, [json.dumps(doc)])
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 502
    assert "over 600 chars" in r.json()["error"]


def test_design_budget_exceeded_is_503(client, tmp_spine_root, monkeypatch):
    _fresh(tmp_spine_root)

    def spent(*args, **kwargs):
        raise llm_client.BudgetExceeded(
            "per-run LLM ceiling reached (1 call) — run stopped incomplete")

    monkeypatch.setattr(llm_client, "complete", spent)
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 503
    assert "ceiling" in r.json()["error"]


def test_design_without_key_is_503(client, tmp_spine_root, monkeypatch):
    _fresh(tmp_spine_root)

    def no_key(*args, **kwargs):
        raise RuntimeError("no LLM API key found — set LLM_API_KEY in llm.env")

    monkeypatch.setattr(llm_client, "complete", no_key)
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 503
    assert "LLM_API_KEY" in r.json()["error"]


# ------------------------------------------------------- nothing is touched


def test_design_writes_nothing(client, tmp_spine_root, monkeypatch):
    """Nothing staged, nothing canon: the campaign tree is byte-identical
    after a design, and simulation/state.yaml still does not exist."""
    _fresh(tmp_spine_root)
    _fake_llm(monkeypatch, [json.dumps(DESIGN_DOC)])
    before = {str(p.relative_to(tmp_spine_root)): p.read_bytes()
              for p in sorted(tmp_spine_root.rglob("*")) if p.is_file()}
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 200, r.text
    after = {str(p.relative_to(tmp_spine_root)): p.read_bytes()
             for p in sorted(tmp_spine_root.rglob("*")) if p.is_file()}
    assert after == before
    assert not (tmp_spine_root / CAMP / "simulation" / "state.yaml").exists()


def test_design_404_unknown_campaign(client, spine_root, monkeypatch):
    _fake_llm(monkeypatch, [])
    r = client.post("/api/campaign/nonesuch/simulation/design",
                    json={"premise": PREMISE})
    assert r.status_code == 404


# ------------------------------------------- the no-LLM create path (accept)


def test_design_prefills_a_form_the_free_path_accepts(client, tmp_spine_root,
                                                      monkeypatch):
    """The design payload, verbatim, through the unchanged create endpoint:
    201, ids derived, and the secret stays out of the state payload."""
    _fresh(tmp_spine_root)
    _fake_llm(monkeypatch, [json.dumps(DESIGN_DOC)])
    design = client.post(DESIGN_URL, json={"premise": PREMISE,
                                           "places": ["Provis"]}
                         ).json()["design"]
    body = {
        "premise": design["premise"],
        "city": {"name": "Provis", "population": 12300},
        "factions": [{"name": f["name"], "kind": f.get("kind", ""),
                      "goal": f["goal"], "resources": f["resources"],
                      **({"place": f["place"]} if f.get("place") else {})}
                     for f in design["factions"]],
    }
    r = client.post(f"/api/campaign/{CAMP}/simulation/state", json=body)
    assert r.status_code == 201, r.text
    state_text = (tmp_spine_root / CAMP / "simulation" / "state.yaml"
                  ).read_text(encoding="utf-8")
    state = yaml.safe_load(state_text)
    assert [f["id"] for f in state["factions"]] == ["the_bridge_wardens",
                                                    "the_ferry_league"]
    assert state["factions"][0]["place"] == "provis"
    assert all("secret" not in f for f in state["factions"])
    assert "harbormaster" not in state_text, \
        "the secret draft never enters the state payload"


# ------------------------------------------------------------------- the CLI


def test_cli_design_prints_json(tmp_spine_root, monkeypatch, capsys):
    _fresh(tmp_spine_root)
    _fake_llm(monkeypatch, [json.dumps(DESIGN_DOC)])
    rc = simdesign.main([CAMP, "--design", "--premise", PREMISE,
                         "--place", "Provis"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["design"]["premise"].startswith("The toll bridge burned")
    assert doc["design"]["secrets"][0]["faction"] == "The Ferry League"


def test_cli_design_out_file(tmp_spine_root, monkeypatch, tmp_path, capsys):
    _fresh(tmp_spine_root)
    _fake_llm(monkeypatch, [json.dumps(DESIGN_DOC)])
    out = tmp_path / "design.json"
    rc = simdesign.main([CAMP, "--design", "--premise", PREMISE,
                         "--place", "Provis", "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert len(doc["design"]["factions"]) == 2


def test_cli_design_refused_exit_1(tmp_spine_root, monkeypatch, capsys):
    _fresh(tmp_spine_root, geo=False)
    _fake_llm(monkeypatch, [])
    rc = simdesign.main([CAMP, "--design", "--premise", PREMISE])
    assert rc == 1
    assert "refused" in capsys.readouterr().err


def test_cli_design_usage_error_exit_2(tmp_spine_root, monkeypatch, capsys):
    _fresh(tmp_spine_root)
    _fake_llm(monkeypatch, [])
    rc = simdesign.main([CAMP, "--premise", PREMISE])  # no --design
    assert rc == 2
