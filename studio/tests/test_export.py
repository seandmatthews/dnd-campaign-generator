"""Phase 7: export + tested restoration.

- exporting packs ONE campaign (never its siblings, never recovery
  payloads, never a dropped credential file) with a sha256 manifest;
- restore verifies every hash before writing, refuses unsafe or unknown
  members, refuses to overwrite an existing target, and opens the restored
  campaign with the app's own loaders;
- the manifest states plainly that git history is NOT included.
"""

import json
import pathlib
import unittest.mock as mock
import zipfile

import export_campaign
import spine


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
        client, tmp_spine_root, monkeypatch, tmp_path, capsys):
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
    # the exclusion list is shared, so a studio export restores WHOLE:
    # nothing legitimately in the archive is dropped, nothing extra lands
    out, _ = capsys.readouterr()
    assert "skipped" not in out
    assert f"restored {len(manifest['files'])} file(s)" in out
    on_disk = {p.relative_to(restored).as_posix()
               for p in restored.rglob("*") if p.is_file()}
    assert on_disk == set(manifest["files"])
    # the app's own loaders open it (the script ran load_check; assert here
    # too through the same loaders)
    saved = spine.CAMPAIGNS_ROOT
    spine.CAMPAIGNS_ROOT = fresh
    try:
        assert spine.require_campaign("testvale")
        assert spine.load_state("testvale").get("city")
    finally:
        spine.CAMPAIGNS_ROOT = saved


def test_restore_skips_excluded_members(client, tmp_spine_root, monkeypatch,
                                        tmp_path, capsys):
    """A hand-zipped live campaign (dir entries and all, every planted file
    hash-verified in the manifest — hashes do not discriminate) must not
    resurrect what the export promises never ride along: a nested .git, a
    credential at any depth, a stale ACTIVE forecast (review P3-25)."""
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    planted = {
        ".git/HEAD": "ref: refs/heads/main\n",
        ".git/hooks/post-commit": "#!/bin/sh\n",
        "llm.env": "LLM_API_KEY=sekrit\n",
        "sessions/llm.env": "LLM_API_KEY=also-sekrit\n",
        "simulation/forecasts/20260925T120000-ab12cd34/manifest.yaml":
            "status: ACTIVE\n",
    }
    hand = tmp_path / "hand.zip"
    with zipfile.ZipFile(archive) as zin, \
            zipfile.ZipFile(hand, "w") as zout:
        manifest = json.loads(zin.read("manifest.json").decode("utf-8"))
        for item in zin.namelist():
            if item != "manifest.json":
                zout.writestr(item, zin.read(item))
        # an Explorer-style zip carries directory entries too
        for d in ("", ".git", ".git/hooks", "sessions",
                  "simulation/forecasts/20260925T120000-ab12cd34"):
            zout.writestr("campaign/" + d + "/", "")
        for rel, text in planted.items():
            manifest["files"][rel] = export_campaign.sha256(
                text.encode("utf-8"))
            zout.writestr("campaign/" + rel, text.encode("utf-8"))
        zout.writestr("manifest.json", json.dumps(manifest))
    fresh = tmp_path / "restored"
    rc = export_campaign.main(["--restore", str(hand), "--into", str(fresh)])
    assert rc == 0
    restored = fresh / "testvale"
    for rel in planted:
        assert not (restored / rel).exists(), f"{rel} landed on disk"
    assert not (restored / "simulation" / "forecasts").exists()
    assert not (restored / "sessions").exists()
    # the response names every skipped member
    out, _ = capsys.readouterr()
    assert "skipped excluded member" in out
    for rel in planted:
        assert rel in out, f"the skip warning must name {rel}"
    # and the campaign's real files all landed — it loaded (rc 0)
    assert (restored / "campaign.yaml").is_file()


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


def _rebuild(archive, tmp_path, name="evil.zip", mutate=None):
    """Copy an archive, optionally rewriting the manifest dict."""
    evil = tmp_path / name
    with zipfile.ZipFile(archive) as zin, \
            zipfile.ZipFile(evil, "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "manifest.json" and mutate is not None:
                manifest = json.loads(data.decode("utf-8"))
                mutate(manifest)
                data = json.dumps(manifest).encode("utf-8")
            zout.writestr(item, data)
    return evil


def test_restore_refuses_drive_qualified_members(client, tmp_spine_root,
                                                 monkeypatch, tmp_path):
    """Windows-specific escape: `Path(x) / "C:/evil"` IS C:/evil, so a
    drive-colon member would write outside --into (found by review)."""
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    evil = tmp_path / "drive.zip"
    with zipfile.ZipFile(archive) as zin, \
            zipfile.ZipFile(evil, "w") as zout:
        for item in zin.namelist():
            zout.writestr(item, zin.read(item))
        zout.writestr("campaign/C:/evil.txt", "pwned")
        zout.writestr("campaign/C:evil2.txt", "pwned")
    fresh = tmp_path / "restored"
    try:
        export_campaign.main(["--restore", str(evil), "--into", str(fresh)])
    except SystemExit as e:
        assert "unsafe path" in str(e)
    else:  # pragma: no cover
        raise AssertionError("a drive-qualified member was accepted")
    assert not (fresh / "evil.txt").exists()
    assert not pathlib.Path("C:/evil.txt").exists()
    assert not pathlib.Path("C:evil2.txt").exists()


def test_restore_refuses_a_manifest_campaign_that_escapes(
        client, tmp_spine_root, monkeypatch, tmp_path, capsys):
    """The manifest is archive-controlled data like any other: a campaign
    name of "../elsewhere" would place the target outside --into."""
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    evil = _rebuild(archive, tmp_path, mutate=lambda m: m.update(
        {"campaign": "../elsewhere"}))
    fresh = tmp_path / "restored"
    try:
        export_campaign.main(["--restore", str(evil), "--into", str(fresh)])
    except SystemExit:
        assert "unusable campaign" in capsys.readouterr().err
    else:  # pragma: no cover
        raise AssertionError("an escaping manifest campaign was accepted")
    assert not (tmp_path / "elsewhere").exists()


def test_restore_refuses_a_manifest_campaign_no_loader_could_address(
        client, tmp_spine_root, monkeypatch, tmp_path, capsys):
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    evil = _rebuild(archive, tmp_path, mutate=lambda m: m.update(
        {"campaign": "bad name with spaces"}))
    try:
        export_campaign.main(["--restore", str(evil), "--into",
                              str(tmp_path / "restored")])
    except SystemExit:
        assert "unusable campaign" in capsys.readouterr().err
    else:  # pragma: no cover
        raise AssertionError("an unusable campaign name was accepted")


def test_restore_refuses_an_archive_with_no_manifest(
        client, tmp_spine_root, monkeypatch, tmp_path, capsys):
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    bare = tmp_path / "bare.zip"
    with zipfile.ZipFile(archive) as zin, \
            zipfile.ZipFile(bare, "w") as zout:
        for item in zin.namelist():
            if item != "manifest.json":
                zout.writestr(item, zin.read(item))
    try:
        export_campaign.main(["--restore", str(bare), "--into",
                              str(tmp_path / "restored")])
    except SystemExit:
        assert "no manifest.json" in capsys.readouterr().err
    else:  # pragma: no cover
        raise AssertionError("a manifest-less archive was restored")


def test_restore_refuses_an_unparseable_manifest(client, tmp_spine_root,
                                                 monkeypatch, tmp_path,
                                                 capsys):
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive) as zin, \
            zipfile.ZipFile(broken, "w") as zout:
        for item in zin.namelist():
            data = b"{not json at all" if item == "manifest.json" \
                else zin.read(item)
            zout.writestr(item, data)
    try:
        export_campaign.main(["--restore", str(broken), "--into",
                              str(tmp_path / "restored")])
    except SystemExit:
        assert "does not parse" in capsys.readouterr().err
    else:  # pragma: no cover
        raise AssertionError("an unparseable manifest was accepted")


def test_restore_refuses_an_archive_missing_a_manifested_file(
        client, tmp_spine_root, monkeypatch, tmp_path):
    _make_campaigns(tmp_spine_root, monkeypatch)
    archive = tmp_path / "testvale.zip"
    export_campaign.main(["testvale", "--out", str(archive)])
    short = tmp_path / "short.zip"
    with zipfile.ZipFile(archive) as zin:
        manifest = json.loads(zin.read("manifest.json").decode("utf-8"))
        victim = "campaign/" + sorted(manifest["files"])[0]
        with zipfile.ZipFile(short, "w") as zout:
            for item in zin.namelist():
                if item != victim:
                    zout.writestr(item, zin.read(item))
    fresh = tmp_path / "restored"
    try:
        export_campaign.main(["--restore", str(short), "--into", str(fresh)])
    except SystemExit as e:
        assert "missing manifest file" in str(e)
    else:  # pragma: no cover
        raise AssertionError("an incomplete archive was restored")
    assert not (fresh / "testvale").exists(), "nothing written on refusal"


def test_export_excludes_every_documented_noise_pattern(client,
                                                        tmp_spine_root,
                                                        monkeypatch):
    """excluded() is the whole privacy story — pin every pattern."""
    _make_campaigns(tmp_spine_root, monkeypatch)
    for rel in ("x.log", "x.bak", "x.tmp", "x.pyc", "sub/__pycache__/m.pyc",
                ".git/HEAD", ".gitignore", "llm.env", "llm.env.example",
                "sessions/llm.env", "lore/deep/llm.env.local",
                "simulation/forecasts/20260925T120000-ab12cd34/manifest.yaml",
                "simulation/forecasts/20260925T120000-ab12cd34/week-0004.yaml"):
        assert export_campaign.excluded(rel), f"{rel} must be excluded"
    for rel in ("campaign.yaml", "simulation/state.yaml", "notes.md",
                "sessions/session-0001.md",
                "simulation/proposals/p1/proposal.yaml"):
        assert not export_campaign.excluded(rel), f"{rel} must ride along"


# ---------------- consistency rules (2026-10-02 bug pass)


def test_export_archives_the_bytes_it_hashed(client, tmp_spine_root,
                                             monkeypatch, tmp_path):
    """The manifest hash and the zip member must come from ONE read: an
    edit landing between the hash pass and the zip pass used to leave an
    export that reported success but failed every restore (hash mismatch
    on the edited file)."""
    root = _make_campaigns(tmp_spine_root, monkeypatch)
    victim = root / "testvale" / "lore" / "timeline.yaml"
    before = victim.read_bytes()
    real_writestr = zipfile.ZipFile.writestr
    fired = {"yes": False}

    def editing_writestr(self, name, data, *a, **k):
        # land a concurrent edit just before the first member is written
        if not fired["yes"] and name != "manifest.json":
            fired["yes"] = True
            victim.write_bytes(before + b"# concurrent edit\n")
        return real_writestr(self, name, data, *a, **k)

    with mock.patch.object(zipfile.ZipFile, "writestr", editing_writestr):
        out = export_campaign.export("testvale", tmp_path / "race.zip")
    assert fired["yes"], "the race window was never exercised"

    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        for rel, digest in manifest["files"].items():
            assert export_campaign.sha256(z.read("campaign/" + rel)) == \
                digest, f"{rel}: archived bytes differ from the manifest"
    # the archive restores whole even though the live file moved on
    fresh = tmp_path / "restored"
    assert export_campaign.main(["--restore", str(out), "--into",
                                 str(fresh)]) == 0


def test_export_refuses_to_race_a_locked_campaign(client, tmp_spine_root,
                                                  monkeypatch, tmp_path,
                                                  capsys):
    """Export runs under the campaign lock: while another writer holds it,
    export fails honestly instead of walking a tree that is changing under
    it; with the lock free, the same export succeeds."""
    _make_campaigns(tmp_spine_root, monkeypatch)
    with mock.patch.object(export_campaign, "EXPORT_LOCK_WAIT_S", 0.2), \
            spine.campaign_lock("testvale"):
        try:
            export_campaign.export("testvale", tmp_path / "busy.zip")
        except SystemExit as e:
            assert e.code == 1
        else:  # pragma: no cover
            raise AssertionError("export ran while the campaign was locked")
    assert "locked by another writer" in capsys.readouterr().err
    out = export_campaign.export("testvale", tmp_path / "busy.zip")
    assert out == tmp_path / "busy.zip" and out.is_file()


def test_export_refuses_a_destination_inside_the_campaign(
        client, tmp_spine_root, monkeypatch, capsys):
    """Writing the zip into the campaign being zipped truncates a file the
    walk already hashed and archives the zip's own half-written bytes — a
    self-swallowing export that can never restore. Refused outright."""
    root = _make_campaigns(tmp_spine_root, monkeypatch)
    camp = root / "testvale"
    for bad in (camp / "backup.zip", camp / "lore" / "nested" / "backup.zip",
                camp / "lore"):
        try:
            export_campaign.export("testvale", bad)
        except SystemExit as e:
            assert e.code == 2, bad
        else:  # pragma: no cover
            raise AssertionError(f"export accepted a destination inside the "
                                 f"campaign: {bad}")
    assert "inside the campaign directory" in capsys.readouterr().err
    assert not (camp / "backup.zip").exists()


def test_export_with_no_arguments_lists_the_campaigns(client, tmp_spine_root,
                                                      monkeypatch, capsys):
    """The friend path (ticket 58): export.cmd is double-clicked with no
    arguments — the answer is the names they can use (and where the
    campaigns live), not bare argparse usage."""
    root = _make_campaigns(tmp_spine_root, monkeypatch)
    rc = export_campaign.main([])
    assert rc == 2  # still a usage exit — nothing was exported
    out = capsys.readouterr().out
    assert "testvale" in out, "the fixture campaign is named"
    assert "export one with:  export.cmd <name>" in out
    assert str(root) in out, "the campaigns' location is said"
