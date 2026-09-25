#!/usr/bin/env python3
"""export_campaign.py — local export + tested restore for one campaign.

EXPORT
  python studio/export_campaign.py <campaign> [--out PATH]

Packs ONE campaign directory into a zip with a manifest of every included
file's sha256. Excludes machine noise and recovery payloads
(.studio-transactions, __pycache__, *.tmp/*.bak/*.log, .git*, llm.env*) —
a credential dropped inside the campaign never rides along, and no other
campaign is ever included.

What the export does and does not guarantee (stated plainly):
- The shared undo history lives at campaigns/.git — NOT inside the campaign
  folder. Exporting one folder does NOT include git history; the zip is a
  snapshot of the files as they stand.
- A same-disk archive is an EXPORT, not protection from disk loss. Copy the
  zip to a separate destination yourself; this tool does not upload
  anything anywhere and does not claim a copy happened.

RESTORE
  python studio/export_campaign.py --restore <zip> --into PARENT_DIR

Restores INTO PARENT_DIR/<campaign> and refuses to touch an existing
directory — restore never overwrites a real campaign by default. Every
member path is validated (no traversal, no absolute paths), every file is
verified against the manifest's hash before anything is written, unknown
members are refused, and the restored campaign is then opened with the
app's own loaders (campaign.yaml, state.yaml, chronicles, entity pages) so
a restore that cannot be loaded fails loudly.

Exit codes: 0 ok, 1 error, 2 usage.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import pathlib
import sys
import zipfile

import yaml

STUDIO_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(STUDIO_DIR))

MANIFEST = "manifest.json"
CAMPAIGN_PREFIX = "campaign/"
EXCLUDES = [
    ".studio-transactions/*",  # publication recovery payloads, not canon
    "__pycache__/*",
    "*.pyc",
    "*.tmp",
    "*.bak",
    "*.log",
    ".git/*",
    ".gitignore",
    "llm.env*",
]
SCHEMA_VERSION = 1


def excluded(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDES)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export(campaign: str, out: pathlib.Path) -> pathlib.Path:
    """Zip one campaign + manifest. Returns the archive path."""
    import spine
    root = spine.campaigns_root()
    camp_dir = root / campaign
    if not (camp_dir / "campaign.yaml").is_file():
        print(f"error: no such campaign: {campaign} (under {root})",
              file=sys.stderr)
        raise SystemExit(1)
    files = {}
    for p in sorted(camp_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(camp_dir).as_posix()
        if excluded(rel):
            continue
        files[rel] = sha256(p.read_bytes())
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "campaign": campaign,
        "created": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "includes_git_history": False,
        "note": "Snapshot of the campaign FILES only. The shared undo "
                "history lives in campaigns/.git and is NOT included. A "
                "same-disk archive is an export, not a backup — copy it "
                "somewhere else yourself.",
        "excluded_patterns": EXCLUDES,
        "files": files,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, indent=2,
                                        ensure_ascii=False))
        for rel in sorted(files):
            z.write(camp_dir / rel, CAMPAIGN_PREFIX + rel)
    print(f"exported {len(files)} file(s) -> {out}")
    print("note: git history (campaigns/.git) is NOT included; a same-disk "
          "archive is an export, not a backup — copy it elsewhere yourself.")
    return out


def _load_manifest(z: zipfile.ZipFile) -> dict:
    try:
        doc = json.loads(z.read(MANIFEST).decode("utf-8"))
    except KeyError:
        print("error: not an export archive (no manifest.json)", file=sys.stderr)
        raise SystemExit(1)
    except ValueError as e:
        print(f"error: manifest does not parse: {e}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
        print("error: manifest has no file list", file=sys.stderr)
        raise SystemExit(1)
    return doc


def _safe_member(name: str) -> str | None:
    """The campaign-relative path for a zip member, or None for non-campaign
    members (the manifest). Any traversal or absolute path is a refusal —
    including a drive-qualified one: on Windows `Path(x) / "C:/evil"` IS
    `C:/evil`, so a `:` anywhere in a relative path is refused outright."""
    if name == MANIFEST:
        return None
    if not name.startswith(CAMPAIGN_PREFIX):
        raise SystemExit(f"error: unexpected member in archive: {name}")
    rel = name[len(CAMPAIGN_PREFIX):]
    if (not rel or rel.startswith("/") or ".." in rel.split("/")
            or "\\" in rel or ":" in rel
            or any(ord(ch) < 32 for ch in rel)):
        raise SystemExit(f"error: unsafe path in archive: {name}")
    return rel


def restore(archive: pathlib.Path, into: pathlib.Path) -> pathlib.Path:
    """Restore the archive into into/<campaign>. Verifies every file's hash
    BEFORE writing; refuses unknown members; refuses an existing target;
    then opens the restored campaign with the app's loaders."""
    if not archive.is_file():
        print(f"error: no such archive: {archive}", file=sys.stderr)
        raise SystemExit(1)
    with zipfile.ZipFile(archive) as z:
        manifest = _load_manifest(z)
        campaign = str(manifest.get("campaign") or "")
        # the manifest is data from the archive like any other: a campaign
        # name that would place the target outside --into ("../x") or that
        # no loader could ever address is a refusal, not a destination
        import spine
        if not spine.valid_name(campaign):
            print(f"error: manifest names an unusable campaign: {campaign!r}",
                  file=sys.stderr)
            raise SystemExit(1)
        target = into / campaign
        if target.exists():
            print(f"error: {target} already exists — restore never "
                  "overwrites; choose another --into directory",
                  file=sys.stderr)
            raise SystemExit(1)
        # hash-verify everything IN MEMORY before writing anything
        verified: dict = {}
        for member in z.namelist():
            rel = _safe_member(member)
            if rel is None:
                continue
            expected = manifest["files"].get(rel)
            if expected is None:
                raise SystemExit(
                    f"error: {rel} is in the archive but not in the "
                    "manifest — refusing an unverified member")
            actual = sha256(z.read(member))
            if actual != expected:
                raise SystemExit(
                    f"error: hash mismatch for {rel} — the archive is "
                    "corrupt or tampered with")
            verified[rel] = z.read(member)
        missing = set(manifest["files"]) - set(verified)
        if missing:
            raise SystemExit(
                "error: the archive is missing manifest file(s): "
                + ", ".join(sorted(missing)))
        into.mkdir(parents=True, exist_ok=True)
        for rel, data in sorted(verified.items()):
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        # re-verify what landed on disk matches the manifest byte for byte
        for rel, expected in manifest["files"].items():
            actual = sha256((target / rel).read_bytes())
            if actual != expected:
                raise SystemExit(
                    f"error: post-write verification failed for {rel}")
    print(f"restored {len(verified)} file(s) -> {target}")
    print("note: this restore carries NO git history (see the manifest) — "
          "the restored campaign's undo trail starts fresh.")
    load_check(target.parent, campaign)
    return target


def load_check(parent: pathlib.Path, campaign: str) -> None:
    """Open the restored campaign with the app's own loaders — a restore
    that cannot be loaded fails loudly, here, not at the table."""
    import spine
    saved = spine.CAMPAIGNS_ROOT
    spine.CAMPAIGNS_ROOT = parent
    try:
        spine.require_campaign(campaign)
        meta = spine.load_campaign_yaml(campaign)
        state = spine.load_state(campaign)
        pages = spine.entity_pages(campaign)
        chron = spine.chronicles(campaign)
    except spine.CampaignError as e:
        print(f"error: the restored campaign does not load: {e}",
              file=sys.stderr)
        raise SystemExit(1)
    finally:
        spine.CAMPAIGNS_ROOT = saved
    print(f"load check OK: {meta.get('title') or campaign} — "
          f"{len(state.get('factions', []))} faction(s), "
          f"{len(pages)} entity page(s), {len(chron)} chronicle(s)")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Export one campaign (with a hash manifest) or restore "
                    "an export into a new directory.")
    ap.add_argument("campaign", nargs="?", help="campaign name to export")
    ap.add_argument("--out", help="archive path (default: <root>/<campaign>"
                                  "-export-<date>.zip)")
    ap.add_argument("--restore", metavar="ZIP",
                    help="restore an export archive")
    ap.add_argument("--into", metavar="DIR",
                    help="parent directory to restore into (required with "
                         "--restore; must not already contain the campaign)")
    args = ap.parse_args(argv)

    if args.restore:
        if not args.into:
            print("usage: --restore needs --into DIR (a parent directory "
                  "that does not already contain the campaign)",
                  file=sys.stderr)
            return 2
        restore(pathlib.Path(args.restore), pathlib.Path(args.into))
        return 0
    if not args.campaign:
        ap.print_usage()
        return 2
    out = pathlib.Path(args.out) if args.out else None
    if out is None:
        import spine
        stamp = datetime.date.today().isoformat()
        out = spine.campaigns_root().parent / f"{args.campaign}-export-{stamp}.zip"
    export(args.campaign, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
