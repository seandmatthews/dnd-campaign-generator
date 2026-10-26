"""llm.env / provider config (studio/llm_client.py): file parsing,
resolution precedence, and the complete()-level key error.

Everything reads through resolve_config(env_file=...) with a pinned world —
no test depends on the machine's real llm.env, environment, or ZCode config.
"""

import json

import pytest

import llm_client


def _isolate(monkeypatch, tmp_path):
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "ZAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm_client, "ENV_FILE", tmp_path / "absent-llm.env")
    monkeypatch.setattr(llm_client, "_CONFIG", tmp_path / "absent-cfg.json")


def test_read_env_file_parsing(tmp_path):
    f = tmp_path / "llm.env"
    f.write_text(
        "# comment line\n"
        "\n"
        "LLM_BASE_URL=https://api.openai.com/v1\n"
        "LLM_API_KEY = quoted spaces in value \n"
        'LLM_MODEL="gpt-4o-mini"\n'
        "LLM_QUOTED='single'\n"
        "not a pair\n"
        "  # indented comment\n",
        encoding="utf-8")
    out = llm_client.read_env_file(f)
    assert out["LLM_BASE_URL"] == "https://api.openai.com/v1"
    assert out["LLM_API_KEY"] == "quoted spaces in value"  # outer space strip
    assert out["LLM_MODEL"] == "gpt-4o-mini"               # quotes stripped
    assert out["LLM_QUOTED"] == "single"
    assert "not a pair" not in out


def test_read_env_file_bom_and_crlf(tmp_path):
    f = tmp_path / "llm.env"
    f.write_bytes("﻿LLM_MODEL=bom-model\r\nLLM_API_KEY=crlf-key\r\n".encode())
    out = llm_client.read_env_file(f)
    assert out["LLM_MODEL"] == "bom-model"
    assert out["LLM_API_KEY"] == "crlf-key"


def test_read_env_file_missing_is_empty(tmp_path):
    assert llm_client.read_env_file(tmp_path / "nope") == {}


def test_resolve_defaults_are_zai_coding_endpoint(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cfg = llm_client.resolve_config()
    assert cfg["url"] == llm_client.API_BASE_DEFAULT + "/chat/completions"
    assert cfg["model"] == llm_client.MODEL_DEFAULT
    assert cfg["key"] == ""  # legacy discovery finds nothing in the pinned world


def test_resolve_precedence_env_over_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "llm.env"
    f.write_text("LLM_API_KEY=file-key\nLLM_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setattr(llm_client, "ENV_FILE", f)
    cfg = llm_client.resolve_config()
    assert cfg["key"] == "file-key" and cfg["model"] == "file-model"
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    cfg = llm_client.resolve_config()
    assert cfg["key"] == "env-key" and cfg["model"] == "file-model"


def test_resolve_custom_base_demands_model(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "llm.env"
    f.write_text("LLM_BASE_URL=http://localhost:11434/v1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        llm_client.resolve_config(env_file=f)
    f.write_text("LLM_BASE_URL=http://localhost:11434/v1\n"
                 "LLM_MODEL=llama3.1\n", encoding="utf-8")
    cfg = llm_client.resolve_config(env_file=f)
    assert cfg["url"] == "http://localhost:11434/v1/chat/completions"
    assert cfg["model"] == "llama3.1"


def test_resolve_default_base_does_not_demand_model(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "llm.env"
    f.write_text(f"LLM_BASE_URL={llm_client.API_BASE_DEFAULT}\n", encoding="utf-8")
    assert llm_client.resolve_config(env_file=f)["model"] == llm_client.MODEL_DEFAULT


def test_resolve_trailing_slash_base(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "llm.env"
    f.write_text("LLM_BASE_URL=https://api.openai.com/v1/\n"
                 "LLM_MODEL=gpt-4o-mini\n", encoding="utf-8")
    cfg = llm_client.resolve_config(env_file=f)
    assert cfg["url"] == "https://api.openai.com/v1/chat/completions"


def test_complete_no_key_is_a_readable_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="llm.env.example"):
        llm_client.complete("prompt")


def test_complete_uses_resolved_model_and_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    seen = {}

    def fake_post(cfg, body, timeout):
        seen.update(cfg)
        seen["body_model"] = body["model"]
        return {"choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    f = tmp_path / "llm.env"
    f.write_text("LLM_API_KEY=env-file-key\nLLM_MODEL=custom-model\n",
                 encoding="utf-8")
    monkeypatch.setattr(llm_client, "ENV_FILE", f)
    monkeypatch.setattr(llm_client, "_post_json", fake_post)
    out = llm_client.complete("prompt")
    assert out == "ok"
    assert seen["key"] == "env-file-key"
    assert seen["body_model"] == "custom-model"


def test_complete_explicit_model_wins(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    seen = {}

    def fake_post(cfg, body, timeout):
        seen["body_model"] = body["model"]
        return {"choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}]}

    monkeypatch.setenv("ZAI_API_KEY", "legacy-key")
    monkeypatch.setattr(llm_client, "_post_json", fake_post)
    llm_client.complete("prompt", model="explicit-model")
    assert seen["body_model"] == "explicit-model"


def test_complete_non_dict_200_takes_the_provider_error_path(
        monkeypatch, tmp_path):
    """P3-24: a 200 whose body is a JSON array (a proxy anomaly) must
    raise the readable provider error — the old code called
    data.get("usage") BEFORE the isinstance guard and crashed with
    AttributeError."""
    _isolate(monkeypatch, tmp_path)

    def fake_post(cfg, body, timeout):
        return [{"role": "assistant", "content": "hi"}]

    monkeypatch.setenv("ZAI_API_KEY", "legacy-key")
    monkeypatch.setattr(llm_client, "_post_json", fake_post)
    with pytest.raises(RuntimeError, match="no choices"):
        llm_client.complete("prompt")


def test_complete_truncated_at_cap_comes_back_marked(monkeypatch, tmp_path):
    """The budget is spent and the provider still says finish_reason
    "length": the reply IS cut mid-thought — it must come back visibly
    marked, never as if it were whole."""
    _isolate(monkeypatch, tmp_path)
    scripted = [
        {"choices": [{"message": {"content": "half a thought, cut"},
                      "finish_reason": "length"}]},
        {"choices": [{"message": {"content": "a longer thought, still cut"},
                      "finish_reason": "length"}]},
    ]

    def fake_post(cfg, body, timeout):
        return scripted.pop(0)

    monkeypatch.setenv("ZAI_API_KEY", "legacy-key")
    monkeypatch.setattr(llm_client, "_post_json", fake_post)
    out = llm_client.complete("prompt", max_tokens=16, max_budget=32)
    assert out.endswith("[truncated]"), out
    assert out.startswith("a longer thought"), \
        "the longest (still truncated) attempt is kept under the marker"


def test_complete_short_reply_beats_longer_truncated(monkeypatch, tmp_path):
    """A whole reply wins outright — even a shorter one: plain longest-wins
    preferred the truncated attempt over the complete answer."""
    _isolate(monkeypatch, tmp_path)
    scripted = [
        {"choices": [{"message": {"content": "one two three four five six"},
                      "finish_reason": "length"}]},
        {"choices": [{"message": {"content": "whole"},
                      "finish_reason": "stop"}]},
    ]

    def fake_post(cfg, body, timeout):
        return scripted.pop(0)

    monkeypatch.setenv("ZAI_API_KEY", "legacy-key")
    monkeypatch.setattr(llm_client, "_post_json", fake_post)
    out = llm_client.complete("prompt", max_tokens=16, max_budget=32)
    assert out == "whole", out


def test_zai_hint_only_for_zai_urls(monkeypatch, tmp_path):
    """The 1113/401 doctor hint is Z.ai-specific — a custom provider must
    not be told about the coding endpoint."""
    _isolate(monkeypatch, tmp_path)

    class FakeErr:
        def __init__(self, code, body):
            self.code = code
            self._body = body

        def read(self):
            return self._body

    import urllib.error
    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 400, "nope", {},
                                     FakeErr(0, b'{"error": {"code": 1113}}'))

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ZAI_API_KEY", "k")

    # default (Z.ai) URL: the hint is appended
    with pytest.raises(RuntimeError, match="doctor"):
        llm_client.complete("p", timeout=1)
    assert attempts[-1] == llm_client.API_BASE_DEFAULT + "/chat/completions"

    # custom URL: same wire error, no Z.ai hint
    f = tmp_path / "llm.env"
    f.write_text("LLM_BASE_URL=https://api.openai.com/v1\n"
                 "LLM_MODEL=gpt-4o-mini\n", encoding="utf-8")
    monkeypatch.setattr(llm_client, "ENV_FILE", f)
    with pytest.raises(RuntimeError) as ei:
        llm_client.complete("p", timeout=1)
    assert "doctor" not in str(ei.value)
    assert attempts[-1].startswith("https://api.openai.com/v1/")


# ------------------------------------------------------------ run metering

def test_env_limit_reads_llm_env_after_the_environment(monkeypatch, tmp_path):
    """LLM_MAX_CALLS has two homes (ticket 66): the environment wins,
    llm.env is the second, and garbage in whichever home is consulted
    leaves the decision to the caller (the roster-scaled default) — the
    same semantics the engine subprocess resolves by."""
    monkeypatch.delenv("LLM_MAX_CALLS", raising=False)
    monkeypatch.setattr(llm_client, "ENV_FILE", tmp_path / "absent-llm.env")
    assert llm_client.env_limit() is None
    f = tmp_path / "llm.env"
    f.write_text("LLM_MAX_CALLS=40\n", encoding="utf-8")
    monkeypatch.setattr(llm_client, "ENV_FILE", f)
    assert llm_client.env_limit() == 40
    monkeypatch.setenv("LLM_MAX_CALLS", "48")
    assert llm_client.env_limit() == 48, "the environment beats llm.env"
    monkeypatch.delenv("LLM_MAX_CALLS", raising=False)
    f.write_text("LLM_MAX_CALLS=not-a-number\n", encoding="utf-8")
    assert llm_client.env_limit() is None, "garbage yields to the caller"


def _spendable(monkeypatch, tmp_path):
    """A working provider over a patched urlopen — NOT a stubbed
    _post_json: the run meter lives inside _post_json, so a stub would
    bypass the very metering these tests assert on."""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "llm.env"
    f.write_text("LLM_API_KEY=k\n", encoding="utf-8")
    monkeypatch.setattr(llm_client, "ENV_FILE", f)

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [
                {"message": {"content": "x"}, "finish_reason": "stop"}],
                "usage": {}}).encode()

    monkeypatch.setattr(llm_client.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp())


def test_budget_ceiling_stops_between_calls(monkeypatch, tmp_path):
    """begin_run resets; limit=1 admits one POST; the second raises
    BudgetExceeded before leaving the machine instead of spending without
    a bound."""
    _spendable(monkeypatch, tmp_path)
    llm_client.begin_run(limit=1)
    assert llm_client.complete("a") == "x"
    with pytest.raises(llm_client.BudgetExceeded, match="ceiling"):
        llm_client.complete("b")


def test_failed_posts_are_metered_too(monkeypatch, tmp_path):
    """The regression: the counter only moved on SUCCESS, so failing
    requests (HTTP retries included) spent unbounded while the meter sat
    at zero. Every attempt must count before it is made."""
    import urllib.error
    _spendable(monkeypatch, tmp_path)

    def failing(req, timeout=None):
        raise urllib.error.HTTPError("url", 429, "rate limited", {}, None)

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", failing)
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    llm_client.begin_run(limit=1)
    # attempt 1 meters calls=1 and fails 429; the retry must see the
    # ceiling and raise BEFORE a second request leaves the machine
    with pytest.raises(llm_client.BudgetExceeded, match="ceiling"):
        llm_client.complete("a")
    assert llm_client.run_usage()["calls"] == 1, "the failed attempt counts"


def test_budget_is_thread_local(monkeypatch, tmp_path):
    """Generation jobs run one per campaign on separate threads (per-
    campaign locks do not serialize each other). A shared meter once let
    campaign B's begin_run reset campaign A's ceiling mid-run — spend
    overrun — while A's remaining calls flipped B into a spurious
    BudgetExceeded. Each thread must meter its own run."""
    import threading
    _spendable(monkeypatch, tmp_path)
    llm_client.begin_run(limit=2)
    other = {}

    def campaign_b():
        llm_client.begin_run(limit=99)
        llm_client.complete("a")
        llm_client.complete("b")
        other["usage"] = llm_client.run_usage()

    t = threading.Thread(target=campaign_b)
    t.start()
    t.join()
    assert other["usage"]["calls"] == 2, "B meters its own run"
    assert llm_client.run_usage()["calls"] == 0, \
        "B's begin_run did not reset THIS thread's meter"


def test_post_name_begins_its_own_run_and_maps_budget_to_503(
        client, tmp_spine_root, monkeypatch):
    """The name route is synchronous and never used to begin a run: it
    shared whatever the last job left on the meter and a spent meter made
    it 500 forever. It must begin a fresh run and map BudgetExceeded to a
    readable 503."""
    import loregen
    calls = {"begin": 0}
    monkeypatch.setattr(llm_client, "begin_run",
                        lambda limit=None: calls.__setitem__(
                            "begin", calls["begin"] + 1))

    def boom(*a, **k):
        raise llm_client.BudgetExceeded("per-run LLM ceiling reached (24 calls)")

    monkeypatch.setattr(loregen, "name_candidates", boom)
    r = client.post("/api/campaign/testvale/lore/name",
                    json={"culture": "vesten"})
    assert r.status_code == 503 and "ceiling" in r.json()["error"]
    assert calls["begin"] == 1, "the name route begins its own (fresh) run"
