"""Forecast bundles: propose-and-discard projections, never canon.

A forecast runs the REAL engine forward 2-3 weeks on scratch data and
records what it saw — per week, the chronicle and the mechanical diff —
under `simulation/forecasts/<id>/`. It is deliberately NOT a proposal:

  - it never occupies the one pending-proposal slot, so it never blocks
    the next real week;
  - the accept route cannot publish it (there is no accept);
  - the bundles are git-ignored (campaigns/.gitignore) — a projection is
    not canon and not undo-trail material;
  - at most one ACTIVE forecast per campaign; discard flips the status
    and the bundle stays as a receipt of what the DM chose not to keep.

Discarded forecasts are never fed into future prompts.
"""

import datetime
import pathlib

import yaml

import spine
from proposals import new_id

SCHEMA_VERSION = 1


def _root(d: pathlib.Path) -> pathlib.Path:
    return d / "simulation" / "forecasts"


def _bundle(name: str, forecast_id: str) -> pathlib.Path:
    bdir = _root(spine.campaign_dir(name)) / forecast_id
    if not bdir.is_dir() or not (bdir / "manifest.yaml").is_file():
        raise ForecastError(f"no such forecast: {forecast_id}")
    return bdir


class ForecastError(Exception):
    pass


def create(name: str, *, base_week, weeks: list, run_record: dict) -> str:
    """Stage a forecast bundle. weeks: [{week, title, markdown, diff}].
    Caller holds the campaign lock."""
    bdir = _root(spine.campaign_dir(name)) / new_id()
    bdir.mkdir(parents=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "forecast_id": bdir.name,
        "kind": "forecast",
        "status": "active",
        "base_week": base_week,
        "weeks_planned": len(weeks),
        "created": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "run_record": run_record,
    }
    for w in weeks:
        fn = f"week-{int(w['week']):04d}.yaml"
        (bdir / fn).write_text(
            yaml.safe_dump(w, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8")
        manifest.setdefault("artifacts", {})[f"week-{w['week']}"] = fn
    spine.write_text_atomic(bdir / "manifest.yaml", yaml.safe_dump(
        manifest, sort_keys=False, allow_unicode=True, width=100))
    return bdir.name


def _load_manifest(bdir: pathlib.Path) -> dict | None:
    try:
        doc = yaml.safe_load(
            (bdir / "manifest.yaml").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, yaml.YAMLError):
        return None


def find_active(name: str) -> dict | None:
    root = _root(spine.campaign_dir(name))
    if not root.is_dir():
        return None
    for bdir in sorted(root.iterdir(), reverse=True):
        if not bdir.is_dir():
            continue
        m = _load_manifest(bdir)
        if m and m.get("status") == "active":
            return m
    return None


def load(name: str, forecast_id: str) -> dict:
    """{manifest, weeks: [{week, title, markdown, diff}]} — everything the
    forecast panel renders. Reads disk, so it survives a restart."""
    bdir = _bundle(name, forecast_id)
    manifest = _load_manifest(bdir)
    if not manifest:
        raise ForecastError(f"forecast {forecast_id} is damaged")
    weeks = []
    for fn in sorted(bdir.glob("week-*.yaml")):
        try:
            w = yaml.safe_load(fn.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise ForecastError(f"forecast {forecast_id} is damaged: {e}")
        if isinstance(w, dict):
            weeks.append(w)
    weeks.sort(key=lambda w: int(w.get("week") or 0))
    return {"manifest": manifest, "weeks": weeks}


def set_status(name: str, forecast_id: str, status: str,
               decided_note: str = "") -> dict:
    bdir = _bundle(name, forecast_id)
    manifest = _load_manifest(bdir)
    if not manifest:
        raise ForecastError(f"forecast {forecast_id} is damaged")
    if manifest.get("status") != "active":
        raise ForecastError(
            f"forecast {forecast_id} is {manifest.get('status')!r}, "
            "not active")
    manifest["status"] = status
    if decided_note:
        manifest["decision_note"] = decided_note
    spine.write_text_atomic(bdir / "manifest.yaml", yaml.safe_dump(
        manifest, sort_keys=False, allow_unicode=True, width=100))
    return manifest
