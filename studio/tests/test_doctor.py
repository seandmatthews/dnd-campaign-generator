"""Doctor (M5): check shapes, the key finder, the LLM probe classification,
and the overall/required split. Every probe is faked — the suite makes no
live LLM call. (The engine-CLI probes in run_all_checks are real but local,
fast, and read-only usage probes.)"""

import io
import json
import sys
import urllib.error
import urllib.request

import doctor


def _http_error(code, body):
    payload = json.dumps(body).encode()
    return urllib.error.HTTPError("http://probe", code, "err", {},
                                  io.BytesIO(payload))


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"choices": []}'


def _raise(e):
    def inner(req, timeout=None):
        raise e
    return inner


def _ok_probe(key, url=None, model=None):
    return True, "coding endpoint fine", None


def _isolate_llm(monkeypatch, tmp_path):
    """Pin the llm config world: no LLM_* env, no llm.env, no ZCode config
    — tests then opt in to exactly the state they exercise."""
    import llm_client
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "ZAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm_client, "ENV_FILE", tmp_path / "absent-llm.env")
    monkeypatch.setattr(llm_client, "_CONFIG", tmp_path / "absent-cfg.json")


def test_run_all_checks_shapes_and_key_situation(monkeypatch, tmp_path):
    _isolate_llm(monkeypatch, tmp_path)
    monkeypatch.setenv("ZAI_API_KEY", "k")
    checks = doctor.run_all_checks(probe=_ok_probe)
    assert all({"name", "ok", "detail", "fix"} <= set(c) for c in checks)
    by_name = {c["name"]: c for c in checks}
    assert by_name["llm key"]["ok"] is True
    assert by_name["llm key"]["detail"].startswith("coding endpoint fine")
    assert "[key: $ZAI_API_KEY]" in by_name["llm key"]["detail"]
    assert by_name["llm key"]["required"] is False
    assert by_name["vale"]["required"] is False
    assert by_name["uv (optional)"]["required"] is False
    assert {c["name"] for c in checks} >= {
        "python", "imports", "node", "uv (optional)", "vale", "campaigns",
        "engine: dnd-mapping", "engine: agentic-simulator", "engine: verification"}


def test_import_check(monkeypatch):
    ok = doctor.check_imports(modules=["json", "pathlib"])
    assert ok["ok"] is True
    bad = doctor.check_imports(modules=["json", "studio_no_such_module"])
    assert bad["ok"] is False
    assert "studio_no_such_module" in bad["detail"]
    assert "pip install -r studio/requirements.txt" in bad["fix"]


def test_python_check():
    check = doctor.check_python()
    assert check["name"] == "python"
    assert check["ok"] == (sys.version_info >= (3, 10))
    assert "Python " in check["detail"]


def test_find_key_from_env(monkeypatch, tmp_path):
    _isolate_llm(monkeypatch, tmp_path)
    monkeypatch.setenv("ZAI_API_KEY", "env-key")
    assert doctor.find_key() == "env-key"


def test_find_key_none_when_no_config(monkeypatch, tmp_path):
    _isolate_llm(monkeypatch, tmp_path)
    assert doctor.find_key() is None


def test_find_key_prefers_rule_one_and_skips_enc(tmp_path, monkeypatch):
    _isolate_llm(monkeypatch, tmp_path)
    import llm_client
    cfg = tmp_path / "provider_config.json"
    cfg.write_text(json.dumps({
        "config": {"providerConfigRules": {"providerRules": [
            {"config": {"access": {"apiKey": "rule-zero"}}},
            {"config": {"access": {"apiKey": "enc:opaque"}}},
            {"config": {"access": {"apiKey": "rule-one"}}},
        ]}}}), encoding="utf-8")
    monkeypatch.setattr(llm_client, "_CONFIG", cfg)
    assert doctor.find_key() == "rule-one"  # rule 1 first, enc: skipped


def test_find_key_reads_llm_env(monkeypatch, tmp_path):
    _isolate_llm(monkeypatch, tmp_path)
    import llm_client
    env_file = tmp_path / "llm.env"
    env_file.write_text("LLM_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setattr(llm_client, "ENV_FILE", env_file)
    assert doctor.find_key() == "file-key"


def test_no_key_is_an_optional_finding(monkeypatch, tmp_path):
    _isolate_llm(monkeypatch, tmp_path)
    check = doctor.check_llm()
    assert check["ok"] is False
    assert check["required"] is False
    assert check["detail"] == "no key found"
    assert "llm.env" in check["fix"]


def test_probe_200_is_coding_endpoint_fine(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp())
    ok, detail, fix = doctor.probe_llm("k")
    assert ok is True and detail == "coding endpoint fine" and fix is None


def test_probe_1113_names_the_wrong_door(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _raise(
        _http_error(400, {"error": {"code": 1113,
                                    "message": "insufficient balance"}})))
    ok, detail, fix = doctor.probe_llm("k")
    assert ok is False
    assert "balance/subscription door wrong" in detail
    assert "coding-plan" in fix


def test_probe_401_invalid_key(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _raise(_http_error(401, {"error": {}})))
    ok, detail, fix = doctor.probe_llm("k")
    assert ok is False and "invalid" in detail and "ZAI_API_KEY" in fix


def test_probe_network_error(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _raise(urllib.error.URLError("no route")))
    ok, detail, fix = doctor.probe_llm("k")
    assert ok is False and "unreachable" in detail


def test_overall_ignores_optional_failures(monkeypatch):
    def fail_llm(key=None):
        return False, "no key found", "fix"
    monkeypatch.setattr(doctor, "check_python",
                        lambda: doctor._mk("python", True, "ok"))
    monkeypatch.setattr(doctor, "check_imports",
                        lambda modules=None: doctor._mk("imports", True, "ok"))
    monkeypatch.setattr(doctor, "check_node",
                        lambda: doctor._mk("node", True, "ok"))
    monkeypatch.setattr(doctor, "check_engines",
                        lambda: [doctor._mk("engine: x", True, "ok")])
    monkeypatch.setattr(doctor, "check_vale",
                        lambda: doctor._mk("vale", True, "absent", required=False))
    monkeypatch.setattr(doctor, "check_campaigns",
                        lambda: doctor._mk("campaigns", True, "ok"))
    monkeypatch.setattr(doctor, "check_llm", lambda probe=None: doctor._mk(
        "llm key", False, "no key found", fix="set one", required=False))
    rep = doctor.report()
    assert rep["ok"] is True  # read-only boot is fine without a key
    assert any(c["name"] == "llm key" and not c["ok"] for c in rep["checks"])


def test_overall_false_when_required_fails(monkeypatch):
    monkeypatch.setattr(doctor, "check_python",
                        lambda: doctor._mk("python", False, "too old",
                                           fix="install 3.10"))
    monkeypatch.setattr(doctor, "check_imports",
                        lambda modules=None: doctor._mk("imports", True, "ok"))
    monkeypatch.setattr(doctor, "check_node",
                        lambda: doctor._mk("node", True, "ok"))
    monkeypatch.setattr(doctor, "check_engines", lambda: [])
    monkeypatch.setattr(doctor, "check_vale",
                        lambda: doctor._mk("vale", True, "absent", required=False))
    monkeypatch.setattr(doctor, "check_campaigns",
                        lambda: doctor._mk("campaigns", True, "ok"))
    monkeypatch.setattr(doctor, "_llm_cfg",
                        lambda: (None, "LLM_MODEL is required but not set"))
    rep = doctor.report()
    assert rep["ok"] is False


def test_cli_json_flag(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "report",
                        lambda: {"ok": True, "checks": [
                            {"name": "python", "ok": True, "detail": "d",
                             "fix": None, "required": True}]})
    assert doctor.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    monkeypatch.setattr(doctor, "report",
                        lambda: {"ok": False, "checks": [
                            {"name": "imports", "ok": False, "detail": "m",
                             "fix": "pip install", "required": True}]})
    assert doctor.main(["--json"]) == 1


def test_check_llm_custom_base_without_model_is_a_finding(monkeypatch, tmp_path):
    """A custom LLM_BASE_URL without LLM_MODEL is the one config error:
    reported as the finding, in both the probing and the presence check."""
    _isolate_llm(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    check = doctor.check_llm()
    assert check["ok"] is False and "LLM_MODEL" in check["detail"]
    check = doctor.check_llm_key()
    assert check["ok"] is False and "LLM_MODEL" in check["detail"]


def test_check_llm_key_names_its_source(monkeypatch, tmp_path):
    _isolate_llm(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    check = doctor.check_llm_key()
    assert check["ok"] is True
    assert "key found in $LLM_API_KEY" in check["detail"]


def test_probe_llm_custom_endpoint_success_names_it(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["model"] = json.loads(req.data.decode())["model"]
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ok, detail, fix = doctor.probe_llm(
        "k", url="http://localhost:11434/v1/chat/completions",
        model="llama3.1")
    assert ok is True and "llama3.1" in detail and "11434" in detail
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["model"] == "llama3.1"


def test_probe_llm_401_fix_mentions_llm_env(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _raise(_http_error(401, {"error": {}})))
    ok, detail, fix = doctor.probe_llm("k")
    assert ok is False and "invalid" in detail and "llm.env" in fix


def test_check_engines_skips_simulator_probe_while_week_busy(monkeypatch, tmp_path):
    """The probe-vs-save_state race: tick.py's parting save_state replaces
    state.yaml, and a concurrent reader (this probe) can make the os.replace
    fail on Windows — losing the week after the spend. While the flag is up
    the simulator check reports a skip, and no engine subprocess runs."""
    import engines
    fake_home = tmp_path / "agentic-simulator"
    fake_home.mkdir()
    (engines.week_flag_path()).touch()
    try:
        probed = []

        def spy_probe(root, script, *args, **kwargs):
            probed.append(script)
            return 2, ["usage"]

        monkeypatch.setattr(engines, "run_py", spy_probe)
        checks = doctor.check_engines()
    finally:
        (engines.week_flag_path()).unlink()
    by_name = {c["name"]: c for c in checks}
    sim = by_name["engine: agentic-simulator"]
    assert sim["ok"] is True and sim["required"] is False
    assert "skipped" in sim["detail"]
    # the untouched engines still probe for real
    assert "src/tick.py" not in probed, probed
    assert "check.py" in probed, probed


def test_doctor_route_caches_the_full_run(client, monkeypatch):
    from routers import system
    calls = {"n": 0}

    def counting_report(*args, **kwargs):
        if kwargs.get("live_llm", True) is False:
            return {"ok": True, "checks": []}  # quick: cheap presence check
        calls["n"] += 1
        return {"ok": True, "checks": [], "call": calls["n"]}

    monkeypatch.setattr(doctor, "report", counting_report)
    first = client.get("/api/doctor").json()
    second = client.get("/api/doctor").json()
    assert calls["n"] == 1, "the full doctor must run once per cache window"
    assert first == second
    # quick stays uncached and never spends the full path
    assert client.get("/api/doctor?quick=1").status_code == 200
    assert calls["n"] == 1


# ------------------------------------------------- workspace gitignore (47)


def test_workspace_ignore_check_missing_file_is_advisory_ok(
        tmp_path, monkeypatch):
    import spine
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_path)
    c = doctor.check_workspace_ignore()
    assert c["ok"] is True and c["required"] is False
    assert "next snapshot" in c["detail"]


def test_workspace_ignore_check_complete_file(tmp_path, monkeypatch):
    import spine
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_path)
    (tmp_path / ".gitignore").write_text(
        ".obsidian/\n**/simulation/forecasts/\n", encoding="utf-8")
    c = doctor.check_workspace_ignore()
    assert c["ok"] is True and c["required"] is False


def test_workspace_ignore_check_missing_lines_names_the_untrack_fix(
        tmp_path, monkeypatch):
    import spine
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    c = doctor.check_workspace_ignore()
    assert c["ok"] is False and c["required"] is False
    assert ".obsidian/" in c["detail"]
    assert "**/simulation/forecasts/" in c["detail"]
    assert "rm -r --cached" in (c["fix"] or ""), \
        "an ignore does not untrack: the fix must name the command"


# --------------------------------------------- git / undo trail (ticket 58)


def test_check_git_absent_names_the_missing_undo_trail(monkeypatch):
    """Git absent is the release bundle's DESIGNED state: informational,
    never a failure (required=False) — but never silent either: the detail
    must name the missing undo trail and the backup story."""
    import canon
    monkeypatch.setattr(canon, "GIT", None)
    c = doctor.check_git()
    assert c["name"] == "git (undo trail)"
    assert c["required"] is False, "absent git must never fail the doctor"
    assert c["ok"] is False  # an info finding line, like the llm key's
    assert "git is not installed" in c["detail"]
    assert "undo/history trail is OFF" in c["detail"]
    assert "back them up" in c["detail"]
    assert c["fix"], "the check names the way out (install git, or not)"


def test_check_git_present_reports_the_trail(monkeypatch):
    import canon
    monkeypatch.setattr(canon, "GIT", "C:/Program Files/Git/cmd/git.exe")
    c = doctor.check_git()
    assert c["ok"] is True
    assert c["required"] is False  # informational either way
    assert "undo history" in c["detail"]


def test_git_check_is_wired_into_the_report(monkeypatch, tmp_path):
    _isolate_llm(monkeypatch, tmp_path)
    monkeypatch.setenv("ZAI_API_KEY", "k")
    import canon
    monkeypatch.setattr(canon, "GIT", None)
    checks = doctor.run_all_checks(probe=_ok_probe)
    git = [c for c in checks if c["name"] == "git (undo trail)"]
    assert git and git[0]["required"] is False, \
        "the undo-spine check is part of the doctor report"
    # required=False is what keeps it out of the overall verdict — an
    # absent git (every bundle install) can never fail the doctor


# ------------------------------------------------ campaign system field (48)


def _campaign_with_system(root, name, system):
    camp = root / name
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "campaign.yaml").write_text(
        f"name: {name}\ntitle: {name}\n"
        + (f"system: {system}\n" if system is not None else ""),
        encoding="utf-8")
    return camp


def test_campaign_system_check_unset_everywhere_is_normal(
        tmp_path, monkeypatch):
    import spine
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_path)
    _campaign_with_system(tmp_path, "ember-coast", None)
    c = doctor.check_campaign_system()
    assert c["ok"] is True and c["required"] is False
    assert "normal" in c["detail"]


def test_campaign_system_check_names_the_value_lowercase(
        tmp_path, monkeypatch):
    import spine
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_path)
    _campaign_with_system(tmp_path, "ember-coast", "ICRPG")
    # a typo passes too — freeform, findings never verdicts
    _campaign_with_system(tmp_path, "salt-marsh", "icrpggg")
    c = doctor.check_campaign_system()
    assert c["ok"] is True and c["required"] is False
    assert "ember-coast: icrpg" in c["detail"]
    assert "salt-marsh: icrpggg" in c["detail"]


def test_campaign_system_check_nonstring_is_the_only_wrongness(
        tmp_path, monkeypatch):
    import spine
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_path)
    camp = _campaign_with_system(tmp_path, "ember-coast", None)
    (camp / "campaign.yaml").write_text(
        "name: ember-coast\nsystem: 5\n", encoding="utf-8")
    c = doctor.check_campaign_system()
    assert c["ok"] is False and c["required"] is False
    assert "not a plain string" in c["detail"]
    assert "system: icrpg" in (c["fix"] or "")
