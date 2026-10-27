"""release/build_release.py's supply-chain pins (ticket 67): node pinned
to an exact release instead of the live index's latest v24, the four
cached downloads (python embed, get-pip, node, 7zr) verified against
recorded SHA256s before reuse and after every fetch, and the CI
workflow's actions resolving by commit SHA rather than a movable major
tag. The real builder downloads, extracts and boots; these tests pin
the constants and drive download() against a fake urllib.

Ticket 68 adds the builder behaviors the 2026-10-23 release review left
open: --sfx-only warns loudly when tracked templates are newer than the
cached payload (template edits were silently untested), a fully warm
cache carries the build to the smoke stage with the network stubbed out
(offline builds died on a fetch), and the share message names the
BEFORE-YOU-RUN.txt sidecar the build stages beside the exe.
"""

import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

PARENT = Path(__file__).resolve().parents[2]  # repo root (or worktree)

_spec = importlib.util.spec_from_file_location(
    "build_release", PARENT / "release" / "build_release.py")
build_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_release)


# ------------------------------------------------- the pins themselves

def test_node_is_pinned_to_an_exact_release():
    """The pin is a full node version, not a floating major: the old
    code took the live index's latest v24 on every build, so two exes
    with adjacent stamps could carry different runtimes with no trace."""
    assert re.fullmatch(r"v\d+\.\d+\.\d+", build_release.NODE_VERSION)
    assert build_release.NODE_VERSION.startswith("v24.")  # FMG wants >= 24
    src = Path(build_release.__file__).read_text(encoding="utf-8")
    assert "index.json" not in src  # the live index no longer answers


def test_checksum_table_covers_the_four_downloads():
    """Every pinned download carries a 64-hex sha256 and an https URL."""
    assert set(build_release.CHECKSUMS) == {
        f"python-{build_release.PY_VERSION}-embed.zip",
        "get-pip.py",
        f"node-{build_release.NODE_VERSION}-win-x64.zip",
        "7zr.exe",
    }
    for url, sha in build_release.CHECKSUMS.values():
        assert url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", sha), url


def test_companion_is_pinned_by_commit_sha():
    """The tag rides beside the commit for humans; the pin itself is the
    SHA (a tag is a movable pointer server-side — the old "tags are
    immutable" claim was wrong)."""
    assert re.fullmatch(r"[0-9a-f]{40}", build_release.COMPANION_REV)
    assert build_release.COMPANION_TAG == "v0.2.0"


# --------------------------------------------- download() verification

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        out, self._payload = self._payload, b""
        return out


def _fake_fetch(monkeypatch, payload):
    """Stand in for urlopen; returns the URLs it was asked for."""
    calls = []

    def fake(req, timeout=None):
        calls.append(getattr(req, "full_url", str(req)))
        return _FakeResponse(payload)

    monkeypatch.setattr(build_release.urllib.request, "urlopen", fake)
    return calls


def test_download_reuses_a_cache_copy_that_verifies(tmp_path, monkeypatch):
    dest = tmp_path / "artifact.zip"
    dest.write_bytes(b"good bytes")
    calls = _fake_fetch(monkeypatch, b"must-not-be-fetched")
    out = build_release.download(
        "https://example.test/a.zip", dest,
        hashlib.sha256(b"good bytes").hexdigest())
    assert out == dest and out.read_bytes() == b"good bytes"
    assert calls == []  # a cache that verifies never touches the network


def test_download_replaces_a_corrupted_cache_copy(tmp_path, monkeypatch):
    """The ticket-67 finding: a corrupted cached zip was reused forever.
    Now a sha256 mismatch on the cache deletes it and re-downloads —
    exactly once."""
    dest = tmp_path / "artifact.zip"
    dest.write_bytes(b"corrupted cache bytes")
    calls = _fake_fetch(monkeypatch, b"fresh good bytes")
    out = build_release.download(
        "https://example.test/a.zip", dest,
        hashlib.sha256(b"fresh good bytes").hexdigest())
    assert out.read_bytes() == b"fresh good bytes"
    assert len(calls) == 1
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_fails_when_the_refetch_still_mismatches(
        tmp_path, monkeypatch):
    """One re-download, then a hard stop: the pin is the provenance, so
    a persistently wrong artifact fails the build instead of shipping.
    No partial file is left behind for the next run to trust."""
    dest = tmp_path / "artifact.zip"
    dest.write_bytes(b"corrupted cache bytes")
    _fake_fetch(monkeypatch, b"still wrong")
    with pytest.raises(SystemExit):
        build_release.download(
            "https://example.test/a.zip", dest,
            hashlib.sha256(b"the real bytes").hexdigest())
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_verifies_fresh_bytes_too(tmp_path, monkeypatch):
    """A truncated/corrupted FETCH must not land in the cache either —
    otherwise the first build poisons the cache for every later one."""
    dest = tmp_path / "artifact.zip"
    _fake_fetch(monkeypatch, b"truncated")
    with pytest.raises(SystemExit):
        build_release.download(
            "https://example.test/a.zip", dest,
            hashlib.sha256(b"the real bytes").hexdigest())
    assert not dest.exists()


def test_download_without_a_pin_keeps_the_old_trust(tmp_path, monkeypatch):
    """The unpinned path (the LZMA SDK fallback chain — only the 7zSD.sfx
    extracted from it ships) keeps the legacy behavior: cache trusted,
    no verification."""
    dest = tmp_path / "legacy.bin"
    dest.write_bytes(b"anything")
    calls = _fake_fetch(monkeypatch, b"x")
    out = build_release.download("https://example.test/legacy.bin", dest)
    assert out == dest and calls == []


# ----------------------------- the CI workflow's actions resolve by SHA

def test_ci_actions_are_sha_pinned():
    """Ticket 67 AC1: every `uses:` in ci.yml resolves a full commit SHA
    (a major tag such as checkout@v4 is a movable reference); the
    human-readable release stays in the trailing comment."""
    text = (PARENT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    uses = [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith(("uses:", "- uses:"))]
    assert len(uses) == 3  # checkout, setup-python, setup-node
    for ln in uses:
        ref = ln.split("@", 1)[1].split()[0]  # up to the trailing comment
        assert re.fullmatch(r"[0-9a-f]{40}", ref), ln
        assert "# v" in ln, ln  # the tag recorded beside it for humans


# ------------------ ticket 68: --sfx-only staleness + warm-cache offline

def _write_templates(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name in build_release.TEMPLATE_NAMES:
        (directory / name).write_text(f"template {name}\n", encoding="ascii")
    # the sidecar is a template too (make_sfx copies it beside the exe) —
    # but not a payload one, so it stays out of TEMPLATE_NAMES
    (directory / "BEFORE-YOU-RUN.txt").write_text("read first\n",
                                                  encoding="ascii")
    return directory


def test_stale_templates_names_edits_newer_than_the_payload(tmp_path,
                                                            monkeypatch):
    """--sfx-only reuses the cached payload.7z, so a template edited after
    the pack is silently untested (ticket 68 finding 1): stale_templates()
    must name exactly the templates newer than the payload, and nothing
    when the cache is fresh."""
    templates = _write_templates(tmp_path / "templates")
    monkeypatch.setattr(build_release, "TEMPLATES", templates)
    payload = tmp_path / "payload.7z"
    payload.write_bytes(b"payload")
    past = payload.stat().st_mtime - 120
    for name in build_release.TEMPLATE_NAMES:
        os.utime(templates / name, (past, past))
    assert build_release.stale_templates(payload) == []

    future = payload.stat().st_mtime + 120
    os.utime(templates / "README.txt", (future, future))
    os.utime(templates / "install.cmd", (future, future))
    assert build_release.stale_templates(payload) == ["install.cmd",
                                                      "README.txt"]


def test_sfx_only_main_warns_loudly_and_the_share_message_names_the_sidecar(
        tmp_path, monkeypatch, capsys):
    """The loud path through main(): --sfx-only over a payload older than
    an edited template prints the WARNING banner naming the stale file,
    and the final share message names BEFORE-YOU-RUN.txt beside the exe —
    the sidecar is part of what the builder tells you to share."""
    templates = _write_templates(tmp_path / "templates")
    work = tmp_path / "work"
    work.mkdir()
    payload = work / "payload.7z"
    payload.write_bytes(b"payload")
    future = payload.stat().st_mtime + 120
    os.utime(templates / "launch.cmd", (future, future))
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "VERSION").write_text(
        "0.1.0+20261026000000+v0.2.0+atlas-0.7.1\n", encoding="ascii")
    sfx = tmp_path / "7zSD.sfx"
    sfx.write_bytes(b"sfx-module")
    dist = tmp_path / "dist"

    monkeypatch.setattr(build_release, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(build_release, "WORK", work)
    monkeypatch.setattr(build_release, "STAGE", stage)
    monkeypatch.setattr(build_release, "DIST", dist)
    monkeypatch.setattr(build_release, "TEMPLATES", templates)
    monkeypatch.setattr(build_release, "ensure_cache",
                        lambda: (tmp_path / "7zr.exe", sfx,
                                 tmp_path / "node.zip"))
    monkeypatch.setattr(sys, "argv",
                        ["build_release.py", "--sfx-only", "--no-smoke",
                         "--keep-work"])
    build_release.main()
    out = capsys.readouterr().out
    assert "WARNING" in out and "launch.cmd" in out, out
    assert "drop --sfx-only" in out, out
    assert "BEFORE-YOU-RUN.txt" in out, out  # the share message
    assert (dist / "BEFORE-YOU-RUN.txt").is_file()


def test_sfx_only_on_a_fresh_cache_prints_no_warning(tmp_path, monkeypatch,
                                                     capsys):
    """The quiet half of the contract: a payload newer than every template
    (the normal post-full-build state) runs --sfx-only warning-free."""
    templates = _write_templates(tmp_path / "templates")
    work = tmp_path / "work"
    work.mkdir()
    payload = work / "payload.7z"
    payload.write_bytes(b"payload")
    past = payload.stat().st_mtime - 120
    for name in build_release.TEMPLATE_NAMES:
        os.utime(templates / name, (past, past))
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "VERSION").write_text(
        "0.1.0+20261026000000+v0.2.0+atlas-0.7.1\n", encoding="ascii")
    sfx = tmp_path / "7zSD.sfx"
    sfx.write_bytes(b"sfx-module")

    monkeypatch.setattr(build_release, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(build_release, "WORK", work)
    monkeypatch.setattr(build_release, "STAGE", stage)
    monkeypatch.setattr(build_release, "DIST", tmp_path / "dist")
    monkeypatch.setattr(build_release, "TEMPLATES", templates)
    monkeypatch.setattr(build_release, "ensure_cache",
                        lambda: (tmp_path / "7zr.exe", sfx,
                                 tmp_path / "node.zip"))
    monkeypatch.setattr(sys, "argv",
                        ["build_release.py", "--sfx-only", "--no-smoke",
                         "--keep-work"])
    build_release.main()
    out = capsys.readouterr().out
    assert "WARNING" not in out, out


def _warm_cache(tmp_path, monkeypatch):
    """A cache holding every artifact ensure_cache() wants: the four
    pinned downloads whose bytes match the (test-local) CHECKSUMS table,
    the LZMA SDK package of the first fallback version, and an extracted
    7zSD.sfx that already carries its asInvoker manifest."""
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(build_release, "CACHE", cache)
    pins = {}
    for name in build_release.CHECKSUMS:
        data = f"warm bytes for {name}".encode()
        (cache / name).write_bytes(data)
        pins[name] = (f"https://example.test/{name}",
                      hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(build_release, "CHECKSUMS", pins)
    (cache / f"lzma{build_release.LZMA_SDK_VERSIONS[0]}.7z") \
        .write_bytes(b"sdk package")
    sfx = cache / "7zSD.sfx"
    sfx.write_bytes(b"7zSD stub, manifest carries asInvoker")
    return cache, sfx


def _forbid_network(monkeypatch):
    def forbidden(req, timeout=None):
        raise AssertionError(f"offline build attempted a fetch: {req}")
    monkeypatch.setattr(build_release.urllib.request, "urlopen", forbidden)


def test_warm_cache_reaches_the_smoke_stage_offline(tmp_path, monkeypatch,
                                                    capsys):
    """Ticket 68 AC: with the cache fully warm the build must reach the
    smoke stage with the network call stubbed out — the review's finding
    was an unconditional nodejs.org fetch; the ticket-67 pin made every
    fetch cache-first, and this drives main() end to end on the warm
    cache to pin it. Staging, the companion check, pack and the verify
    passes are faked away (they shell out to pip/git/7zr and run real
    servers); the seam under test is the cache phase — the build's only
    network stop before smoke."""
    cache, sfx = _warm_cache(tmp_path, monkeypatch)
    _forbid_network(monkeypatch)
    monkeypatch.setattr(build_release, "ensure_companion", lambda: None)

    stage = tmp_path / "stage"
    stage.mkdir()

    def fake_build_stage(node_zip):
        (stage / "VERSION").write_text(
            "0.1.0+warm-cache-offline+v0.2.0+atlas-0.7.1\n", encoding="ascii")

    monkeypatch.setattr(build_release, "STAGE", stage)
    monkeypatch.setattr(build_release, "build_stage", fake_build_stage)
    smoked = []
    monkeypatch.setattr(build_release, "smoke",
                        lambda port: smoked.append(port))

    def fake_pack(sevenzr):
        payload = tmp_path / "work" / "payload.7z"
        payload.write_bytes(b"payload")
        return payload

    monkeypatch.setattr(build_release, "WORK", tmp_path / "work")
    monkeypatch.setattr(build_release, "pack", fake_pack)
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "BEFORE-YOU-RUN.txt").write_text("read first\n",
                                                  encoding="ascii")
    monkeypatch.setattr(build_release, "TEMPLATES", templates)
    monkeypatch.setattr(build_release, "DIST", tmp_path / "dist")
    monkeypatch.setattr(build_release, "verify_sfx", lambda exe, port: None)
    monkeypatch.setattr(build_release, "verify_update",
                        lambda exe, sevenzr, port: None)
    # the payload split (ticket 51) packs with the real 7zr after the
    # verifies — faked away at the same seam as pack() (both shell out
    # to 7z), and asserted to have RUN so the offline build provably
    # reaches the whole payload emission, not just the smoke stage
    packed = []

    def fake_pack_payload(sevenzr, out_name, *args):
        out = build_release.DIST / out_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"payload " + out_name.encode("ascii"))
        packed.append(out_name)
        return out

    monkeypatch.setattr(build_release, "pack_payload", fake_pack_payload)
    monkeypatch.setattr(build_release, "verify_payload_sets",
                        lambda sevenzr, packs, app_only: None)
    monkeypatch.setattr(build_release, "verify_app_over_full",
                        lambda sevenzr, port, app: None)
    monkeypatch.setattr(sys, "argv", ["build_release.py", "--keep-work"])

    build_release.main()
    assert smoked == [build_release.PORT_STAGE], \
        "the warm-cache offline build must reach the stage smoke"
    # app + runtime always; the tools pack only when the stage carries
    # dnd-mapping/tools — this fake stage has none, so the build must
    # skip it LOUDLY (the warning the real build prints on a tools-less
    # machine), not fail or emit silently
    assert [n for n in packed if n.startswith("CampaignStudio-app-")], packed
    assert any(n.startswith("CampaignStudio-runtime-") for n in packed), packed
    out = capsys.readouterr().out
    assert "no dnd-mapping/tools" in out and "WARNING" in out, out


def test_warm_cache_ensure_cache_itself_needs_no_network(tmp_path,
                                                         monkeypatch):
    """The unit-level seam under the end-to-end test above: with the cache
    warm, ensure_cache() verifies and reuses every artifact — urlopen is
    never reached (an offline machine gets (7zr, sfx, node zip) back)."""
    cache, sfx = _warm_cache(tmp_path, monkeypatch)
    _forbid_network(monkeypatch)
    sevenzr, sfx_module, node_zip = build_release.ensure_cache()
    assert sevenzr == cache / "7zr.exe"
    assert sfx_module == sfx
    assert node_zip == cache / f"node-{build_release.NODE_VERSION}-win-x64.zip"
