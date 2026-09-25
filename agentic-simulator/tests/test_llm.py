"""Tests for the LLM client — no network: _post_json/urlopen are faked.

Zero dependencies — run directly:  python tests/test_llm.py
"""

import io
import contextlib
import json
import os
import sys
import tempfile
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


def test_budget_ceiling_stops_between_calls():
    """The per-run ceiling: begin_run resets, limit=1 admits one call, the
    second raises BudgetExceeded instead of spending without a bound."""
    saved_post, saved_budget = llm._post_json, dict(llm._BUDGET)
    saved_key = os.environ.get("LLM_API_KEY")
    os.environ["LLM_API_KEY"] = "test-key"

    def fake_post(cfg, body, timeout, attempts=3):
        return response("ok")

    llm._post_json = fake_post
    try:
        llm.begin_run(limit=1)
        assert llm.complete("prompt one") == "ok"
        try:
            llm.complete("prompt two")
        except llm.BudgetExceeded:
            pass
        else:
            raise AssertionError("the second call must hit the ceiling")
        llm.begin_run(limit=2)
        llm.complete("a")
        llm.complete("b")
        usage = llm.run_usage()
        assert usage["calls"] == 2, "the meter counts every POST"
    finally:
        llm._post_json = saved_post
        llm._BUDGET.clear()
        llm._BUDGET.update(saved_budget)
        if saved_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = saved_key
    print("  budget: begin_run resets, the ceiling stops between calls  OK")


def test_budget_is_rechecked_on_length_retries():
    """The ceiling must be re-checked before EVERY POST: a finish_reason
    'length' retry loop used to be unmetered and could spend past it."""
    saved_post, saved_budget = llm._post_json, dict(llm._BUDGET)
    saved_key = os.environ.get("LLM_API_KEY")
    os.environ["LLM_API_KEY"] = "test-key"
    seq = [response("half a thought", finish="length"),
           response("a complete thought at last", finish="stop")]
    calls = []

    def fake_post(cfg, body, timeout, attempts=3):
        calls.append(body["max_tokens"])
        return seq[min(len(calls), len(seq)) - 1]

    llm._post_json = fake_post
    try:
        llm.begin_run(limit=1)
        # the first POST returns finish=length; the retry must see calls
        # (1) >= limit (1) and raise, never POST again
        llm.complete("prompt", max_tokens=16, max_budget=64)
    except llm.BudgetExceeded:
        assert len(calls) == 1, "stopped between calls, having spent one"
    else:
        raise AssertionError("the length retry must hit the ceiling at limit=1")
    finally:
        llm._post_json = saved_post
        llm._BUDGET.clear()
        llm._BUDGET.update(saved_budget)
        if saved_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = saved_key
    print("  budget: length-extension retries are metered too  OK")


def test_empty_choices_is_a_readable_error_not_indexerror():
    saved_post, saved_budget = llm._post_json, dict(llm._BUDGET)
    saved_key = os.environ.get("LLM_API_KEY")
    os.environ["LLM_API_KEY"] = "test-key"
    llm._post_json = lambda cfg, body, timeout, attempts=3: {"choices": []}
    try:
        llm.begin_run(limit=4)
        llm.complete("prompt")
    except RuntimeError as e:
        assert "no choices" in str(e), str(e)
    else:
        raise AssertionError("an empty choices list must raise, not IndexError")
    finally:
        llm._post_json = saved_post
        llm._BUDGET.clear()
        llm._BUDGET.update(saved_budget)
        if saved_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = saved_key
    print("  provider: empty choices -> readable RuntimeError  OK")


def test_legacy_key_scan_prefers_the_coding_plan_rule():
    """Same order as studio/llm_client: rule 1 (the coding-plan key the
    default endpoint needs) first, rule 0 last — both sides must pick the
    SAME key from one provider_config.json."""
    fake_home = pathlib.Path(tempfile.mkdtemp(prefix="llm-home-"))
    cfg_dir = fake_home / ".zcode" / "v2"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "provider_config.json").write_text(json.dumps({
        "config": {"providerConfigRules": {"providerRules": [
            {"config": {"access": {"apiKey": "rule-zero-key"}}},
            {"config": {"access": {"apiKey": "rule-one-key"}}},
        ]}}}), encoding="utf-8")
    saved_home = pathlib.Path.home
    pathlib.Path.home = staticmethod(lambda: fake_home)
    try:
        assert llm._find_key() == "rule-one-key", \
            "rule 0 is scanned last (studio parity)"
    finally:
        pathlib.Path.home = saved_home
    print("  config: legacy key scan order matches the studio  OK")


def test_env_file_points_at_the_repo_root():
    """The seam fix: the engine reads the SAME llm.env as the studio — the
    repo root, next to llm.env.example — not this directory's own file."""
    assert llm.ENV_FILE == llm.ROOT.parent / "llm.env", llm.ENV_FILE
    assert llm.read_env_file(llm.ROOT.parent / "no-such-llm.env") == {}, \
        "a missing llm.env is {} (optional by design)"
    print("  config: ENV_FILE is the repo root, shared with the studio  OK")


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


def test_propose_budget_exceeded_is_neither_retried_nor_laundered():
    """BudgetExceeded must escape propose() with its type intact and no
    retry: the ceiling is re-checked before every POST, so a retry can only
    meet it again — and run_live's abort-the-week path depends on the type
    surviving this loop instead of becoming 'failed twice: BudgetExceeded'
    (which the old all-holds-canon behavior swallowed per faction)."""
    calls = []
    def spent(prompt):
        calls.append(prompt)
        raise llm.BudgetExceeded("per-run LLM ceiling reached (24 calls)")
    saved = llm.complete
    llm.complete = spent
    try:
        try:
            propose(view(), "t", "me")
            raise AssertionError("expected BudgetExceeded")
        except llm.BudgetExceeded:
            pass
        except RuntimeError as e:
            raise AssertionError(
                f"the budget type was laundered into {type(e).__name__}: {e}")
    finally:
        llm.complete = saved
    assert len(calls) == 1, f"no retry after budget exhaustion; got {len(calls)}"
    print("  budget: propose neither retries nor launders exhaustion  OK")


def test_env_limit_unset_or_garbage_yields_to_the_caller():
    saved = os.environ.get("LLM_MAX_CALLS")
    try:
        os.environ.pop("LLM_MAX_CALLS", None)
        assert llm.env_limit() is None, "unset means 'caller decides'"
        os.environ["LLM_MAX_CALLS"] = "not-a-number"
        assert llm.env_limit() is None, "garbage means 'caller decides'"
        os.environ["LLM_MAX_CALLS"] = "7"
        assert llm.env_limit() == 7
        os.environ["LLM_MAX_CALLS"] = "0"
        assert llm.env_limit() == 1, "clamped positive, as before"
    finally:
        if saved is None:
            os.environ.pop("LLM_MAX_CALLS", None)
        else:
            os.environ["LLM_MAX_CALLS"] = saved
    print("  budget: env_limit parses LLM_MAX_CALLS or yields  OK")


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
    saved_urlopen, saved_sleep = llm.urllib.request.urlopen, llm.time.sleep
    llm.urllib.request.urlopen = fake_urlopen
    llm.time.sleep = lambda s: None
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            data = llm._post_json({"key": "test-key", "url": llm.API_URL}, {}, 5)
    finally:
        llm.urllib.request.urlopen, llm.time.sleep = saved_urlopen, saved_sleep
    assert len(attempts) == 3 and data["choices"][0]["message"]["content"] == "ok"
    print("  post: transient 429 retried with backoff  OK")


def test_post_json_hard_errors_become_readable_runtimeerrors():
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    saved_urlopen = llm.urllib.request.urlopen
    llm.urllib.request.urlopen = fake_urlopen
    try:
        try:
            llm._post_json({"key": "test-key", "url": llm.API_URL}, {}, 5)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "401" in str(e), f"readable error, got: {e}"
    finally:
        llm.urllib.request.urlopen = saved_urlopen
    print("  post: hard HTTP errors raise readable RuntimeError  OK")


def test_complete_budget_doubling_keeps_longest():
    scripted = [
        response("half a thou", finish="length"),
        response("the full answer, finally"),
    ]
    seen_tokens = []
    def fake_post(cfg, body, timeout):
        seen_tokens.append(body["max_tokens"])
        return scripted.pop(0)
    saved_post, saved_resolve = llm._post_json, llm.resolve_config
    llm._post_json = fake_post
    llm.resolve_config = lambda env_file=None: {
        "key": "k", "url": llm.API_URL, "model": llm.MODEL}
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            out = llm.complete("prompt", max_tokens=16, max_budget=32)
    finally:
        llm._post_json, llm.resolve_config = saved_post, saved_resolve
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
        assert llm._find_key() == "", "no key: empty string, never a raise"
    finally:
        pathlib.Path.home = saved_home
        if saved is not None:
            os.environ["ZAI_API_KEY"] = saved
    print("  key: env wins; config walk skips enc:; missing key is empty  OK")


def test_resolve_config_precedence_and_custom_base_error():
    import os
    import tempfile
    saved = {k: os.environ.pop(k, None) for k in
             ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "ZAI_API_KEY")}
    empty_env = pathlib.Path(tempfile.mkdtemp()) / "absent.env"
    saved_home = pathlib.Path.home
    pathlib.Path.home = classmethod(
        lambda cls: pathlib.Path(tempfile.mkdtemp()))  # no ZCode config either
    try:
        cfg = llm.resolve_config(env_file=empty_env)
        assert cfg["url"] == llm.API_URL and cfg["model"] == llm.MODEL, \
            "defaults are the Z.ai coding endpoint"
        assert cfg["key"] == "", "no key anywhere -> empty, complete() explains"

        env_file = pathlib.Path(tempfile.mkdtemp()) / "llm.env"
        env_file.write_text(
            '# comment\nLLM_BASE_URL="http://localhost:11434/v1"\n'
            "LLM_API_KEY=file-key\nLLM_MODEL=file-model\n\n", encoding="utf-8")
        cfg = llm.resolve_config(env_file=env_file)
        assert cfg["url"] == "http://localhost:11434/v1/chat/completions"
        assert cfg["key"] == "file-key"

        env_file.write_text(
            'LLM_BASE_URL="http://localhost:11434/v1"\n'
            "LLM_API_KEY=file-key\n", encoding="utf-8")
        try:
            llm.resolve_config(env_file=env_file)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "LLM_MODEL" in str(e), "custom base without a model refuses"

        os.environ["LLM_MODEL"] = "llama3.1"
        os.environ["LLM_API_KEY"] = "env-key"
        cfg = llm.resolve_config(env_file=env_file)
        assert cfg["model"] == "llama3.1" and cfg["key"] == "env-key", \
            "environment beats the file"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("  config: env > llm.env > defaults; custom base demands a model  OK")


if __name__ == "__main__":
    for fn in [
        test_extract_yaml_from_fences,
        test_validate_clamps_and_canonicalizes_target,
        test_validate_accepts_district_targets,
        test_propose_retries_then_succeeds,
        test_propose_fails_loudly_twice,
        test_propose_budget_exceeded_is_neither_retried_nor_laundered,
        test_env_limit_unset_or_garbage_yields_to_the_caller,
        test_render_prompt_fills_placeholders,
        test_post_json_retries_transient_http_errors,
        test_post_json_hard_errors_become_readable_runtimeerrors,
        test_complete_budget_doubling_keeps_longest,
        test_budget_ceiling_stops_between_calls,
        test_budget_is_rechecked_on_length_retries,
        test_empty_choices_is_a_readable_error_not_indexerror,
        test_find_key_env_wins_and_fallback_walks_rules,
        test_legacy_key_scan_prefers_the_coding_plan_rule,
        test_resolve_config_precedence_and_custom_base_error,
        test_env_file_points_at_the_repo_root,
    ]:
        fn()
    print("all llm tests passed")
