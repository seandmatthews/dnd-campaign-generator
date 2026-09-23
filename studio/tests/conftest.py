"""Shared pytest fixtures: path setup, a fixture campaign tree, TestClient.

The spine root is a module-level constant (spine.CAMPAIGNS_ROOT) precisely so
tests can point it at tests/fixture-campaigns/ (or a writable copy of it)
instead of the real campaigns/ tree.
"""

import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STUDIO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO_DIR))

import server  # noqa: E402
import spine  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixture-campaigns"


@pytest.fixture()
def spine_root(monkeypatch):
    """Read-only fixture campaigns root."""
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", FIXTURES)
    return FIXTURES


@pytest.fixture()
def tmp_spine_root(spine_root, monkeypatch, tmp_path):
    """A writable copy of the fixture tree (for tests that PUT/write)."""
    dest = tmp_path / "campaigns"
    shutil.copytree(FIXTURES, dest)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", dest)
    return dest


@pytest.fixture()
def client():
    from routers import system
    system._doctor_cache["at"] = None
    system._doctor_cache["report"] = None
    return TestClient(server.app)


def wait_for_job(client, job_id, timeout=10.0):
    """Poll GET /api/jobs/{id} until the job leaves queued/running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def tiny_fmg_dump(seed="42"):
    """A minimal but structurally real FMG headless dump (columnar cells,
    raw 0-placeholder lists) the studio renderer can actually draw. No
    engine calls in tests — this is what the fake FMG step 'writes'."""
    return {
        "info": {"version": "1.153.1-headless", "mapName": "Testland",
                 "seed": seed, "width": 100, "height": 80,
                 "generator": "node (headless GenerationPipeline)"},
        "settings": {"seed": seed,
                     "graph": {"width": 100, "height": 80, "points": 8}},
        "pack": {
            "vertices": {"p": [[0, 0], [100, 0], [100, 80], [0, 80],
                               [50, 10], [50, 70], [20, 40], [80, 40]]},
            "cells": {
                "v": [[0, 4, 6], [4, 1, 7], [1, 2, 7], [2, 5, 7],
                      [5, 3, 6], [3, 0, 6]],
                "f": [1, 2, 2, 2, 2, 1],
                "state": [0, 1, 1, 1, 1, 0],
            },
            # raw lists keep FMG's index-0 placeholder: index == id
            "features": [0, {"i": 1, "type": "ocean"},
                         {"i": 2, "type": "island", "land": True}],
            "states": [0, {"i": 1, "name": "Testonia", "color": "#aa4040",
                           "cells": [1, 2, 3, 4], "pole": [50, 40]}],
            "burgs": [0, {"i": 1, "name": "Provis", "x": 50.0, "y": 40.0,
                          "state": 1, "port": True, "capital": True}],
        },
    }
