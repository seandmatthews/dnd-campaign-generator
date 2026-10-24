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
- The export runs under the campaign lock and reads every file exactly
  once: the bytes hashed into the manifest are the bytes archived, so an
  approval or edit landing mid-export cannot produce an archive that
  restores as "corrupt". A lock disciplines the app's writers, not an
  editor that takes none — the single read is what makes the archive
  self-consistent either way.
- The archive destination must be OUTSIDE the campaign directory; the
  export refuses one inside it (the zip would swallow itself).
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
a restore that cannot be loaded fails loudly. The exclusion list is a
trust boundary BOTH ways: members it excludes (.git, credentials,
forecasts, machine noise — even hash-verified in the manifest) are skipped
with a warning naming them, never written, so a hand-zipped copy of a live
campaign restores to the same shape a studio export would have.

Exit codes: 0 ok, 1 error, 2 usage.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import pathlib
import sys
import zipfile


STUDIO_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(STUDIO_DIR))

MANIFEST = "manifest.json"
CAMPAIGN_PREFIX = "campaign/"
EXCLUDES = [
    ".studio-transactions/*",  # publication recovery payloads, not canon
    ".studio-jobs/*",  # rolling job-duration history (ETA estimates), not canon
    "simulation/forecasts/*",  # projections are not canon and not undo-
    # trail material (campaigns/.gitignore) — and a restored stale ACTIVE
    # forecast would block new forecasts until someone finds and discards it
    "__pycache__/*",
    "*.pyc",
    "*.tmp",
    "*.bak",
    "*.log",
    ".git/*",
    ".gitignore",
    "llm.env*",  # a dropped credential never rides along — at any depth:
    "*/llm.env*",  # fnmatch anchors at the string start, so the top-level
    # pattern alone would miss a copy nested deeper in the tree
]
SCHEMA_VERSION = 1

# How long export waits for the campaign lock (a week job holds it for the
# job's whole run — past this, refusing honestly beats blocking a watched
# CLI for an hour). The wait itself covers brief holders: snapshots and the
# short interactive writers.
EXPORT_LOCK_WAIT_S = 10.0


def excluded(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDES)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(path: pathlib.Path, directory: pathlib.Path) -> bool:
    """True when path IS directory or lies somewhere under it
    (case-insensitive — Windows paths compare case-blind)."""
    p = os.path.normcase(str(path))
    d = os.path.normcase(str(directory))
    return p == d or p.startswith(d + os.sep)


def export(campaign: str, out: pathlib.Path) -> pathlib.Path:
    """Zip one campaign + manifest. Returns the archive path.

    Consistency rules (the 2026-10-02 bug pass):
    - the campaign lock is held for the whole walk: an approval or snapshot
      landing mid-export used to produce an archive whose members no longer
      matched the manifest hashes — an export that reported success but
      failed every restore;
    - every file is read exactly ONCE, into memory, and those same bytes
      are both hashed and archived (a lock disciplines app writers, not an
      editor that takes none);
    - the output path must live OUTSIDE the campaign directory: writing
      the zip into the tree being zipped truncates a file the walk already
      hashed and swallows the archive's own half-written bytes."""
    import spine
    root = spine.campaigns_root()
    camp_dir = root / campaign
    if not (camp_dir / "campaign.yaml").is_file():
        print(f"error: no such campaign: {campaign} (under {root})",
              file=sys.stderr)
        raise SystemExit(1)
    if _inside(out.resolve(), camp_dir.resolve()):
        print(f"error: --out {out} is inside the campaign directory "
              f"({camp_dir}) — the archive would swallow itself; choose a "
              "destination outside the campaign",
              file=sys.stderr)
        raise SystemExit(2)
    lock = spine.campaign_lock(campaign)
    if not lock.acquire(timeout=EXPORT_LOCK_WAIT_S):
        print(f"error: {campaign} is locked by another writer (a running "
              f"job?) — gave up after {EXPORT_LOCK_WAIT_S:.0f}s; export "
              "again once it finishes",
              file=sys.stderr)
        raise SystemExit(1)
    try:
        payloads = {}
        for p in sorted(camp_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(camp_dir).as_posix()
            if excluded(rel):
                continue
            payloads[rel] = p.read_bytes()
    finally:
        lock.release()
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
        "files": {rel: sha256(data) for rel, data in payloads.items()},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, indent=2,
                                        ensure_ascii=False))
        for rel in sorted(payloads):
            z.writestr(CAMPAIGN_PREFIX + rel, payloads[rel])
    print(f"exported {len(payloads)} file(s) -> {out}")
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
        # the exclusion list is the trust boundary BOTH ways: a manifest
        # entry for an excluded path (a hand zip hashes everything it
        # zipped) is ignored, not honored — those files are never written,
        # so they are neither required from the archive nor re-verified
        wanted = {rel: digest for rel, digest in manifest["files"].items()
                  if not excluded(rel)}
        # hash-verify everything IN MEMORY before writing anything
        verified: dict = {}
        skipped: list = []
        for member in z.namelist():
            # a hand-zipped campaign folder (Explorer) carries directory
            # entries; they hold no content — parents are made on write
            if member.startswith(CAMPAIGN_PREFIX) and member.endswith("/"):
                continue
            rel = _safe_member(member)
            if rel is None:
                continue
            if excluded(rel):
                skipped.append(rel)  # a hash does not make noise canon
                continue
            expected = wanted.get(rel)
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
        if skipped:
            print("warning: skipped excluded member(s) — the export never "
                  "includes these and neither does a restore: "
                  + ", ".join(sorted(skipped)))
        missing = set(wanted) - set(verified)
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
        for rel, expected in wanted.items():
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
        # the friend path (ticket 58): export.cmd is double-clicked with no
        # arguments — answer with the names they can use, not bare usage
        import spine
        root = spine.campaigns_root()
        names = sorted(d.name for d in root.iterdir()
                       if d.is_dir() and (d / "campaign.yaml").is_file()) \
            if root.is_dir() else []
        if names:
            print(f"your campaigns: {', '.join(names)}")
            print(f"export one with:  export.cmd <name>   "
                  f"(campaigns live under {root})")
        else:
            print(f"no campaigns found under {root} — nothing to export yet")
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
