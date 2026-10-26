"""API.md is the contract — code and doc move together (the file's own
rule, first paragraph). Ticket 69 found drift: GET .../simulation was
documented without places/projects (the route returns both, plus
active_forecast and run_contract), the views engine line omitted --state
(the route passes it — see test_fixes.py for the code-side pin), and the
health line pinned a hardcoded 0.1.0 the code no longer answers. These
tests pin the doc's lines against what the CODE actually answers and
spawns, so the next drift fails the fast tier instead of shipping.
"""

from conftest import STUDIO_DIR

API_MD = STUDIO_DIR / "API.md"


def _segment(start_marker: str, end_marker: str) -> str:
    """The doc text from start_marker up to the next route bullet at
    end_marker — the segment one line-level claim lives in."""
    text = API_MD.read_text(encoding="utf-8")
    start = text.index(start_marker)
    return text[start:text.index(end_marker, start)]


def test_simulation_doc_names_every_key_the_route_returns(
        client, tmp_spine_root):
    """Every key the route returns must appear in the doc's shape line —
    the route is the truth the doc documents."""
    r = client.get("/api/campaign/testvale/simulation")
    assert r.status_code == 200, r.text
    segment = _segment(
        "`GET /api/campaign/{name}/simulation` →",
        "- `POST /api/campaign/{name}/simulation/state`")
    for key in r.json():
        assert f'"{key}"' in segment, (
            f'API.md\'s simulation shape is missing "{key}" — the code '
            "returns it, so the doc must carry it (they move together)")


def test_views_engine_line_carries_the_state_flag():
    """The route spawns `views.py <faction_id> --state <campaign's
    state.yaml>` (no copy-in, no engine lock) — the doc's engine line must
    say so, not the old copy-in-era argument list."""
    segment = _segment(
        "`GET /api/campaign/{name}/simulation/views/{faction_id}`",
        "- `POST /api/campaign/{name}/simulation/design`")
    assert "src/views.py" in segment
    assert "--state" in segment, (
        "the views engine line must carry --state — the code reads the "
        "campaign's own state file, no copy-in")


def test_health_doc_does_not_pin_a_hardcoded_version():
    """The health line documents the VERSION-stamp behavior, not the old
    hardcoded "0.1.0" the code stopped answering (ticket 69)."""
    text = API_MD.read_text(encoding="utf-8")
    assert '"version":"0.1.0"' not in text, (
        "API.md must not pin health to a hardcoded version — health "
        "answers the install's VERSION stamp")
