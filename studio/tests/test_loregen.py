"""M2 loregen: fed-or-refused, pending staging, entry validation, findings.

The LLM client is monkeypatched throughout — no test ever hits the network.
verification/check.py DOES run for real (structural layer, no deps, no
network): it is the seam this milestone must exercise, per D1's CLI
equivalence rule.
"""

import pytest
import yaml

import llm_client
import loregen
import spine
from conftest import wait_for_job

CAMP = "testvale"

VALID_ENTRIES = """```yaml
- when: before the toll
  title: Alweric's Bridge
  text: Alweric Tealeaf kept the only bridge across the Vane, and counted every unpaid cart twice.
  new_names: []
- when: before the toll
  title: Provis Warehouse
  text: Besiana Crow moved the Provis warehouse ledgers to higher ground after the spring floods.
  new_names: [Provis Warehouse]
```"""

CANON_JSON = ('```json\n[{"rule": "canon-audit", '
              '"quote": "counted every unpaid cart twice", '
              '"note": "the STATE does not back the counting habit", '
              '"severity": "warning"}]\n```')


def _canned_llm(monkeypatch, entries_yaml="", canon_json=""):
    """Patch llm_client.complete: the entries prompt and the canon-audit
    prompt (which ends with 'Return the JSON ONLY.') get different answers.
    Returns the list of prompts received."""
    calls = []

    def fake(prompt, **kwargs):
        calls.append(prompt)
        if prompt.endswith("Return the JSON ONLY."):
            return canon_json
        return entries_yaml

    monkeypatch.setattr(llm_client, "complete", fake)
    return calls


# 1. Fed-or-refused (guardrail 4) — no LLM call may happen ---------------


def test_entries_refused_without_geography(tmp_spine_root, monkeypatch):
    calls = _canned_llm(monkeypatch)
    (tmp_spine_root / CAMP / "world" / "geography.yaml").unlink()
    with pytest.raises(loregen.FedOrRefused):
        loregen.generate_entries(tmp_spine_root / CAMP, "the Vane")
    assert calls == []  # refused before any model call


def test_entries_route_422_without_geography(client, tmp_spine_root):
    (tmp_spine_root / CAMP / "world" / "geography.yaml").unlink()
    r = client.post(f"/api/campaign/{CAMP}/lore/entries",
                    json={"focus": "the Vane"})
    assert r.status_code == 422
    assert "error" in r.json()


def test_names_refused_without_seed_list(tmp_spine_root, monkeypatch):
    calls = _canned_llm(monkeypatch)
    with pytest.raises(loregen.FedOrRefused):
        loregen.name_candidates(tmp_spine_root / CAMP, "nonsuch", 5)
    assert calls == []


def test_names_route_422_without_seed_list(client, tmp_spine_root):
    r = client.post(f"/api/campaign/{CAMP}/lore/name",
                    json={"culture": "Nonsuch", "count": 5})
    assert r.status_code == 422
    assert "seed list" in r.json()["error"]


# 2. The entries job: staging shape, lowest free N, findings, untouched spine


def test_entries_job_stages_pending_and_attaches_findings(
        client, tmp_spine_root, monkeypatch):
    calls = _canned_llm(monkeypatch, VALID_ENTRIES, CANON_JSON)
    camp = tmp_spine_root / CAMP
    timeline_before = (camp / "lore" / "timeline.yaml").read_text("utf-8")

    r = client.post(f"/api/campaign/{CAMP}/lore/entries",
                    json={"focus": "the toll bridge"})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"], timeout=60)
    assert job["status"] == "done", job["error"]
    assert job["kind"] == "lore-entries"

    result = job["result"]
    assert result["pending"] == "lore/pending-entries-1.yaml"
    assert result["entries"] == 2

    # the pending file: {entries, created, focus}; ids continue t0003...
    doc = yaml.safe_load((camp / "lore" / "pending-entries-1.yaml")
                         .read_text("utf-8"))
    assert doc["focus"] == "the toll bridge"
    assert doc["created"]  # ISO-8601
    assert [e["id"] for e in doc["entries"]] == ["t0003", "t0004"]
    assert all(e["source"] == "authored" for e in doc["entries"])
    assert doc["entries"][0]["title"] == "Alweric's Bridge"

    # exactly two LLM calls: entries, then canon-audit
    assert len(calls) == 2
    assert "Return the JSON ONLY." in calls[1]

    # canon-audit finding attached, normalized to the findings shape
    canon = [f for f in result["findings"]
             if f["rule"] == "prompts/canon-audit"]
    assert len(canon) == 1
    assert canon[0]["level"] == "warning"
    assert canon[0]["quote"].startswith("counted every unpaid cart")

    # check.py really ran as a subprocess and reported into the job log
    assert any("check.py" in line for line in job["log_tail"])

    # timeline.yaml untouched — approval (M4) does that, not generation
    assert (camp / "lore" / "timeline.yaml").read_text("utf-8") == timeline_before
    assert spine.count_pending(CAMP) == 1

    # a second run stages to the lowest free integer
    r2 = client.post(f"/api/campaign/{CAMP}/lore/entries", json={})
    assert r2.status_code == 202
    job2 = wait_for_job(client, r2.json()["job_id"], timeout=60)
    assert job2["status"] == "done", job2["error"]
    assert job2["result"]["pending"] == "lore/pending-entries-2.yaml"


# 3. Entry validation drops hallucinated places ---------------------------


def test_hallucinated_entry_dropped_with_finding(tmp_spine_root, monkeypatch):
    _canned_llm(monkeypatch, entries_yaml="""- when: before the toll
  title: The Silver Bazaar of Qeth
  text: The Silver Bazaar of Qeth burned twice, and the Guild of Silvermongers rebuilt it in salt.
  new_names: []
- when: before the toll
  title: Alweric's Bridge
  text: Alweric Tealeaf kept the only bridge across the Vane, and counted every unpaid cart twice.
  new_names: []
""")
    result = loregen.generate_entries(tmp_spine_root / CAMP, "rope")
    assert result["entries"] == 1
    bad = [f for f in result["findings"]
           if f["rule"] == "loregen/hallucinated-name"]
    assert len(bad) == 1
    assert "Qeth" in bad[0]["note"]
    doc = yaml.safe_load(
        (tmp_spine_root / CAMP / "lore" / "pending-entries-1.yaml")
        .read_text("utf-8"))
    assert [e["title"] for e in doc["entries"]] == ["Alweric's Bridge"]


# 4. check.py subprocess findings attach (real structural layer) ----------


def test_structural_findings_fire_on_awful_entry(tmp_spine_root, monkeypatch):
    # one 47-word sentence — over the 35-word structural cap — built only
    # from names the fixture spine knows, so only the rhythm check fires
    awful = ("Provis counted every cart that crossed the Vane twice, and "
             "Dunmore counted the carts that did not cross at all, and the "
             "two towns kept rival ledgers of the same road for a whole "
             "generation, each certain in its bones that the other one "
             "could not add.")
    _canned_llm(monkeypatch, entries_yaml=(
        "- when: before the toll\n"
        "  title: Ledgers\n"
        f"  text: {awful}\n"
        "  new_names: []\n"), canon_json="[]")
    result = loregen.generate_entries(tmp_spine_root / CAMP, "tolls")
    assert result["entries"] == 1
    rules = {f["rule"] for f in result["findings"]}
    assert "structural/long-sentence" in rules


# 5. The name helper ------------------------------------------------------


def test_name_candidates_parse(tmp_spine_root, monkeypatch):
    calls = []

    def fake(prompt, **kwargs):
        calls.append(prompt)
        if "naming assistant" in prompt:
            # a case-duplicate and an exact seed among the goods
            return ("Corvinth\nElswyra\nCorvin\n- Besmara\n"
                    "2. Draconis\nElswyth\n")
        return ""

    monkeypatch.setattr(llm_client, "complete", fake)
    names = loregen.name_candidates(tmp_spine_root / CAMP, "shadian", 8,
                                    hint="river trades")
    # seeds and case-duplicates dropped, bullets/numbers stripped, order kept
    assert names == ["Corvinth", "Elswyra", "Besmara", "Draconis"]
    assert len(calls) == 1
    assert "Alweric" in calls[0]            # the seed list is in the prompt
    assert "DM HINT: river trades" in calls[0]
    assert "Exactly 8 names" in calls[0]


def test_name_candidates_cap_at_twelve(tmp_spine_root, monkeypatch):
    def fake(prompt, **kwargs):
        if "naming assistant" in prompt:
            return "\n".join(f"Name{i}" for i in range(20))
        return ""

    monkeypatch.setattr(llm_client, "complete", fake)
    names = loregen.name_candidates(tmp_spine_root / CAMP, "shadian", 50)
    assert len(names) == 12


def test_name_endpoint_sync(client, tmp_spine_root, monkeypatch):
    def fake(prompt, **kwargs):
        if "naming assistant" in prompt:
            return ("Corvinth\nElswyra\nBesmara\nDraconis\n"
                    "Garrow\nLuthane\nOmbrey\nFenwick\n")
        return ""

    monkeypatch.setattr(llm_client, "complete", fake)
    r = client.post(f"/api/campaign/{CAMP}/lore/name",
                    json={"culture": "shadian", "count": 8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body) == ["names"]
    assert len(body["names"]) == 8
    assert "Corvinth" in body["names"]
