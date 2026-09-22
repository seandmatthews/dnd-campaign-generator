#!/usr/bin/env python3
"""Run the Campaign Studio test tiers from the repo root.

  python run-tests.py          fast tier: agentic-simulator, verification,
                               studio unit tests (seconds; engines mocked)
  python run-tests.py --all    fast tier + the integration tier: real engine
                               CLIs, real bytes, temp dirs (studio/tests,
                               marked `integration`)

Stdlib only. Every engine invocation is list-form args with cwd set
explicitly — the parent path contains `&`, so nothing ever goes through a
shell. Output streams live; each suite gets a summary line and the final
table; the run exits nonzero if any suite failed.
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# The fast tier. verification/tests is a zero-dependency direct-run script
# (no test_ functions for pytest to collect), so it runs via its documented
# runner: python verification/tests/test_check.py.
FAST_SUITES = [
    ("agentic-simulator",
     [sys.executable, "-m", "pytest", "agentic-simulator/tests", "-q"]),
    ("verification", [sys.executable, "verification/tests/test_check.py"]),
    ("studio", [sys.executable, "-m", "pytest", "studio/tests", "-q"]),
]

# The integration tier: real engines behind studio/engines.py's wrappers.
# -m integration overrides the studio/pytest.ini addopts (-m "not integration").
INTEGRATION = ("integration",
               [sys.executable, "-m", "pytest", "studio/tests",
                "-m", "integration", "-q"])


def run_suite(name, cmd):
    print(f"\n=== {name} ===  ({' '.join(cmd)})", flush=True)
    start = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(ROOT))  # inherits stdio: streams live
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

    suites = list(FAST_SUITES) + ([INTEGRATION] if all_tiers else [])
    tier = "fast + integration" if all_tiers else "fast"
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
