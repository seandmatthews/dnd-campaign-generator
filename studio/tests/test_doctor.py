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


def _ok_probe(key):
    return True, "coding endpoint fine", None


def test_run_all_checks_shapes_and_key_situation(monkeypatch):
    monkeypatch.setattr(doctor, "find_key", lambda: "k")
    checks = doctor.run_all_checks(probe=_ok_probe)
    assert all({"name", "ok", "detail", "fix"} <= set(c) for c in checks)
    by_name = {c["name"]: c for c in checks}
    assert by_name["llm key"]["ok"] is True
    assert by_name["llm key"]["detail"] == "coding endpoint fine"
    assert by_name["llm key"]["required"] is False
    assert by_name["vale"]["required"] is False
    assert {c["name"] for c in checks} >= {
        "python", "imports", "node", "pnpm", "vale", "campaigns",
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


def test_find_key_from_env(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "env-key")
    assert doctor.find_key() == "env-key"


def test_find_key_none_when_no_config(monkeypatch, tmp_path):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.setattr(doctor, "_CONFIG", tmp_path / "missing.json")
    assert doctor.find_key() is None


def test_find_key_prefers_rule_one_and_skips_enc(tmp_path, monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    cfg = tmp_path / "provider_config.json"
    cfg.write_text(json.dumps({
        "config": {"providerConfigRules": {"providerRules": [
            {"config": {"access": {"apiKey": "rule-zero"}}},
            {"config": {"access": {"apiKey": "enc:opaque"}}},
            {"config": {"access": {"apiKey": "rule-one"}}},
        ]}}}), encoding="utf-8")
    monkeypatch.setattr(doctor, "_CONFIG", cfg)
    assert doctor.find_key() == "rule-one"  # rule 1 first, enc: skipped


def test_no_key_is_an_optional_finding(monkeypatch):
    monkeypatch.setattr(doctor, "find_key", lambda: None)
    check = doctor.check_llm()
    assert check["ok"] is False
    assert check["required"] is False
    assert check["detail"] == "no key found"
    assert "ZAI_API_KEY" in check["fix"]


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
    monkeypatch.setattr(doctor, "check_pnpm",
                        lambda: doctor._mk("pnpm", True, "ok"))
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
    monkeypatch.setattr(doctor, "check_pnpm",
                        lambda: doctor._mk("pnpm", True, "ok"))
    monkeypatch.setattr(doctor, "check_engines", lambda: [])
    monkeypatch.setattr(doctor, "check_vale",
                        lambda: doctor._mk("vale", True, "absent", required=False))
    monkeypatch.setattr(doctor, "check_campaigns",
                        lambda: doctor._mk("campaigns", True, "ok"))
    monkeypatch.setattr(doctor, "find_key", lambda: None)
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
