"""The `system:` field in campaign.yaml (ticket 48): one optional freeform
string, lowercased on read, no catalog — the core does not know systems.
The read helper is the sanctioned accessor; build_campaign_yaml passes a
hand-set value through a rebuild."""

import yaml

import assemble
import spine


def _write_campaign_yaml(camp, doc):
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "campaign.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_campaign_system_normalizes_case_and_space():
    assert spine.campaign_system({"system": "ICRPG"}) == "icrpg"
    assert spine.campaign_system({"system": "  ICRPG  "}) == "icrpg"
    # freeform: a typo is still a string — findings, never verdicts
    assert spine.campaign_system({"system": "icrpggg"}) == "icrpggg"


def test_campaign_system_none_for_missing_empty_or_nonstring():
    assert spine.campaign_system({}) is None
    assert spine.campaign_system({"system": ""}) is None
    assert spine.campaign_system({"system": "   "}) is None
    assert spine.campaign_system({"system": 5}) is None, \
        "YAML parses `system: 5` as an int — not a plain string"
    assert spine.campaign_system({"system": ["icrpg"]}) is None
    assert spine.campaign_system(None) is None
    assert spine.campaign_system("icrpg") is None


def test_load_campaign_yaml_reads_system(tmp_path, monkeypatch):
    root = tmp_path / "campaigns"
    camp = root / "ember-coast"
    _write_campaign_yaml(camp, {"name": "ember-coast", "title": "Ember",
                                "system": "ICRPG"})
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    doc = spine.load_campaign_yaml("ember-coast")
    assert spine.campaign_system(doc) == "icrpg"


def test_build_campaign_yaml_preserves_a_hand_set_system(tmp_path):
    camp = tmp_path / "ruravaqan"
    _write_campaign_yaml(camp, {"name": "ruravaqan", "system": "ICRPG",
                                "title": "Ruravaqan"})
    row = assemble.build_campaign_yaml(
        camp, {"meta": {"premise": "The toll bridge burned."}})
    assert row[0] == "campaign.yaml"
    rebuilt = yaml.safe_load(
        (camp / "campaign.yaml").read_text(encoding="utf-8"))
    assert rebuilt["system"] == "ICRPG", \
        "an idempotent rebuild must not revert the edge-authored field"
    assert rebuilt["premise"] == "The toll bridge burned."


def test_build_campaign_yaml_writes_no_system_when_none_existed(tmp_path):
    camp = tmp_path / "ruravaqan"
    row = assemble.build_campaign_yaml(
        camp, {"meta": {"premise": "The toll bridge burned."}})
    assert row[0] == "campaign.yaml"
    rebuilt = yaml.safe_load(
        (camp / "campaign.yaml").read_text(encoding="utf-8"))
    assert "system" not in rebuilt


def test_build_campaign_yaml_drops_only_a_corrupt_prior_file(tmp_path):
    """An unparseable campaign.yaml carries nothing to preserve — the
    rebuild writes its own (the same honest behavior as before 48)."""
    camp = tmp_path / "ruravaqan"
    camp.mkdir(parents=True)
    (camp / "campaign.yaml").write_text("{not yaml:", encoding="utf-8")
    assemble.build_campaign_yaml(camp, {"meta": {"premise": "p"}})
    rebuilt = yaml.safe_load(
        (camp / "campaign.yaml").read_text(encoding="utf-8"))
    assert "system" not in rebuilt and rebuilt["premise"] == "p"
