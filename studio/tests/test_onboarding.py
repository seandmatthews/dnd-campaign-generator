"""The onboarding wave: create-a-simulation from the UI, staging a name
seed list, the /tutorial route, and the doctor's quick mode.

These cover the first-campaign path a newcomer walks (TUTORIAL.md): a
fresh campaign has no simulation/state.yaml and no names-<culture>.txt —
until now there was no way to create either from the app.
"""

import yaml
import pytest


@pytest.fixture()
def fresh_campaign(client, tmp_spine_root):
    """A brand-new campaign via the real first-campaign path (POST
    /api/campaigns) — no simulation, no lore, exactly what a newcomer has."""
    r = client.post("/api/campaigns", json={"name": "onboard", "title": "Onboard"})
    assert r.status_code == 201, r.text
    return "onboard"


def _body(**over):
    body = {
        "premise": "The toll bridge burned the night the wardens raised the toll.",
        "city": {"name": "Provis"},
        "factions": [
            {"name": "The Bridge Wardens", "kind": "toll Brotherhood",
             "goal": "rebuild the bridge and the toll with it",
             "resources": {"coin": "medium", "muscle": "low", "legitimacy": "high"}},
            {"name": "The Ferrymen", "goal": "keep the crossing broken",
             "resources": {"muscle": "medium"}},
        ],
    }
    body.update(over)
    return body


def test_create_state_happy_path_writes_valid_spine(client, tmp_spine_root,
                                                    fresh_campaign):
    name = fresh_campaign
    assert client.get(f"/api/campaign/{name}/simulation").status_code == 404
    r = client.post(f"/api/campaign/{name}/simulation/state", json=_body())
    assert r.status_code == 201, r.text
    assert r.json()["week"] == 0
    assert r.json()["factions"] == ["the_bridge_wardens", "the_ferrymen"]

    doc = yaml.safe_load(
        (tmp_spine_root / name / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert doc["meta"]["clock"] == {"week": 0, "season": "early spring"}
    assert doc["meta"]["premise"].startswith("The toll bridge burned")
    assert doc["city"] == {"name": "Provis"}
    (w, f) = doc["factions"]
    assert w["id"] == "the_bridge_wardens"
    assert w["resources"] == {"coin": "medium", "muscle": "low", "legitimacy": "high"}
    # missing resources default to low; kind omitted when empty
    assert f["resources"] == {"coin": "low", "muscle": "medium", "legitimacy": "low"}
    assert "kind" not in f
    assert doc["events"] == [] and doc["rumors"] == []
    # the tab reads it back
    assert client.get(f"/api/campaign/{name}/simulation").status_code == 200


def test_create_state_city_and_season(client, tmp_spine_root, fresh_campaign):
    name = fresh_campaign
    r = client.post(f"/api/campaign/{name}/simulation/state",
                    json=_body(city={"name": "Provis", "population": 12300},
                               season="late autumn"))
    assert r.status_code == 201
    doc = yaml.safe_load(
        (tmp_spine_root / name / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert doc["city"] == {"name": "Provis", "population": 12300}
    assert doc["meta"]["clock"]["season"] == "late autumn"


def test_create_state_409_without_force_and_replace_with(client, tmp_spine_root,
                                                         fresh_campaign):
    name = fresh_campaign
    url = f"/api/campaign/{name}/simulation/state"
    assert client.post(url, json=_body()).status_code == 201
    r = client.post(url, json=_body())
    assert r.status_code == 409
    assert "force" in r.json()["error"]
    assert client.post(url, json=_body(force=True)).status_code == 201
    doc = yaml.safe_load(
        (tmp_spine_root / name / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert doc["meta"]["clock"]["week"] == 0  # a fresh sandbox again


def test_create_state_validation(client, tmp_spine_root, fresh_campaign):
    name = fresh_campaign
    url = f"/api/campaign/{name}/simulation/state"
    six = [{"name": f"Faction {i}", "goal": f"wants {i}"} for i in range(6)]
    cases = [
        (_body(premise="   "), "premise"),
        (_body(city=None), "needs a city"),
        (_body(city={"name": "   "}), "needs a city"),
        (_body(city={"name": "Provis", "population": 0}), "positive"),
        (_body(factions=[]), "2-5 factions"),
        (_body(factions=[_body()["factions"][0]]), "2-5 factions"),
        (_body(factions=six), "2-5"),
        (_body(factions=[
            {"name": "A", "goal": "wants a"},
            {"name": "B", "goal": ""}]), "goal"),
        (_body(factions=[
            {"name": "Same Name", "goal": "x"},
            {"name": "same  name", "goal": "y"}]), "same id"),
        (_body(factions=[
            {"name": "A", "goal": "x", "resources": {"luck": "high"}},
            {"name": "B", "goal": "y"}]), "unknown resource"),
        (_body(factions=[
            {"name": "A", "goal": "x", "resources": {"coin": "vast"}},
            {"name": "B", "goal": "y"}]), "none, low, medium, high"),
        (_body(factions=[
            {"name": "world", "goal": "x"},
            {"name": "B", "goal": "y"}]), "reserved"),
    ]
    for body, fragment in cases:
        r = client.post(url, json=body)
        assert r.status_code == 422, body
        assert fragment in r.json()["error"], fragment
    # nothing was written by any rejected attempt
    assert not (tmp_spine_root / fresh_campaign / "simulation" / "state.yaml").exists()


# --------------------------------------------- multi-place sandboxes (places)

def test_create_state_with_places_writes_multi_place_schema(
        client, tmp_spine_root, fresh_campaign):
    """Extra places switch the sandbox to the engine's `places` schema and
    let a faction be born outside the primary city — the two-tier setup."""
    name = fresh_campaign
    r = client.post(f"/api/campaign/{name}/simulation/state", json=_body(
        city={"name": "Provis", "population": 12300},
        places=[{"name": "Harmouth"},
                {"name": "the Kingdom of Sweth", "kind": "nation"}],
        factions=_body()["factions"] + [
            {"name": "The Crown of Sweth", "goal": "own every crossing",
             "place": "The Kingdom of Sweth"}],
    ))
    assert r.status_code == 201, r.text
    doc = yaml.safe_load(
        (tmp_spine_root / name / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert "city" not in doc, "a multi-place sandbox carries places, not city"
    assert [p["id"] for p in doc["places"]] == [
        "provis", "harmouth", "the-kingdom-of-sweth"]
    assert doc["places"][0]["kind"] == "city" and doc["places"][0]["population"] == 12300
    assert doc["places"][1] == {"id": "harmouth", "name": "Harmouth",
                                "kind": "burg"}, "extras default to burgs"
    assert doc["places"][2]["kind"] == "nation"
    by_name = {f["name"]: f for f in doc["factions"]}
    assert "place" not in by_name["The Bridge Wardens"], \
        "a faction naming no place stays on the primary city"
    assert by_name["The Crown of Sweth"]["place"] == "the-kingdom-of-sweth", \
        "the place name is resolved to the place id"
    # the tab reads the normalized places back for the roster
    sim = client.get(f"/api/campaign/{name}/simulation").json()
    assert [p["id"] for p in sim["places"]] == [
        "provis", "harmouth", "the-kingdom-of-sweth"]


def test_create_state_refuses_an_unknown_faction_place(
        client, tmp_spine_root, fresh_campaign):
    name = fresh_campaign
    r = client.post(f"/api/campaign/{name}/simulation/state", json=_body(
        places=[{"name": "Harmouth"}],
        factions=_body()["factions"] + [
            {"name": "The Crown of Sweth", "goal": "own every crossing",
             "place": "Swethholm"}],
    ))
    assert r.status_code == 422
    assert "Swethholm" in r.json()["error"]
    assert "Harmouth" in r.json()["error"], "the valid places are named"
    # a place reference to the city itself resolves (and the legacy city
    # shape stays when it is the only place)
    r = client.post(f"/api/campaign/{name}/simulation/state", json=_body(
        factions=[{"name": "A", "goal": "x", "place": "provis"},
                  {"name": "B", "goal": "y"}]))
    assert r.status_code == 201
    doc = yaml.safe_load(
        (tmp_spine_root / name / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert "places" not in doc and doc["city"] == {"name": "Provis"}, \
        "no extras means the legacy shape, wherever a faction points"


def test_create_state_refuses_duplicate_places(client, tmp_spine_root,
                                               fresh_campaign):
    name = fresh_campaign
    url = f"/api/campaign/{name}/simulation/state"
    r = client.post(url, json=_body(places=[{"name": "Provis"}]))
    assert r.status_code == 422
    assert "duplicates" in r.json()["error"]
    r = client.post(url, json=_body(places=[{"name": "Harmouth"},
                                            {"name": "harmouth"}]))
    assert r.status_code == 422
    assert not (tmp_spine_root / name / "simulation" / "state.yaml").exists()


# ------------------------------------------------- stage a name seed list (POST style)

def test_stage_names_list_happy_path(client, tmp_spine_root, fresh_campaign):
    url = f"/api/campaign/{fresh_campaign}/lore/style/names-shadian.txt"
    r = client.post(url, json={"text": "# seeds\nKestra\nOlvane\nDross\n"})
    assert r.status_code == 201, r.text
    text = (tmp_spine_root / fresh_campaign / "lore" / "style" /
            "names-shadian.txt").read_text(encoding="utf-8")
    assert "Kestra" in text
    # PUT can now edit it; a second POST is a 409 pointing at Style & voice
    assert client.post(url, json={"text": "A\nB\nC\n"}).status_code == 409
    assert client.put(url, json={"text": "A\nB\nC\n"}).status_code == 200


def test_stage_names_list_needs_three_seeds(client, tmp_spine_root, fresh_campaign):
    url = f"/api/campaign/{fresh_campaign}/lore/style/names-shadian.txt"
    for text in ("", "Kestra", "# only a comment\n\nKestra"):
        r = client.post(url, json={"text": text})
        assert r.status_code == 422, text
        assert "3" in r.json()["error"]
    # nothing half-staged
    assert not (tmp_spine_root / fresh_campaign / "lore" / "style" /
                "names-shadian.txt").exists()


def test_stage_style_rejects_bad_filenames(client, tmp_spine_root, fresh_campaign):
    # a filename outside [A-Za-z0-9._-] (e.g. a culture name with a space)
    # is a clean 422, not a write
    r = client.post(f"/api/campaign/{fresh_campaign}/lore/style/bad%20name.txt",
                    json={"text": "a\nb\nc\n"})
    assert r.status_code == 422
    assert client.post(
        f"/api/campaign/{fresh_campaign}/lore/style/names-shadian.txt",
        json={"text": "Kestra\nOlvane\nDross\n"}).status_code == 201


# --------------------------------------------------------- /tutorial route

def test_tutorial_route_serves_repo_readme_sibling(client):
    from conftest import STUDIO_DIR
    r = client.get("/tutorial")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "first campaign" in body.lower()
    # the route reads the real file, never a copy
    disk = (STUDIO_DIR.parent / "TUTORIAL.md").read_text(encoding="utf-8")
    assert body == disk


# ------------------------------------------------------- doctor quick mode

def test_doctor_quick_skips_live_probe(client, monkeypatch):
    import doctor
    calls = {}
    real_report = doctor.report

    def spy_report(*args, **kwargs):
        calls["kwargs"] = kwargs
        return {"ok": True, "checks": []}

    monkeypatch.setattr(doctor, "report", spy_report)
    client.get("/api/doctor")
    assert calls["kwargs"] == {}          # default path: unchanged call shape
    client.get("/api/doctor?quick=1")
    assert calls["kwargs"] == {"live_llm": False}
    # no monkeypatch.undo() here: undoing the shared instance would also tear
    # down fixture patches (e.g. a tmp campaigns root). The original captured
    # above is called directly instead.
    assert real_report(live_llm=False)["ok"] in (True, False)  # accepts the kwarg


def test_check_llm_key_is_presence_only(monkeypatch, tmp_path):
    import doctor
    import llm_client
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "ZAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm_client, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(llm_client, "_CONFIG", tmp_path / "absent.json")
    monkeypatch.setenv("ZAI_API_KEY", "k")
    check = doctor.check_llm_key()
    assert check["ok"] is True
    assert "not probed" in check["detail"]
    monkeypatch.delenv("ZAI_API_KEY")
    check = doctor.check_llm_key()
    assert check["ok"] is False and check["required"] is False
    assert "everything else works without" in check["fix"]
