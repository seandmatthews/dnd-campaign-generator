"""Shared pytest bootstrap: put src/ on sys.path once for every test module.

The per-file `sys.path.insert` blocks this replaces did the same thing; the
zero-dependency direct-run suites (`python tests/test_x.py`) keep their own
inserts so they still work without pytest.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
