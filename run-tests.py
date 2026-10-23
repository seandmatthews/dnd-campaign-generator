#!/usr/bin/env python3
"""Run the Campaign Studio test tiers from the repo root.

  python run-tests.py          fast tier: agentic-simulator, verification,
                               studio unit tests (seconds; engines mocked)
  python run-tests.py --all    fast tier + the integration tier: real engine
                               CLIs, real bytes, temp dirs (studio/tests,
                               marked `integration`)

Stdlib only. Every engine invocation is list-form args with cwd set
explicitly — nothing ever goes through a shell (a rule kept from when
the parent path contained `&`). Output streams live; each suite gets a summary line and the final
table; the run exits nonzero if any suite failed.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# The fast tier. verification/tests is a zero-dependency direct-run script
# (no test_ functions for pytest to collect), so it runs via its documented
# runner: python verification/tests/test_check.py. The JS suite is skipped
# (with a note) when node is not installed.
FAST_SUITES = [
    ("agentic-simulator",
     [sys.executable, "-m", "pytest", "agentic-simulator/tests", "-q"]),
    ("verification", [sys.executable, "verification/tests/test_check.py"]),
    ("dnd-mapping", [sys.executable, "dnd-mapping/tests/test_import_fmg.py"]),
    ("dnd-mapping-battlemap", [sys.executable, "dnd-mapping/tests/test_battlemap.py"]),
    ("dnd-mapping-provenance", [sys.executable, "dnd-mapping/tests/test_provenance.py"]),
    ("studio", [sys.executable, "-m", "pytest", "studio/tests", "-q"]),
]

_node = shutil.which("node")
if _node:
    FAST_SUITES.append(
        ("studio-js", [_node, "studio/tests/js/test_esc.cjs"]))
    FAST_SUITES.append(
        ("studio-js-logtail", [_node, "studio/tests/js/test_logtail.cjs"]))
    FAST_SUITES.append(
        ("studio-js-handoff", [_node, "studio/tests/js/test_handoff.cjs"]))
    FAST_SUITES.append(
        ("studio-js-sessions", [_node,
                                "studio/tests/js/test_sessions_prepare.cjs"]))
    FAST_SUITES.append(
        ("studio-js-staging", [_node,
                               "studio/tests/js/test_sessions_staging.cjs"]))
    FAST_SUITES.append(
        ("studio-js-durations", [_node,
                                 "studio/tests/js/test_durations.cjs"]))
    FAST_SUITES.append(
        ("studio-js-world", [_node,
                             "studio/tests/js/test_world_tab.cjs"]))
    FAST_SUITES.append(
        ("studio-js-review-order", [_node,
                                    "studio/tests/js/test_review_order.cjs"]))
    FAST_SUITES.append(
        ("studio-js-review-statblock", [_node,
                                        "studio/tests/js/test_review_statblock.cjs"]))
    FAST_SUITES.append(
        ("studio-js-sessions-tab", [_node,
                                    "studio/tests/js/test_sessions_tab.cjs"]))
    FAST_SUITES.append(
        ("studio-js-banners", [_node,
                               "studio/tests/js/test_core_banners.cjs"]))
    FAST_SUITES.append(
        ("studio-js-toast", [_node,
                             "studio/tests/js/test_sim_toast.cjs"]))
    FAST_SUITES.append(
        ("studio-js-design", [_node,
                              "studio/tests/js/test_design_feedback.cjs"]))
    FAST_SUITES.append(
        ("studio-js-design-restore", [_node,
                                      "studio/tests/js/test_design_restore.cjs"]))
    FAST_SUITES.append(
        ("studio-js-maps-seed", [_node,
                                 "studio/tests/js/test_maps_seed.cjs"]))
    FAST_SUITES.append(
        ("studio-js-maps-burg-form", [_node,
                                      "studio/tests/js/test_maps_burg_form.cjs"]))
    FAST_SUITES.append(
        ("studio-js-prep", [_node,
                            "studio/tests/js/test_prep_feedback.cjs"]))
    FAST_SUITES.append(
        ("studio-js-api-timeout", [_node,
                                   "studio/tests/js/test_api_timeout.cjs"]))
    FAST_SUITES.append(
        ("studio-js-log-clean", [_node,
                                 "studio/tests/js/test_maps_log_clean.cjs"]))
    FAST_SUITES.append(
        ("studio-js-copy-claims", [_node,
                                   "studio/tests/js/test_copy_claims.cjs"]))
    FAST_SUITES.append(
        ("studio-js-campaign-select-order",
         [_node, "studio/tests/js/test_campaign_select_order.cjs"]))
    # Browser E2E (ticket 36): the first-DM pass against a real server +
    # stub LLM provider, via the local @playwright/test install. Skipped
    # with a note until `npm install` has run in studio/tests/e2e (and
    # `npx playwright install chromium` fetched the browser).
    _E2E_DIR = ROOT / "studio" / "tests" / "e2e"
    _E2E_CLI = _E2E_DIR / "node_modules" / "@playwright" / "test" / "cli.js"
    if _E2E_CLI.is_file():
        FAST_SUITES.append(
            ("studio-e2e",
             [_node, str(_E2E_CLI.relative_to(ROOT)), "test",
              "--config=studio/tests/e2e/playwright.config.ts"]))
    else:
        print("note: studio-e2e skipped — studio/tests/e2e has "
              "no node_modules (run: cd studio/tests/e2e && npm install "
              "&& npx playwright install chromium)")
else:
    print("note: node not found — skipping the studio-js (esc/logtail/"
          "handoff/sessions/staging/durations/world/sessions-tab/banners/"
          "toast/design/design-restore/maps-seed/maps-burg-form/prep/"
          "api-timeout/"
          "log-clean/copy-claims/campaign-select-order) and the "
          "studio-e2e suites (first_dm + second_evening)")

# The integration tier: real engines behind studio/engines.py's wrappers.
# -m integration overrides the studio/pytest.ini addopts (-m "not integration").
INTEGRATION = ("integration",
               [sys.executable, "-m", "pytest", "studio/tests",
                "-m", "integration", "-q"])

# What the integration tier needs that git does not carry (campaign-data/
# and tools/ are gitignored): without these it error-spams instead of
# skipping.
INTEGRATION_NEEDS = [
    ROOT / "campaign-data" / "campaigns" / "ruravaqan" / "simulation"
    / "state.yaml",
    ROOT / "dnd-mapping" / "tools" / "Fantasy-Map-Generator",
    ROOT / "dnd-mapping" / "tools" / "settlemaker" / "dist" / "index.js",
]


def run_suite(name, cmd):
    print(f"\n=== {name} ===  ({' '.join(cmd)})", flush=True)
    start = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(ROOT))  # stdio streams live
    secs = time.monotonic() - start
    ok = proc.returncode == 0
    print(f"--- {name}: {'PASS' if ok else 'FAIL (rc=%d)' % proc.returncode}"
          f" in {secs:.1f}s", flush=True)
    return name, ok, secs


def main():
    args = sys.argv[1:]
    all_tiers = "--all" in args
    unknown = [a for a in args if a != "--all"]
    if unknown:
        print(f"usage: python run-tests.py [--all]  (unknown args: {unknown})")
        return 2

    suites = list(FAST_SUITES)
    tier = "fast"
    if all_tiers:
        missing = [p for p in INTEGRATION_NEEDS if not p.exists()]
        if missing:
            print("note: skipping the integration tier — missing (gitignored) "
                  "prerequisites:\n  " + "\n  ".join(str(p) for p in missing)
                  + "\n  the tier runs on the machines that have them; see "
                    "studio/tests/test_integration.py")
        else:
            suites.append(INTEGRATION)
            tier = "fast + integration"
    print(f"run-tests.py: {len(suites)} suite(s), {tier} tier, root {ROOT}")

    results = [run_suite(name, cmd) for name, cmd in suites]

    width = max(len(name) for name, _, _ in results)
    print("\n=== summary ===")
    for name, ok, secs in results:
        print(f"  {name:<{width}}  {'PASS' if ok else 'FAIL'}  {secs:6.1f}s")
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"FAILED suites: {', '.join(failed)}")
        return 1
    print("all suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
