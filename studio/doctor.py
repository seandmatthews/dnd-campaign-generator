"""Dependency doctor (M5) — the honest answer to "why won't it boot".

Checks, in order: python version, the studio's imports, node, pnpm, the
three engine roots with their key CLIs runnable, vale (optional),
the campaigns tree, and the LLM key situation — no key vs 1113-balance vs
coding-endpoint-fine, classified by ONE tiny live probe.

Stdlib only at module level: the doctor must run when fastapi/uvicorn/yaml
are exactly the things missing. Output is {"ok": overall, "checks":[...]}
where every check is {name, ok, detail, fix, required}. `ok` covers the
required checks; the LLM key and vale are optional (the app works
read-only without a key — generation does not).

CLI:  python studio/doctor.py           human-readable
      python studio/doctor.py --json    the API shape
Importable: doctor.report() — wired to GET /api/doctor (routers/system.py).
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
PARENT = STUDIO_DIR.parent

STUDIO_MODULES = ["fastapi", "uvicorn", "yaml", "httpx"]
API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
PROBE_MODEL = "glm-5.3-flash"
PROBE_TIMEOUT_S = 15
ENGINE_PROBE_TIMEOUT_S = 30

_CONFIG = Path.home() / ".zcode" / "v2" / "provider_config.json"

ENGINES = [
    ("dnd-mapping", "src/import_fmg.py",
     "clone/zip the parent folder whole — studio sits beside dnd-mapping/"),
    ("agentic-simulator", "src/tick.py",
     "clone/zip the parent folder whole — studio sits beside agentic-simulator/"),
    ("verification", "check.py",
     "clone/zip the parent folder whole — studio sits beside verification/"),
]


def _mk(name, ok, detail, fix=None, required=True):
    return {"name": name, "ok": bool(ok), "detail": detail,
            "fix": fix, "required": bool(required)}


# ------------------------------------------------------------------- checks


def check_python():
    v = sys.version_info
    ok = v >= (3, 10)
    return _mk("python", ok, f"Python {v.major}.{v.minor}.{v.micro}"
               + ("" if ok else " (need >= 3.10)"),
               None if ok else "install Python 3.10 or newer (python.org)")


def check_imports(modules=None):
    """Each studio dependency actually imports — not just find_spec: a
    present-but-broken install must be reported, since run.py's friendly
    boot path quotes this check verbatim."""
    names = list(modules or STUDIO_MODULES)
    broken = []
    for m in names:
        try:
            importlib.import_module(m)
        except Exception as e:  # noqa: BLE001 — any import failure is the finding
            reason = f": {e}" if str(e) else ""
            broken.append(f"{m} ({type(e).__name__}{reason})")
    if not broken:
        return _mk("imports", True, "all importable: " + ", ".join(names))
    return _mk("imports", False, "not importable: " + "; ".join(broken),
               fix="pip install -r studio/requirements.txt")


def _version_of(cmd, cwd=None):
    """Run a --version-ish probe; (ran, version-or-error line).

    Windows .cmd/.bat shims (nodist's pnpm) cannot be exec'd directly by
    CreateProcess — they go through ["cmd", "/c", <full path>, ...]. Still
    list-form args against a resolved PATH entry, never a shell string."""
    attempts = [cmd]
    where = shutil.which(cmd[0])
    if where and where.lower().endswith((".cmd", ".bat")):
        attempts.insert(0, ["cmd", "/c", where, *cmd[1:]])
    last = "not found"
    for attempt in attempts:
        try:
            proc = subprocess.run(attempt, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=15, cwd=str(cwd) if cwd else None)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            last = str(e)
            continue
        line = (proc.stdout or "").strip().splitlines()
        if not line:
            line = (proc.stderr or "").strip().splitlines()
        if line:
            return True, line[0]
        last = f"(exit {proc.returncode})"
    return False, last


def check_node():
    where = shutil.which("node")
    if not where:
        return _mk("node", False, "not on PATH",
                   fix="install Node 18+ (https://nodejs.org) — the map engines need it")
    ran, line = _version_of(["node", "--version"])
    return _mk("node", ran, f"on PATH at {where}" + (f" ({line})" if ran else f" ({line})"),
               fix=None if ran else "node exists but would not run")


def check_pnpm():
    where = shutil.which("pnpm")
    if not where:
        return _mk("pnpm", False, "not on PATH",
                   fix="install pnpm only (e.g. `corepack enable` or "
                       "https://pnpm.io/installation) — the nodist npm/npx "
                       "shims on this machine are broken; do not rely on them")
    ran, line = _version_of(["pnpm", "--version"])
    return _mk("pnpm", ran, f"on PATH at {where}" + (f" ({line})" if ran else f" ({line})"),
               fix=None if ran else "pnpm exists but would not run")


def check_engines():
    """The three sibling engine roots exist and their key CLIs run.

    Probes are usage probes (no args): import_fmg.py prints its docstring
    and exits 1, tick.py prints the faction views and exits 0, check.py
    prints argparse usage and exits 2 — any of those means "runnable".
    List-form args, cwd=engine root, never shell=True (the parent path
    contains `&`)."""
    import engines
    out = []
    for dirname, script, fix in ENGINES:
        name = f"engine: {dirname}"
        root = PARENT / dirname
        if not root.is_dir():
            out.append(_mk(name, False, f"{root} not found", fix=fix))
            continue
        try:
            rc, lines = engines.run_py(root, script, timeout=ENGINE_PROBE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — a dead engine is a doctor finding
            out.append(_mk(name, False, f"{script} could not run: {e}", fix=fix))
            continue
        ran = rc in (0, 1, 2) and bool(lines)
        detail = f"{script} runs (exit {rc}: " + (lines[0] if lines else "no output")[:70] + ")"
        out.append(_mk(name, ran, detail,
                       fix=None if ran else f"{script} exited {rc} — see the engine's own docs"))
    return out


def check_vale():
    where = shutil.which("vale")
    if where:
        ran, line = _version_of(["vale", "--version"])
        return _mk("vale", True, f"installed at {where}" + (f" ({line})" if ran else ""),
                   required=False)
    return _mk("vale", True, "not installed — check.py's taste layer is skipped, "
               "the structural layer still runs",
               fix="winget install Vale.Vale (optional)", required=False)


def check_campaigns():
    """campaigns/ exists with at least one campaign (a dir with campaign.yaml)."""
    root = PARENT / "campaigns"
    if not root.is_dir():
        return _mk("campaigns", False, f"{root} does not exist",
                   fix="create campaigns/ (or run python studio/assemble.py "
                       "to build campaigns/ruravaqan from the engine artifacts)")
    found = sorted(d.name for d in root.iterdir()
                   if d.is_dir() and (d / "campaign.yaml").is_file())
    if not found:
        return _mk("campaigns", False, f"{root} has no campaign (a folder with "
                   "campaign.yaml inside)",
                   fix="run python studio/assemble.py, or create "
                       "campaigns/<name>/campaign.yaml")
    return _mk("campaigns", True, f"{len(found)} campaign(s): " + ", ".join(found))


# ------------------------------------------------------------------ llm key


def find_key():
    """$ZAI_API_KEY, else the key in ~/.zcode/v2/provider_config.json —
    mirrors studio/llm_client.py: rule 1 (the coding-plan key) is preferred,
    the rest scanned in order, enc: blobs skipped. None when nothing fits."""
    if os.environ.get("ZAI_API_KEY"):
        return os.environ["ZAI_API_KEY"]
    if _CONFIG.exists():
        try:
            rules = json.loads(_CONFIG.read_text(encoding="utf-8"))[
                "config"]["providerConfigRules"]["providerRules"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        order = list(range(1, len(rules))) + [0]
        for i in order:
            try:
                key = ((rules[i].get("config") or {}).get("access") or {}).get("apiKey")
            except (AttributeError, IndexError, TypeError):
                continue
            if key and not key.startswith("enc:"):
                return key
    return None


def probe_llm(key):
    """ONE tiny live probe to the coding endpoint (max_tokens 16, 'Reply OK').
    Returns (ok, detail, fix) with the situation classified:
    200 fine / 1113 wrong-door / 401 invalid / anything else reported as-is."""
    body = json.dumps({
        "model": PROBE_MODEL,
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 16,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            resp.read()
        return True, "coding endpoint fine", None
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — body is best-effort context
            pass
        code = None
        try:
            err = json.loads(raw)
            node = err.get("error") if isinstance(err, dict) else None
            code = (node.get("code") if isinstance(node, dict) else None) \
                or (err.get("code") if isinstance(err, dict) else None)
        except ValueError:
            pass
        if code is not None and str(code) == "1113":
            return False, "plan pool reachable but balance/subscription door " \
                          "wrong — use the coding endpoint", \
                   f"requests must hit {API_URL} (the studio already does); " \
                   "the configured key is probably not a coding-plan key"
        if e.code == 401:
            return False, "key invalid (HTTP 401)", \
                   "check ZAI_API_KEY, or the apiKey in " \
                   "~/.zcode/v2/provider_config.json"
        return False, f"endpoint returned HTTP {e.code}: {raw[:120] or e.reason}", \
               "see the Z.ai error code, then retry"
    except Exception as e:  # noqa: BLE001 — network errors are findings, not crashes
        return False, f"endpoint unreachable: {e}", "check the network, then retry"


def check_llm(probe=None):
    key = find_key()
    if key is None:
        return _mk("llm key", False, "no key found",
                   fix="set ZAI_API_KEY, or put the Z.ai key in "
                       "~/.zcode/v2/provider_config.json — generation and live "
                       "weeks need it; everything else works without",
                   required=False)
    try:
        ok, detail, fix = (probe or probe_llm)(key)
    except Exception as e:  # noqa: BLE001 — a throwing probe is a finding
        ok, detail, fix = False, f"probe failed: {e}", None
    return _mk("llm key", ok, detail, fix=fix, required=False)


# ------------------------------------------------------------------- report


def run_all_checks(probe=None, modules=None):
    checks = [check_python(), check_imports(modules), check_node(), check_pnpm()]
    checks.extend(check_engines())
    checks.append(check_vale())
    checks.append(check_campaigns())
    checks.append(check_llm(probe))
    return checks


def report(probe=None, modules=None):
    checks = run_all_checks(probe=probe, modules=modules)
    overall = all(c["ok"] for c in checks if c["required"])
    return {"ok": overall, "checks": checks}


def _print_report(rep):
    print("Campaign Studio doctor")
    print()
    for c in rep["checks"]:
        tag = "ok  " if c["ok"] else ("FAIL" if c["required"] else "info")
        print(f"  [{tag}] {c['name']:<22} {c['detail']}")
        if not c["ok"] and c.get("fix"):
            print(f"           fix: {c['fix']}")
        elif c["ok"] and c.get("fix"):
            print(f"           note: {c['fix']}")
    print()
    blocked = [c for c in rep["checks"] if not c["ok"] and c["required"]]
    optional = [c for c in rep["checks"] if not c["ok"] and not c["required"]]
    if not blocked and not optional:
        print("All clear. Boot with:  python studio/run.py  (or double-click studio/start.bat)")
    elif not blocked:
        print("The studio will boot (read-only without an LLM key). "
              "See the info lines above for generation.")
        print("Boot with:  python studio/run.py  (or double-click studio/start.bat)")
    else:
        print("Not ready — fix the FAIL lines above, then re-run "
              "python studio/doctor.py.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Check every prerequisite of Campaign Studio and say "
                    "precisely which one is missing.")
    ap.add_argument("--json", action="store_true",
                    help="print the API shape instead of the human report")
    args = ap.parse_args(argv)
    rep = report()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        _print_report(rep)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
