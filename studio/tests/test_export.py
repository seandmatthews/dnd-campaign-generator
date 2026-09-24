"""Phase 7: export + tested restoration.

- exporting packs ONE campaign (never its siblings, never recovery
  payloads, never a dropped credential file) with a sha256 manifest;
- restore verifies every hash before writing, refuses unsafe or unknown
  members, refuses to overwrite an existing target, and opens the restored
  campaign with the app's own loaders;
- the manifest states plainly that git history is NOT included.
"""

import json
import zipfile

import export_campaign
import spine
from conftest import client  # noqa: F401


def _make_campaigns(tmp_spine_root, monkeypatch):
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_spine_root)
    return tmp_spine_root


def test_export_manifest_and_exclusions(client, tmp_spine_root, monkeypatch,
                                        tmp_path):
    root = _make_campaigns(tmp_spine_root, monkeypatch)
    # noise that must never ride along: recovery payloads + a dropped credential
    tx = root / "testvale" / ".studio-transactions" / "20260101T000000-abc"
    tx.mkdir(parents=True, exist_ok=True)
    (tx / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "testvale" / "llm.env").write_text("LLM_API_KEY=sekrit\n",
                                               encoding="utf-8")
    out = tmp_path / "exports" / "testvale.zip"
    rc = export_campaign.main(["testvale", "--out", str(out)])
    assert rc == 0

    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        names = z.namelist()
    assert manifest["campaign"] == "testvale"
    assert manifest["includes_git_history"] is False
    assert manifest["files"]  # the campaign's real files are in
    assert all("testvale/" not in n or n.startswith("campaign/")
               for n in names if n != "manifest.json")
    joined = "\n".join(names)
    assert ".studio-transactions" not in joined
    assert "llm.env" not in joined
    assert not any(n.endswith("/") for n in names)
    # every manifest hash matches the live file
    for rel, digest in manifest["files"].items():
        assert export_campaign.sha256(
            (root / "testvale" / rel).read_bytes()) == digest


def test_restore_to_new_directory_verifies_and_loads(
        client, tmp_spine_root, monkeypatch, tmp_path):
    root = _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])

    fresh = tmp_path / "restored"
    rc = export_campaign.main(["--restore", str(archive), "--into",
                               str(fresh)])
    assert rc == 0
    restored = fresh / "testvale"
    # byte-for-byte identical to the original campaign (minus exclusions)
    manifest = json.loads(zipfile.ZipFile(archive).read("manifest.json")
                          .decode("utf-8"))
    for rel in manifest["files"]:
        assert (restored / rel).read_bytes() == \
            (root / "testvale" / rel).read_bytes()
    # the app's own loaders open it (the script ran load_check; assert here
    # too through the same loaders)
    saved = spine.CAMPAIGNS_ROOT
    spine.CAMPAIGNS_ROOT = fresh
    try:
        assert spine.require_campaign("testvale")
        assert spine.load_state("testvale").get("city")
    finally:
        spine.CAMPAIGNS_ROOT = saved


def test_restore_refuses_existing_target(client, tmp_spine_root,
                                         monkeypatch, tmp_path, capsys):
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    fresh = tmp_path / "restored"
    fresh.mkdir()
    (fresh / "testvale").mkdir()
    try:
        export_campaign.main(["--restore", str(archive), "--into",
                              str(fresh)])
    except SystemExit as e:
        assert e.code == 1
        assert "never overwrites" in capsys.readouterr().err
    else:  # pragma: no cover
        raise AssertionError("restore overwrote an existing directory")


def test_restore_refuses_tampered_archive(client, tmp_spine_root,
                                          monkeypatch, tmp_path):
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    # tamper: rewrite one member's content, keeping the name
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as zin, \
            zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "campaign/campaign.yaml":
                data = data + b"# tampered\n"
            zout.writestr(item, data)
    fresh = tmp_path / "restored"
    try:
        export_campaign.main(["--restore", str(tampered), "--into",
                              str(fresh)])
    except SystemExit as e:
        assert "hash mismatch" in str(e)
    else:  # pragma: no cover
        raise AssertionError("a tampered archive was restored")
    assert not (fresh / "testvale").exists()  # nothing written on refusal


def test_restore_refuses_traversal_members(client, tmp_spine_root,
                                           monkeypatch, tmp_path):
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive) as zin, \
            zipfile.ZipFile(evil, "w") as zout:
        for item in zin.namelist():
            zout.writestr(item, zin.read(item))
        zout.writestr("campaign/../../escaped.txt", "pwned")
    fresh = tmp_path / "restored"
    try:
        export_campaign.main(["--restore", str(evil), "--into", str(fresh)])
    except SystemExit as e:
        assert "unsafe path" in str(e) or "not in the manifest" in str(e)
    else:  # pragma: no cover
        raise AssertionError("a traversal member was accepted")
    assert not (fresh / "escaped.txt").exists()
