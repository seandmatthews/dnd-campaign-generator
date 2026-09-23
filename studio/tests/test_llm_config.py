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
