"""Premise-to-sandbox design (ticket 03, job lane ticket 42): validation
mapping, the bounded re-ask, fed-or-refused, and the no-LLM create path
the design prefills.

The endpoint is a 202 QUEUED JOB on the no-lock lane: the cheap refusals
(422) answer synchronously before the queue, everything else lands in the
job record — these tests wait the job out and read its result/error.
llm_client.complete is monkeypatched throughout — no test hits the
network. The ENGINE project check is real (src/projects.py --check via
subprocess, local and free): it is the validator the ticket names for
suggested projects, per the CLI-equivalence rule.
"""

import json
import time
from pathlib import Path

import pytest

import llm_client
import simdesign
import spine
import yaml
from conftest import wait_for_job

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
    client's _post_json would, so `usage` in the result is checkable.
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


def _design_job(client, payload):
    """POST the design and wait the queued job out (ticket 42: the pass is
    a 202 job now). Returns the finished job record."""
    r = client.post(DESIGN_URL, json=payload)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] and "queue_position" in body
    job = wait_for_job(client, body["job_id"], timeout=30.0)
    assert job["kind"] == "sim-design"
    return job


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
    job = _design_job(client, {"premise": PREMISE,
                               "places": ["Provis", "Dunmore"]})
    assert job["status"] == "done", job["error"]
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
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "done", job["error"]
    body = job["result"]
    design = body["design"]
    # one metered pass
    assert len(calls) == 1
    assert body["usage"]["calls"] == 1
    # the submission's places ride in the record (the reload restore
    # prefills the city/other-places fields from them)
    assert body["places"] == ["Provis"]
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
    # and the pass's words streamed into the job log
    assert any("fed:" in ln for ln in job["log_tail"])


def test_design_clears_unknown_faction_place_with_a_note(client,
                                                         tmp_spine_root,
                                                         monkeypatch):
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["factions"][0]["place"] = "Atlantis"
    _fake_llm(monkeypatch, [json.dumps(doc)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "done", job["error"]
    design = job["result"]["design"]
    assert "place" not in design["factions"][0], \
        "a place the sandbox will not have must not prefill the form"
    assert any("Atlantis" in n for n in job["result"]["notes"])


# ------------------------------------------------- validation mapping + job


def test_design_reasks_once_then_fails_with_validator_words(client,
                                                            tmp_spine_root,
                                                            monkeypatch):
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [GOALLESS])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error", job["result"]
    # bounded: exactly one re-ask, then the validators' words surface
    assert len(calls) == 2
    assert "failed validation twice" in job["error"]
    assert "needs a goal" in job["error"]


def test_design_reask_gets_the_validators_words(client, tmp_spine_root,
                                                monkeypatch):
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [GOALLESS, json.dumps(DESIGN_DOC)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "done", job["error"]
    assert len(calls) == 2
    # the re-ask carries the first attempt's diagnosis so the model can fix it
    assert "failed validation" in calls[1]
    assert "needs a goal" in calls[1]
    assert job["result"]["usage"]["calls"] == 2


def test_design_project_target_validated_by_engine(client, tmp_spine_root,
                                                   monkeypatch):
    """The engine's project normalizer is the authority: target 9 is
    outside 2-8 and its refusal words surface."""
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["projects"][0]["target"] = 9
    _fake_llm(monkeypatch, [json.dumps(doc)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert "2-8" in job["error"]


def test_design_project_for_unknown_faction_is_a_validation_failure(
        client, tmp_spine_root, monkeypatch):
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["projects"][0]["faction"] = "The Ghost Guild"
    _fake_llm(monkeypatch, [json.dumps(doc)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert "does not define" in job["error"]


def test_design_secret_for_unknown_faction_is_a_validation_failure(
        client, tmp_spine_root, monkeypatch):
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["secrets"][0]["faction"] = "The Ghost Guild"
    _fake_llm(monkeypatch, [json.dumps(doc)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert "does not define" in job["error"]


def test_design_premise_validated_like_the_forms(client, tmp_spine_root,
                                                 monkeypatch):
    """A 900-char design premise fails the form's own rule, and the form's
    words surface (the same message the create endpoint answers with)."""
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["premise"] = "tension " * 120
    _fake_llm(monkeypatch, [json.dumps(doc)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert "over 600 chars" in job["error"]


def test_design_budget_exceeded_fails_the_job(client, tmp_spine_root,
                                              monkeypatch):
    _fresh(tmp_spine_root)

    def spent(*args, **kwargs):
        raise llm_client.BudgetExceeded(
            "per-run LLM ceiling reached (1 call) — run stopped incomplete")

    monkeypatch.setattr(llm_client, "complete", spent)
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert "ceiling" in job["error"]


def test_design_without_key_fails_the_job(client, tmp_spine_root,
                                          monkeypatch):
    _fresh(tmp_spine_root)

    def no_key(*args, **kwargs):
        raise RuntimeError("no LLM API key found — set LLM_API_KEY in llm.env")

    monkeypatch.setattr(llm_client, "complete", no_key)
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert "LLM_API_KEY" in job["error"]


# ------------------------------------------------- the no-lock lane itself


def test_design_job_takes_no_campaign_lock(client, tmp_spine_root,
                                           monkeypatch):
    """Ticket 15's recorded decision survives the queue move (ticket 42):
    the design job never holds the campaign lock — a canon writer could
    run while it does. Pinned at the spine lock itself: while the job is
    inside its LLM call, the lock is free to acquire."""
    _fresh(tmp_spine_root)
    state = {"entered": False, "go": False}

    def slow(prompt, **kwargs):
        state["entered"] = True
        while not state["go"]:
            time.sleep(0.01)
        return json.dumps(DESIGN_DOC)

    monkeypatch.setattr(llm_client, "complete", slow)
    r = client.post(DESIGN_URL, json={"premise": PREMISE,
                                      "places": ["Provis"]})
    assert r.status_code == 202, r.text
    deadline = time.time() + 5.0
    while time.time() < deadline and not state["entered"]:
        time.sleep(0.02)
    assert state["entered"], "the job never reached its LLM call"
    lock = spine.campaign_lock(CAMP)
    acquired = lock.acquire(blocking=False)
    try:
        assert acquired, \
            "the design job must run WITHOUT holding the campaign lock"
    finally:
        if acquired:
            lock.release()
    state["go"] = True
    job = wait_for_job(client, r.json()["job_id"], timeout=30.0)
    assert job["status"] == "done", job["error"]


def test_design_writes_nothing(client, tmp_spine_root, monkeypatch):
    """Nothing staged, nothing canon: the campaign tree is byte-identical
    after a design, and simulation/state.yaml still does not exist. (The
    git-ignored .studio-jobs duration history is studio metadata, not
    campaign content — excluded, like the transactions dir would be.)"""
    _fresh(tmp_spine_root)
    _fake_llm(monkeypatch, [json.dumps(DESIGN_DOC)])

    def tree(root):
        return {str(p.relative_to(root)): p.read_bytes()
                for p in sorted(root.rglob("*")) if p.is_file()
                and ".studio-jobs" not in p.parts}

    before = tree(tmp_spine_root)
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "done", job["error"]
    after = tree(tmp_spine_root)
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
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    design = job["result"]["design"]
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


# ----------------------------------------------- ticket 15: hardening


def test_unparseable_json_reask_then_job_error(tmp_spine_root, client,
                                               monkeypatch):
    """A reply with no JSON object takes the same re-ask path as schema
    failures: two complete() calls at most, then a FAILED JOB quoting the
    diagnosis — never a raw exception."""
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [
        "Sure! Here is a lovely design in prose, no JSON at all.",
        "Also prose, sorry.",
    ])
    job = _design_job(client,
                      {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert "unusable reply" in job["error"]
    assert len(calls) == 2, "the unparseable reply must be re-asked once"


def test_overlong_request_premise_is_refused(tmp_spine_root, client):
    """The input paragraph cap is a 422 before any spend (the empty
    premise arm was covered; the length arm was not)."""
    _fresh(tmp_spine_root)
    r = client.post(DESIGN_URL,
                    json={"premise": "x" * 2001, "places": ["Provis"]})
    assert r.status_code == 422, r.text
    assert "2000" in r.json()["error"]


def test_places_list_is_capped(tmp_spine_root, client, monkeypatch):
    """A pasted gazetteer inflates every prompt attempt for a meter that
    counts calls, not tokens — the route refuses it up front, naming the
    cap; so does an overlong name."""
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [])
    r = client.post(DESIGN_URL,
                    json={"premise": PREMISE,
                          "places": [f"Place {i}" for i in range(13)]})
    assert r.status_code == 422 and "at most 12" in r.json()["error"]
    r = client.post(DESIGN_URL,
                    json={"premise": PREMISE,
                          "places": ["Provis", "x" * 121]})
    assert r.status_code == 422 and "under 120 characters" in r.json()["error"]
    assert calls == [], "a refusal must not spend a call"


def test_unreadable_geography_refuses_cleanly(tmp_spine_root, client,
                                              monkeypatch):
    """A geography.yaml the process cannot read (the Maps pipeline
    swapping it) is a readable 422 after the retries, never a 500 — and
    the retry actually retried."""
    _fresh(tmp_spine_root)
    geo = tmp_spine_root / CAMP / "world" / "geography.yaml"
    real_read = Path.read_text
    attempts = []

    def flaky(self, *a, **k):
        if self == geo:
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError("sharing violation")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    # a valid reply, so the pass succeeds once the read retried through
    _fake_llm(monkeypatch, [])
    monkeypatch.setattr(
        llm_client, "complete",
        lambda prompt, **k: json.dumps(DESIGN_DOC))
    job = _design_job(client, {"premise": PREMISE})
    assert job["status"] == "done", job["error"]
    # the grounding is read at least twice (the router's synchronous
    # pre-flight, then the job's own fresh read) and the flaky read
    # retried through inside the pre-flight
    assert len(attempts) >= 3, "the read must retry before failing"

    # and a persistently unreadable world refuses (a 422 at submit: the
    # grounding pre-flight owns it), never a 500
    def always_fail(self, *a, **k):
        if self == geo:
            raise PermissionError("locked")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", always_fail)
    r = client.post(DESIGN_URL, json={"premise": PREMISE})
    assert r.status_code == 422, r.text
    assert "could not be read" in r.json()["error"]


def test_reask_quotes_are_clamped(tmp_spine_root, monkeypatch):
    """The re-ask prompt clamps the validator's words: those words embed
    model-authored strings, and a hostile reply must not author its own
    re-ask at length."""
    _fresh(tmp_spine_root)
    calls = _fake_llm(monkeypatch, [
        # first reply: a faction NAME that is an essay — it lands in the
        # validator's message, which the re-ask must clamp
        json.dumps({**DESIGN_DOC,
                    "factions": [
                        {**DESIGN_DOC["factions"][0],
                         "name": "INJECT " + "x" * 2000},
                        DESIGN_DOC["factions"][1]]}),
        json.dumps(DESIGN_DOC),
    ])
    out = simdesign.design_simulation(CAMP, PREMISE, places=["Provis"])
    assert out["design"]["factions"][0]["name"] == "The Bridge Wardens"
    assert len(calls) == 2
    re_ask = calls[1]
    assert "INJECT" not in re_ask or         len(re_ask) < len(calls[0]) + simdesign._REASK_QUOTE_MAX + 200


# ----------------------------------------------- ticket 21: reply shapes


def test_scalar_resources_reasks_then_succeeds(client, tmp_spine_root,
                                               monkeypatch):
    """A reply with scalar `resources` ("high") takes the bounded re-ask
    path with the issue named in the quote, then succeeds — never the
    AttributeError 500 the raw value used to cause in
    spine.validate_faction_docs (.items())."""
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["factions"][0]["resources"] = "high"
    calls = _fake_llm(monkeypatch, [json.dumps(doc), json.dumps(DESIGN_DOC)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "done", job["error"]
    assert len(calls) == 2
    assert "must be a mapping of resource to level" in calls[1]
    assert "not a str" in calls[1]
    assert job["result"]["usage"]["calls"] == 2


def test_list_resources_reasks_once_then_502_with_the_issue(
        client, tmp_spine_root, monkeypatch):
    """A list `resources` is the same shape class: one re-ask, then 502
    quoting the issue — not a 500."""
    _fresh(tmp_spine_root)
    doc = json.loads(json.dumps(DESIGN_DOC))
    doc["factions"][1]["resources"] = ["coin", "muscle"]
    calls = _fake_llm(monkeypatch, [json.dumps(doc), json.dumps(doc)])
    job = _design_job(client, {"premise": PREMISE, "places": ["Provis"]})
    assert job["status"] == "error"
    assert len(calls) == 2
    assert "must be a mapping of resource to level" in job["error"]
    assert "not a list" in job["error"]


def test_spine_rejects_non_dict_resources_as_campaign_error():
    """The spine guard behind the coercion: validate_faction_docs answers a
    non-dict resources with CampaignError (4xx on every route), never an
    AttributeError."""
    factions = [
        {"name": "First", "goal": "hold the bridge", "resources": "high"},
        {"name": "Second", "goal": "burn the bridge"},
    ]
    with pytest.raises(spine.CampaignError, match="not a str"):
        spine.validate_faction_docs(factions)
