"""release/build_release.py's supply-chain pins (ticket 67): node pinned
to an exact release instead of the live index's latest v24, the four
cached downloads (python embed, get-pip, node, 7zr) verified against
recorded SHA256s before reuse and after every fetch, and the CI
workflow's actions resolving by commit SHA rather than a movable major
tag. The real builder downloads, extracts and boots; these tests pin
the constants and drive download() against a fake urllib.
"""

import hashlib
import importlib.util
import re
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
