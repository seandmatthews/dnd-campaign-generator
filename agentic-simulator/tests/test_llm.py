"""Tests for the LLM client — no network: _post_json/urlopen are faked.

Zero dependencies — run directly:  python tests/test_llm.py
"""

import io
import contextlib
import json
import sys
import urllib.error
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import llm
from llm import _extract_yaml, _validate, propose, render_prompt


def response(content, finish="stop", usage=None):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


def view(resources=None, others=(("rival", "House Rival"),), districts=("the Docks",)):
    return {
        "week": 4,
        "city": {"name": "Testburg", "districts": list(districts)},
        "you": {"name": "House Me", "resources": resources or {"coin": "medium", "muscle": "low", "legitimacy": "high"}},
        "others": [{"id": fid, "name": name, "kind": "test",
                    "apparent_strength": "strong", "posture_toward_you": "cold"}
                   for fid, name in others],
    }


def test_extract_yaml_from_fences():
    assert _extract_yaml("```yaml\naction: x\n```") == {"action": "x"}
    assert _extract_yaml("```json\n{\"action\": \"x\"}\n```") == {"action": "x"}, \
        "a json fence parses too"
    assert _extract_yaml("action: bare") == {"action": "bare"}
    print("  extract: yaml/json fences and bare YAML all parse  OK")


def test_validate_clamps_and_canonicalizes_target():
    p = _validate({
        "action": "do a thing",
        "target": "RIVAL",
        "resources_committed": {"coin": "high", "muscle": "high", "glory": "high"},
        "risk_acceptance": "absurd",
    }, "me", {"coin": "low", "muscle": "low", "legitimacy": "high"},
        known_ids=["me", "rival", "world"])
    assert p["target"] == "rival", "case-insensitive id match canonicalizes"
    assert p["resources_committed"] == {"coin": "low", "muscle": "low"}, \
        "clamped to actual stock; unknown resource keys dropped"
    assert p["risk_acceptance"] == "medium", "invalid risk folds to medium"
    assert p["actor"] == "me", "actor is forced, never model-chosen"
    print("  validate: clamp to stock, canonical target, forced actor  OK")


def test_validate_accepts_district_targets():
    p = _validate({"action": "patrol", "target": "The docks"}, "me", None,
                  known_ids=["me"], districts=["the Docks"])
    assert p["target"] == "the Docks", "district case-insensitive match canonicalizes"
    print("  validate: district targets canonicalize  OK")


def test_propose_retries_then_succeeds():
    calls = []
    def flaky(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return "```yaml\naction: bribe someone\ntarget: rival\nrisk_acceptance: low\n```"
    saved = llm.complete
    llm.complete = flaky
    try:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            p = propose(view(), "template {{FACTION_VIEW}}", "me")
    finally:
        llm.complete = saved
    assert p["action"] == "bribe someone" and p["actor"] == "me"
    assert len(calls) == 2, "one retry after the transient failure"
    assert "attempt 1 failed" in buf.getvalue(), "the first failure is logged, not swallowed"
    print("  propose: transient failure logged, retry succeeds  OK")


def test_propose_fails_loudly_twice():
    def dead(prompt):
        raise RuntimeError("api down")
    saved = llm.complete
    llm.complete = dead
    try:
        try:
            propose(view(), "t", "me")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "failed twice" in str(e)
    finally:
        llm.complete = saved
    print("  propose: double failure raises with context  OK")


def test_render_prompt_fills_placeholders():
    out = render_prompt("You are {{FACTION_NAME}} — {{LEADER}}:\n{{FACTION_VIEW}}",
                        view())
    assert "House Me" in out and "the leadership of House Me" in out
    assert "{{" not in out and "apparent_strength" in out, "the view is embedded"
    print("  render: placeholders filled, view embedded  OK")


def test_post_json_retries_transient_http_errors():
    attempts = []
    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(response("ok")).encode()
    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.HTTPError("url", 429, "rate limited", {}, None)
        return FakeResp()
    saved_urlopen, saved_sleep, saved_key = llm.urllib.request.urlopen, llm.time.sleep, llm._find_key
    llm.urllib.request.urlopen = fake_urlopen
    llm.time.sleep = lambda s: None
    llm._find_key = lambda: "test-key"
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            data = llm._post_json({}, 5)
    finally:
        llm.urllib.request.urlopen, llm.time.sleep, llm._find_key = saved_urlopen, saved_sleep, saved_key
    assert len(attempts) == 3 and data["choices"][0]["message"]["content"] == "ok"
    print("  post: transient 429 retried with backoff  OK")


def test_post_json_hard_errors_become_readable_runtimeerrors():
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    saved_urlopen, saved_key = llm.urllib.request.urlopen, llm._find_key
    llm.urllib.request.urlopen = fake_urlopen
    llm._find_key = lambda: "test-key"
    try:
        try:
            llm._post_json({}, 5)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "401" in str(e), f"readable error, got: {e}"
    finally:
        llm.urllib.request.urlopen, llm._find_key = saved_urlopen, saved_key
    print("  post: hard HTTP errors raise readable RuntimeError  OK")


def test_complete_budget_doubling_keeps_longest():
    scripted = [
        response("half a thou", finish="length"),
        response("the full answer, finally"),
    ]
    seen_tokens = []
    def fake_post(body, timeout):
        seen_tokens.append(body["max_tokens"])
        return scripted.pop(0)
    saved = llm._post_json
    llm._post_json = fake_post
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            out = llm.complete("prompt", max_tokens=16, max_budget=32)
    finally:
        llm._post_json = saved
    assert out == "the full answer, finally", "the longest answer wins"
    assert seen_tokens == [16, 32], "budget doubled once, capped at max_budget"
    print("  complete: length-retry doubles budget, keeps longest  OK")


def test_find_key_env_wins_and_fallback_walks_rules():
    import os
    import tempfile
    saved = os.environ.get("ZAI_API_KEY")
    os.environ["ZAI_API_KEY"] = "env-key"
    try:
        assert llm._find_key() == "env-key", "$ZAI_API_KEY wins"
    finally:
        if saved is None:
            del os.environ["ZAI_API_KEY"]
        else:
            os.environ["ZAI_API_KEY"] = saved

    home = pathlib.Path(tempfile.mkdtemp())
    cfg_path = home / ".zcode" / "v2" / "provider_config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({
        "config": {"providerConfigRules": {"providerRules": [
            {"config": {"access": {"apiKey": "enc:encrypted-blob"}}},
            {"config": {"access": {"apiKey": "plain-key"}}},
        ]}}
    }), encoding="utf-8")
    saved_home = pathlib.Path.home
    pathlib.Path.home = classmethod(lambda cls: home)
    try:
        assert llm._find_key() == "plain-key", "config walk skips enc: keys"
    finally:
        pathlib.Path.home = saved_home

    empty = pathlib.Path(tempfile.mkdtemp())
    pathlib.Path.home = classmethod(lambda cls: empty)
    os.environ.pop("ZAI_API_KEY", None)
    try:
        try:
            llm._find_key()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "no Z.ai API key" in str(e)
    finally:
        pathlib.Path.home = saved_home
        if saved is not None:
            os.environ["ZAI_API_KEY"] = saved
    print("  key: env wins; config walk skips enc:; missing key explains itself  OK")


if __name__ == "__main__":
    for fn in [
        test_extract_yaml_from_fences,
        test_validate_clamps_and_canonicalizes_target,
        test_validate_accepts_district_targets,
        test_propose_retries_then_succeeds,
        test_propose_fails_loudly_twice,
        test_render_prompt_fills_placeholders,
        test_post_json_retries_transient_http_errors,
        test_post_json_hard_errors_become_readable_runtimeerrors,
        test_complete_budget_doubling_keeps_longest,
        test_find_key_env_wins_and_fallback_walks_rules,
    ]:
        fn()
    print("all llm tests passed")
