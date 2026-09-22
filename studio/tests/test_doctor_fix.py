"""doctor --fix (H): repair planning and execution. Every probe and every
subprocess is faked — the suite never runs real pnpm or pip, and never
touches the real tool trees."""

import shutil

import pytest

import doctor


def _check(name, ok, detail="d", fix=None, required=True):
    return {"name": name, "ok": ok, "detail": detail, "fix": fix,
            "required": required}


def _fake_report(imports_ok=True, campaigns_ok=True):
    return {"ok": imports_ok and campaigns_ok, "checks": [
        _check("python", True, "Python 3.12.0"),
        _check("imports", imports_ok, "all importable" if imports_ok
               else "not importable: yaml (ModuleNotFoundError)",
               fix=None if imports_ok else "pip install -r studio/requirements.txt"),
        _check("engine: dnd-mapping", True, "runs"),
        _check("week run", True, "no week run in progress", required=False),
        _check("campaigns", campaigns_ok, "2 campaign(s)" if campaigns_ok
               else "campaigns/ does not exist",
               fix=None if campaigns_ok else "run python studio/assemble.py"),
    ]}


def _patch_all_probes(monkeypatch, settlemaker=True, fmg=True, pnpm=True):
    monkeypatch.setattr(doctor, "settlemaker_ok",
                        lambda root=None: (settlemaker, "probe"))
    monkeypatch.setattr(doctor, "fmg_ok",
                        lambda root=None: (fmg, "probe"))
    if pnpm:
        monkeypatch.setattr(doctor, "_pnpm_cmd", lambda args: ["pnpm", *args])
    else:
        monkeypatch.setattr(doctor, "_pnpm_cmd", lambda args: None)


# ------------------------------------------------------------------ probes


def test_settlemaker_probe_missing_tree_is_not_a_junction_problem(tmp_path):
    ok, why = doctor.settlemaker_ok(tmp_path / "nope")
    assert ok is True and "not installed" in why


def test_settlemaker_probe_missing_dist_is_broken(tmp_path):
    ok, why = doctor.settlemaker_ok(tmp_path)
    assert ok is False and "dist/index.js" in why


def test_settlemaker_probe_real_node_import(tmp_path):
    """The load probe itself: a file node can import passes, a missing
    import fails. (The only test here that shells out — node only.)"""
    if not shutil.which("node"):
        pytest.skip("node not on PATH")
    good = tmp_path / "dist" / "index.js"
    good.parent.mkdir()
    good.write_text("export default 42;\n", encoding="utf-8")
    ok, why = doctor._node_import_ok(good)
    assert ok is True and "loads" in why

    bad = tmp_path / "dist" / "broken.js"
    bad.write_text("import './does-not-exist.js';\n", encoding="utf-8")
    ok, why = doctor._node_import_ok(bad)
    assert ok is False and "will not load" in why


def test_fmg_probe(tmp_path):
    ok, _ = doctor.fmg_ok(tmp_path / "nope")
    assert ok is True
    ok, why = doctor.fmg_ok(tmp_path)
    assert ok is False and "vitest.mjs" in why
    marker = tmp_path / "node_modules" / "vitest"
    marker.mkdir(parents=True)
    (marker / "vitest.mjs").write_text("export {};\n", encoding="utf-8")
    ok, _ = doctor.fmg_ok(tmp_path)
    assert ok is True


# -------------------------------------------------------------- plan_fixes


def test_plan_fixes_import_failure_plans_pip(monkeypatch):
    _patch_all_probes(monkeypatch)
    plans = doctor.plan_fixes(_fake_report(imports_ok=True))
    assert not plans  # healthy report -> nothing to do

    plans = doctor.plan_fixes(_fake_report(imports_ok=False))
    pip = [p for p in plans if p["name"] == "python packages"]
    assert len(pip) == 1 and pip[0]["do"] is True
    assert pip[0]["cmd"][-2:-1] == ["-r"]
    assert pip[0]["cmd"][-1].endswith("requirements.txt")
    assert "pip" in pip[0]["cmd"]


def test_plan_fixes_broken_tool_trees_plan_pnpm_relink(monkeypatch):
    _patch_all_probes(monkeypatch, settlemaker=False, fmg=False)
    plans = doctor.plan_fixes(_fake_report())
    relinks = [p for p in plans if p["name"].startswith("pnpm relink:")]
    assert {p["name"] for p in relinks} == {
        "pnpm relink: settlemaker", "pnpm relink: Fantasy-Map-Generator"}
    for p in relinks:
        assert p["do"] is True
        assert "install" in p["cmd"] and "--offline" in p["cmd"]
        assert p["env"] == {"CI": "true"}
        assert p["cwd"] in (doctor.SETTLEMAKER_DIR, doctor.FMG_DIR)


def test_plan_fixes_healthy_tools_plan_nothing(monkeypatch):
    _patch_all_probes(monkeypatch)
    plans = doctor.plan_fixes(_fake_report())
    assert not [p for p in plans if p["name"].startswith("pnpm relink:")]


def test_plan_fixes_without_pnpm_is_advice_only(monkeypatch):
    _patch_all_probes(monkeypatch, settlemaker=False, pnpm=False)
    plans = doctor.plan_fixes(_fake_report())
    relinks = [p for p in plans if p["name"].startswith("pnpm relink:")]
    assert relinks and all(p["do"] is False and p["cmd"] is None
                           for p in relinks)
    assert all("pnpm" in p["advice"].lower() for p in relinks)


def test_plan_fixes_missing_campaigns_is_advice_never_autorun(monkeypatch):
    _patch_all_probes(monkeypatch)
    plans = doctor.plan_fixes(_fake_report(campaigns_ok=False))
    camp = [p for p in plans if p["name"] == "campaigns"]
    assert len(camp) == 1
    assert camp[0]["do"] is False and camp[0]["cmd"] is None
    assert "assemble.py" in camp[0]["advice"]


def test_plan_fixes_defaults_to_a_live_report(monkeypatch):
    monkeypatch.setattr(doctor, "report", lambda: _fake_report())
    _patch_all_probes(monkeypatch)
    assert doctor.plan_fixes() == []


# ------------------------------------------------------------- apply_fixes


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_apply_fixes_runs_only_do_plans(monkeypatch, capsys):
    calls = []

    def fake_run(cmd, cwd=None, env=None, timeout=600):
        calls.append((cmd, cwd, env))
        return _FakeProc(0, "linking node_modules\n+ 42 packages\n")

    monkeypatch.setattr(doctor, "_run_repair", fake_run)
    _patch_all_probes(monkeypatch, settlemaker=False)
    plans = doctor.plan_fixes(_fake_report(campaigns_ok=False))
    ran = doctor.apply_fixes(plans)

    assert ran == 1  # the relink; campaigns stays advice
    assert len(calls) == 1
    cmd, cwd, env = calls[0]
    assert cmd == ["pnpm", "install", "--offline"]
    assert cwd == doctor.SETTLEMAKER_DIR
    assert env == {"CI": "true"}
    out = capsys.readouterr().out
    assert "[fixing]" in out and "[advice]" in out
    assert "assemble.py" in out
    assert "done (exit 0)" in out


def test_apply_fixes_reports_failure_and_continues(monkeypatch, capsys):
    procs = [_FakeProc(1, "", "boom"), _FakeProc(0, "ok\n")]

    def fake_run(cmd, cwd=None, env=None, timeout=600):
        return procs.pop(0)

    monkeypatch.setattr(doctor, "_run_repair", fake_run)
    _patch_all_probes(monkeypatch, settlemaker=False, fmg=False)
    plans = [p for p in doctor.plan_fixes(_fake_report())
             if p["name"] != "python packages"]
    assert len(plans) == 2
    ran = doctor.apply_fixes(plans)
    assert ran == 2
    out = capsys.readouterr().out
    assert out.count("FAILED") == 1
    assert "done (exit 0)" in out  # the second repair still ran


def test_apply_fixes_dry_only_narrates(monkeypatch, capsys):
    def forbidden(*a, **k):
        raise AssertionError("dry mode must not run repairs")
    monkeypatch.setattr(doctor, "_run_repair", forbidden)
    _patch_all_probes(monkeypatch, settlemaker=False)
    plans = doctor.plan_fixes(_fake_report())
    assert doctor.apply_fixes(plans, dry=True) == 0
    out = capsys.readouterr().out
    assert "[would run]" in out and "[fixing]" not in out


# ----------------------------------------------------------- the week flag


def test_week_run_check_absent_flag_is_ok(tmp_path):
    check = doctor.check_week_run(tmp_path / ".week-run-flag")
    assert check["ok"] is True
    assert check["required"] is False


def test_week_run_check_present_flag_is_reported(tmp_path):
    flag = tmp_path / ".week-run-flag"
    flag.write_text("", encoding="utf-8")
    check = doctor.check_week_run(flag)
    assert check["ok"] is False
    assert check["required"] is False
    assert ".week-run-flag" in check["detail"]


def test_week_run_check_defaults_to_engine_home():
    assert doctor.check_week_run()["name"] == "week run"


def test_week_run_check_is_part_of_run_all_checks(monkeypatch):
    monkeypatch.setattr(doctor, "find_key", lambda: "k")
    names = {c["name"] for c in doctor.run_all_checks(probe=lambda k: (
        True, "coding endpoint fine", None))}
    assert "week run" in names


# ----------------------------------------------------------------- the CLI


def test_main_fix_performs_and_rechecks(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "report",
                        lambda: _fake_report(imports_ok=False))
    _patch_all_probes(monkeypatch, settlemaker=False)

    def fake_run(cmd, cwd=None, env=None, timeout=600):
        # pip for the imports plan, the relink for the broken tool tree
        assert ("pip" in cmd) or ("install" in cmd and "--offline" in cmd)
        return _FakeProc(0, "Successfully installed yaml-6.0.3\n")

    monkeypatch.setattr(doctor, "_run_repair", fake_run)
    rc = doctor.main(["--fix"])
    out = capsys.readouterr().out
    assert "[fixing]" in out and "pip" in out
    assert "Re-checking" in out
    assert rc == 1  # the faked post-fix report still fails -> honest exit


def test_main_fix_on_healthy_report_changes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "report", lambda: _fake_report())
    _patch_all_probes(monkeypatch)

    def forbidden(*a, **k):
        raise AssertionError("nothing to fix -> no subprocess at all")
    monkeypatch.setattr(doctor, "_run_repair", forbidden)
    rc = doctor.main(["--fix"])
    out = capsys.readouterr().out
    assert "Nothing to fix automatically" in out
    assert rc == 0


def test_main_json_still_prints_the_api_shape(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "report", lambda: _fake_report())
    _patch_all_probes(monkeypatch)
    assert doctor.main(["--json"]) == 0
    import json
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True and body["checks"][0]["name"] == "python"
