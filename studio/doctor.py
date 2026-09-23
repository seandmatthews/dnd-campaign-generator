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
      python studio/doctor.py --fix     PERFORM the safe repairs (relink
                                        broken pnpm tool trees, run the pip
                                        line); everything else stays advice —
                                        assemble.py is never auto-run (it
                                        writes canon)
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

import spine

STUDIO_DIR = Path(__file__).resolve().parent
PARENT = STUDIO_DIR.parent

STUDIO_MODULES = ["fastapi", "uvicorn", "yaml", "httpx"]
API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
PROBE_MODEL = "glm-5.3-flash"
PROBE_TIMEOUT_S = 15
ENGINE_PROBE_TIMEOUT_S = 30

# the pnpm-linked tool trees with a documented repair (D7): present but
# with their critical module un-loadable means a broken junction.
DND_MAPPING_TOOLS = PARENT / "dnd-mapping" / "tools"
SETTLEMAKER_DIR = DND_MAPPING_TOOLS / "settlemaker"
FMG_DIR = DND_MAPPING_TOOLS / "Fantasy-Map-Generator"
PNPM_INSTALL_CMD = ["pnpm", "install", "--offline"]

# a live/interrupted week leaves this flag in the engine home (the engine
# home is scratch; the campaign is canon — copy-back only happens on success)
WEEK_RUN_FLAG = PARENT / "agentic-simulator" / "world" / ".week-run-flag"

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
                   fix="install pnpm (e.g. `corepack enable` or "
                       "https://pnpm.io/installation) — the map tool "
                       "recipes use pnpm")
    ran, line = _version_of(["pnpm", "--version"])
    return _mk("pnpm", ran, f"on PATH at {where}" + (f" ({line})" if ran else f" ({line})"),
               fix=None if ran else "pnpm exists but would not run")


def check_engines():
    """The three sibling engine roots exist and their key CLIs run.

    Probes are usage probes (no args): import_fmg.py prints its docstring
    and exits 1, tick.py prints the faction views and exits 0, check.py
    prints argparse usage and exits 2 — any of those means "runnable".
    List-form args, cwd=engine root, never shell=True (the parent path
    contains `&`).

    While a week run is in flight the simulator probe is skipped: tick.py's
    parting save_state replaces state.yaml, and a concurrent reader can make
    that os.replace fail on Windows (sharing violation) — losing the whole
    week after the spend. The other engines' homes are untouched by a week,
    so they still probe."""
    import engines
    out = []
    week_busy = engines.week_flag_path().is_file()
    for dirname, script, fix in ENGINES:
        name = f"engine: {dirname}"
        root = PARENT / dirname
        if not root.is_dir():
            out.append(_mk(name, False, f"{root} not found", fix=fix))
            continue
        if week_busy and root == engines.AGENTIC_SIMULATOR:
            out.append(_mk(name, True,
                           "skipped — a week run is in progress; the probe "
                           "would race the engine's state save",
                           required=False))
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


def check_tool_trees():
    """The two third-party tool trees (git-ignored on purpose — setup-tools.py
    clones, patches, pins and builds them; optional: read-only studio use and
    the fast test tier never need them)."""
    out = []
    for label, root, marker in (
        ("Fantasy-Map-Generator", FMG_DIR, Path("node_modules") / "vitest" / "vitest.mjs"),
        ("settlemaker", SETTLEMAKER_DIR, Path("dist") / "index.js"),
    ):
        present = (root / marker).is_file()
        out.append(_mk(f"tool: {label}", present,
                       "ready" if present else
                       f"missing — {label}/ is not under dnd-mapping/tools/",
                       fix=None if present else
                       "python setup-tools.py  (clones, patches, pins and "
                       "builds both trees; needs network once)",
                       required=False))
    return out


def check_week_run(path=None):
    """NEW: is a week run in progress (or left over from a crash)?

    Reports whether agentic-simulator/world/.week-run-flag exists. Absence
    is fine. Presence is informational (required=False): the engine home is
    scratch and is re-copied from the campaign on the next week/boot — a
    crashed run never corrupts a campaign, because copy-back only happens
    on success."""
    import engines
    flag = path or engines.week_flag_path()  # call-time seam: tests relocate it
    if flag.exists():
        return _mk("week run", False, f"in progress or interrupted ({flag})",
                  fix="let it finish if it is still running; otherwise just "
                      "boot — the engine home is restored from the campaign "
                      "(copy-back only happens on a successful week)",
                  required=False)
    return _mk("week run", True, "no week run in progress", required=False)


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
    # Folders whose names cannot be addressed (bad slug) are skipped by the
    # app itself — name them so "where did my campaign go?" has an answer.
    skipped = spine.skipped_campaign_folders()
    detail = f"{len(found)} campaign(s): " + ", ".join(found)
    if skipped:
        detail += f" (skipped, unaddressable names: {', '.join(skipped)})"
    return _mk("campaigns", True, detail,
               fix=None if not skipped else
               "rename the skipped folder(s) to a lowercase slug "
               "(a-z, 0-9, hyphens) — the studio only lists addressable names")


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


def check_llm_key():
    """Presence-only llm check (no network) — the health strip's quick mode:
    a page load must not spend tokens or wait on an API round-trip."""
    key = find_key()
    if key is None:
        return _mk("llm key", False, "no key found",
                   fix="set ZAI_API_KEY, or put the Z.ai key in "
                       "~/.zcode/v2/provider_config.json — generation and live "
                       "weeks need it; everything else works without",
                   required=False)
    return _mk("llm key", True, "key found (presence check — not probed)",
               required=False)


# ------------------------------------------------------------------- report


def run_all_checks(probe=None, modules=None, live_llm=True):
    checks = [check_python(), check_imports(modules), check_node(), check_pnpm()]
    checks.extend(check_engines())
    checks.extend(check_tool_trees())
    checks.append(check_week_run())
    checks.append(check_vale())
    checks.append(check_campaigns())
    checks.append(check_llm(probe) if live_llm else check_llm_key())
    return checks


def report(probe=None, modules=None, live_llm=True):
    checks = run_all_checks(probe=probe, modules=modules, live_llm=live_llm)
    overall = all(c["ok"] for c in checks if c["required"])
    return {"ok": overall, "checks": checks}


# --------------------------------------------------------------------- --fix


def _node_import_ok(entry: Path, timeout: int = ENGINE_PROBE_TIMEOUT_S):
    """(loads, why) — can node import this module at all? A broken pnpm
    junction shows up as a resolution error here. Dynamic import works for
    ESM and CJS alike. List-form args (the parent path contains `&`)."""
    probe = ("import(require('url').pathToFileURL(process.argv[1]).href)"
             ".then(function () {}, function (e) {"
             "console.error(String(e)); process.exit(3); })")
    cmd = ["node", "-e", probe, str(entry)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except FileNotFoundError:
        return True, "node not on PATH — cannot probe (skipped)"
    except subprocess.TimeoutExpired:
        return True, "import did not return in time (assumed loadable)"
    if proc.returncode == 0:
        return True, f"{entry.name} loads"
    lines = [ln for ln in (proc.stderr or "").strip().splitlines() if ln]
    why = lines[-1] if lines else f"exit {proc.returncode}"
    return False, f"{entry.name} will not load: {why[:120]}"


def settlemaker_ok(root=None):
    """Probe (a): the tree exists but dist/index.js won't import → the
    documented pnpm relink applies. A missing tree is NOT a junction
    problem (the engine check already reports that)."""
    root = root or SETTLEMAKER_DIR
    if not root.is_dir():
        return True, "not installed"
    entry = root / "dist" / "index.js"
    if not entry.is_file():
        return False, "dist/index.js missing — the tool tree is incomplete"
    return _node_import_ok(entry)


def fmg_ok(root=None):
    """Probe (b): the tree exists but node_modules/vitest/vitest.mjs is
    missing → the headless FMG pipeline cannot run → relink."""
    root = root or FMG_DIR
    if not root.is_dir():
        return True, "not installed"
    if not (root / "node_modules" / "vitest" / "vitest.mjs").is_file():
        return False, "node_modules/vitest/vitest.mjs missing — junction broken"
    return True, "node_modules/vitest/vitest.mjs present"


def _pnpm_cmd(args):
    """[pnpm, *args] — or through its .cmd/.bat shim, which CreateProcess
    cannot exec directly (same story as _version_of above). None when pnpm
    is not on PATH at all."""
    where = shutil.which("pnpm")
    if not where:
        return None
    if where.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", where, *args]
    return ["pnpm", *args]


MISSING_PNPM_ADVICE = (
    "install pnpm first (corepack enable or https://pnpm.io/installation), "
    "then re-run python studio/doctor.py --fix")


def plan_fixes(rep=None):
    """What --fix would do: a list of plans
    {name, why, cmd, cwd, env, do, advice} — do=True plans are safe to run
    (pnpm relink, the pip line); do=False plans are advice only, because
    they write canon (assemble.py) or need something the doctor cannot
    install (a missing python/node itself, an LLM key)."""
    rep = rep or report()
    plans = []
    by_name = {c["name"]: c for c in rep["checks"]}

    imports = by_name.get("imports")
    if imports and not imports["ok"]:
        plans.append({
            "name": "python packages",
            "why": imports["detail"],
            "cmd": [sys.executable, "-m", "pip", "install", "-r",
                    str(STUDIO_DIR / "requirements.txt")],
            "cwd": None, "env": None, "do": True, "advice": None,
        })

    for label, root, probe in (("settlemaker", SETTLEMAKER_DIR, settlemaker_ok),
                               ("Fantasy-Map-Generator", FMG_DIR, fmg_ok)):
        ok, why = probe(root)
        if not ok:
            cmd = _pnpm_cmd(PNPM_INSTALL_CMD[1:])
            plans.append({
                "name": f"pnpm relink: {label}",
                "why": why,
                "cmd": cmd,
                "cwd": root,
                "env": {"CI": "true"},
                "do": cmd is not None,
                "advice": None if cmd is not None else MISSING_PNPM_ADVICE,
            })

    campaigns = by_name.get("campaigns")
    if campaigns and not campaigns["ok"]:
        plans.append({
            "name": "campaigns",
            "why": campaigns["detail"],
            "cmd": None, "cwd": None, "env": None, "do": False,
            "advice": "run  python studio/assemble.py  to build "
                      "campaigns/ruravaqan from the engine artifacts "
                      "(the doctor never auto-runs it — it writes canon)",
        })
    return plans


def _quote(cmd):
    return " ".join(f'"{c}"' if (" " in c or "&" in c) else c for c in cmd)


def apply_fixes(plans, dry=False, out=print):
    """Run the do=True plans (or narrate them when dry); print the
    advice-only ones. Returns the number of repair commands attempted."""
    ran = 0
    for p in plans:
        if not p.get("do"):
            out(f"  [advice] {p['name']}: {p['why']}")
            out(f"           {p.get('advice') or p.get('fix') or ''}")
            continue
        if dry:
            out(f"  [would run] {p['name']}: {_quote(p['cmd'])}"
                + (f"  (cwd={p['cwd']})" if p.get("cwd") else ""))
            continue
        out(f"  [fixing] {p['name']}: {p['why']}")
        out(f"           running: {_quote(p['cmd'])}"
            + (f"  (cwd={p['cwd']})" if p.get("cwd") else ""))
        ran += 1
        try:
            proc = _run_repair(p["cmd"], cwd=p.get("cwd"), env=p.get("env"))
        except Exception as e:  # noqa: BLE001 — a failed repair is a finding
            out(f"           FAILED: {e}")
            continue
        tail = (proc.stdout or "").strip().splitlines()[-5:]
        for line in tail:
            out(f"           | {line[:110]}")
        if proc.returncode == 0:
            out("           done (exit 0)")
        else:
            out(f"           FAILED (exit {proc.returncode}) — see the "
                "output above; the fix lines in the report still apply")
    return ran


def _run_repair(cmd, cwd=None, env=None, timeout=600):
    """List-form subprocess for repairs — never shell=True (the parent path
    contains `&`)."""
    if env:
        env = {**os.environ, **{k: str(v) for k, v in env.items()}}
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


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
    ap.add_argument("--fix", action="store_true",
                    help="perform the safe repairs (relink broken pnpm tool "
                         "trees, pip install -r studio/requirements.txt); "
                         "everything else is printed as advice — assemble.py "
                         "is never auto-run")
    args = ap.parse_args(argv)
    rep = report()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        _print_report(rep)
    if args.fix:
        plans = plan_fixes(rep)
        if not plans:
            print("Nothing to fix automatically — nothing above has a "
                  "doctor-safe repair.")
        else:
            print(f"{len(plans)} repair(s) found:")
            apply_fixes(plans)
            print()
            print("Re-checking…")
            rep = report()
            _print_report(rep)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
