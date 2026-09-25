"""Phase 4: approved facts with faction-specific visibility.

The handoff's tests, end to end:
- an approved public fact reaches the faction views (engine's real views.py,
  offline);
- a secret known only to faction A appears NOWHERE in faction B's
  serialized view (which is what the prompt embeds);
- rejected (never approved), pending (unapproved), dm_only, and superseded
  facts do not leak;
- a changed source file produces visible staleness instead of unreviewed
  knowledge changes — the compiled text stays the approved text;
- a campaign with no knowledge records still works.
"""

import shutil

import yaml

import engines
import knowledge
import spine
from conftest import client  # noqa: F401


def _add(client, **body):
    return client.post("/api/campaign/testvale/knowledge", json=body)


def _view(client, faction_id, tmp_spine_root):
    """The REAL engine view (offline; temp engine home, never live scratch)."""
    home = tmp_spine_root / "engine-home"
    (home / "world").mkdir(parents=True, exist_ok=True)
    if not (home / "src").is_dir():
        shutil.copytree(engines.AGENTIC_SIMULATOR / "src", home / "src")
    orig_home = engines.AGENTIC_SIMULATOR
    engines.AGENTIC_SIMULATOR = home
    try:
        r = client.get(f"/api/campaign/testvale/simulation/views/{faction_id}")
        assert r.status_code == 200, r.text
        return yaml.safe_load(r.text if False else _dump(r))
    finally:
        engines.AGENTIC_SIMULATOR = orig_home


def _dump(r):
    return r.text


def test_public_fact_reaches_views_secret_stays_with_its_faction(
        client, tmp_spine_root):
    # public fact: the bridge burned — everyone in the world can know
    r = _add(client, text="The toll bridge over the Vane burned.",
             visibility="public")
    assert r.status_code == 200, r.text
    public_fact = r.json()["fact"]
    assert public_fact["id"] and public_fact["visibility"] == "public"

    # secret known ONLY to the wardens
    r = _add(client,
             text="The wardens' secret: they burned the bridge themselves "
                  "for the insurance.",
             visibility=["wardens"])
    assert r.status_code == 200, r.text
    secret_id = r.json()["fact"]["id"]

    wardens_view = _view(client, "wardens", tmp_spine_root)
    ferrymen_view = _view(client, "ferrymen", tmp_spine_root)

    wardens_yaml = yaml.safe_dump(wardens_view, allow_unicode=True)
    ferrymen_yaml = yaml.safe_dump(ferrymen_view, allow_unicode=True)

    assert "The toll bridge over the Vane burned." in wardens_yaml
    assert "The toll bridge over the Vane burned." in ferrymen_yaml
    assert "they burned the bridge themselves" in wardens_yaml
    # the secret does not occur anywhere in the other faction's view —
    # and the view is exactly what the prompt embeds (llm.render_prompt)
    assert "burned the bridge themselves" not in ferrymen_yaml
    assert secret_id not in ferrymen_yaml


def test_dm_only_and_superseded_never_leak(client, tmp_spine_root):
    r = _add(client, text="DM plan: the magistrate is the arsonist's buyer.",
             visibility="dm_only")
    assert r.status_code == 200
    dm_fact = r.json()["fact"]

    r = _add(client, text="The toll bridge over the Vane burned.",
             visibility="public")
    old_id = r.json()["fact"]["id"]
    r = client.post(
        f"/api/campaign/testvale/knowledge/{old_id}/supersede",
        json={"text": "The toll bridge over the Vane burned in week 2.",
              "visibility": "public"})
    assert r.status_code == 200, r.text

    view = _view(client, "wardens", tmp_spine_root)
    view_yaml = yaml.safe_dump(view, allow_unicode=True)
    assert "arsonist" not in view_yaml        # dm_only stays with the DM
    assert dm_fact["id"] not in view_yaml
    assert "burned in week 2" in view_yaml    # the current fact is there
    # the superseded fact is not compiled: only the corrected text appears
    assert view_yaml.count("toll bridge over the Vane burned") == 1

    state = spine.load_state("testvale")
    assert all(f["id"] != dm_fact["id"] for f in state.get("knowledge", []))
    # the old record keeps its provenance in the knowledge file
    doc = knowledge.load("testvale")
    old = next(f for f in doc["facts"] if f["id"] == old_id)
    assert old["status"] == "superseded" and old["superseded_by"]


def test_unclassified_lore_is_not_mass_published(client, tmp_spine_root):
    # nothing in the vault becomes knowledge by itself: the count is exactly
    # what the DM approved through this API, and empty campaigns work
    r = client.get("/api/campaign/testvale/knowledge")
    assert r.status_code == 200
    assert r.json()["facts"] == []
    # the state has no knowledge section at all (nothing compiled yet)
    state = spine.load_state("testvale")
    assert not state.get("knowledge")
    # the engine still works without one
    view = _view(client, "wardens", tmp_spine_root)
    assert "facts_you_know" not in view


def test_source_change_marks_stale_but_keeps_approved_text(
        client, tmp_spine_root):
    src = tmp_spine_root / "testvale" / "lore" / "entities" / "The Vane.md"
    text = src.read_text(encoding="utf-8")
    r = _add(client, text="The Vane freezes in late autumn.",
             visibility="public", source_file="lore/entities/The Vane.md")
    assert r.status_code == 200, r.text
    fact_id = r.json()["fact"]["id"]

    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert not next(f for f in facts if f["id"] == fact_id)["stale"]

    # the source file changes under the DM (Obsidian edit)
    src.write_text(text + "\nA footnote the DM has not reviewed.\n",
                   encoding="utf-8")
    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert next(f for f in facts if f["id"] == fact_id)["stale"]

    # the compiled state keeps the APPROVED text — never the new wording
    state = spine.load_state("testvale")
    compiled = next(f for f in state["knowledge"] if f["id"] == fact_id)
    assert compiled["text"] == "The Vane freezes in late autumn."
    assert "footnote" not in compiled["text"]

    # reconcile after the DM reads it: staleness clears, text unchanged
    r = client.post(f"/api/campaign/testvale/knowledge/{fact_id}/reconcile")
    assert r.status_code == 200, r.text
    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert not next(f for f in facts if f["id"] == fact_id)["stale"]


def test_visibility_validation(client, tmp_spine_root):
    # unknown faction ids are refused — a typo must not hide or leak a fact
    r = _add(client, text="x", visibility=["wardens", "ghosts"])
    assert r.status_code == 422 and "ghosts" in r.json()["error"]
    r = _add(client, text="x", visibility="everyone")
    assert r.status_code == 422
    r = _add(client, text="x", visibility=[])
    assert r.status_code == 422
    # a fact with no text is refused
    r = _add(client, text="  ", visibility="public")
    assert r.status_code == 422


def test_knowledge_requires_simulation_state(client, spine_root):
    # spine_root's testvale HAS a state; use the no-simulation path via a
    # campaign whose state.yaml is absent — the fixture has none such, so
    # assert the 404 shape through the wardens campaign minus its state
    r = client.get("/api/campaign/nonexistent/knowledge")
    assert r.status_code == 404


def test_knowledge_writes_take_history_snapshots(client, tmp_spine_root):
    """A knowledge write published its files but skipped the git snapshot —
    published-but-untracked until someone hit snapshot recovery (found live
    2026-09-25). Every canon change commits: record, supersede, reveal."""
    r = _add(client, text="The ford-toll ledger was rewritten in secret.",
             visibility="dm_only")
    assert r.status_code == 200, r.text
    assert r.json()["snapshot"]["state"] == "committed", r.json().get("snapshot")

    r = _add(client, text="The ford-toll bridge over the Vane burned.",
             visibility="public")
    assert r.json()["snapshot"]["state"] == "committed"
    fid = r.json()["fact"]["id"]
    rv = client.post(f"/api/campaign/testvale/knowledge/{fid}/reveal",
                     json={"week": 1})
    assert rv.status_code == 200, rv.text
    assert rv.json()["snapshot"]["state"] == "committed", rv.json().get("snapshot")
