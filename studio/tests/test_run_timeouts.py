"""Ticket 55 — one honest week-run time contract. The watchdogs, the
queue cap and the goal-review sizing derive from ONE worst-case formula
(the engine's own llm.complete() numbers), so no cap contradicts another
and no env knob can push the watchdog under the legal worst case. These
tests pin the relationships; studio/tests/js/test_run_timeouts.cjs pins
the frontend poll that watches runs sized by the same numbers.
"""

import engines
from routers import simulation


def test_llm_call_worst_case_is_the_engine_formula():
    # complete()'s own numbers: 3 HTTP attempts x 300 s x 3 length passes
    # (base + two max_tokens doublings) + (1 s + 2 s) backoff per pass —
    # the "~2700 s" the goal-review sizing comment always derived
    assert engines.LLM_HTTP_ATTEMPTS == 3
    assert engines.LLM_POST_TIMEOUT_S == 300
    assert engines.LLM_LENGTH_PASSES == 3
    assert engines.llm_call_worst_case_s() == 3 * 300 * 3 + 9


def test_goal_review_watchdog_is_the_shared_formula():
    # the old literal 2800 is now DERIVED beside the week cap, not
    # hardcoded beside it — and it never sits under the worst case
    assert engines.llm_call_watchdog_s() == 2800
    assert simulation._GOAL_REVIEW_TIMEOUT == engines.llm_call_watchdog_s()
    assert simulation._GOAL_REVIEW_TIMEOUT >= engines.llm_call_worst_case_s()


def test_week_watchdog_covers_one_llm_call_worst_case():
    # the acceptance bar (ticket 55): a whole multi-faction week must
    # never get LESS headroom than ONE complete() call can legally spend
    assert engines.WEEK_TIMEOUT >= engines.llm_call_watchdog_s()


def test_week_timeout_derives_from_the_engine_post_ceiling(monkeypatch):
    monkeypatch.delenv("STUDIO_WEEK_TIMEOUT_S", raising=False)
    monkeypatch.delenv("LLM_MAX_CALLS", raising=False)
    # tick.py run_live's own ceiling: max(24, 3N + 6) POSTs on the
    # design-form full roster (five factions — "Five factions is plenty")
    assert engines.week_post_budget() == max(24, 3 * 5 + 6) == 24
    # the watchdog is that ceiling x the per-POST timeout + engine-work
    # margin: any week that can legally FINISH fits under it; a week
    # that would spend past the ceiling aborts by budget, not by kill
    assert engines.week_timeout_s() == \
        24 * engines.LLM_POST_TIMEOUT_S + engines.WEEK_ENGINE_MARGIN_S


def test_week_timeout_env_knob(monkeypatch):
    # STUDIO_WEEK_TIMEOUT_S is the one knob (a positive int wins)
    monkeypatch.setenv("STUDIO_WEEK_TIMEOUT_S", "1200")
    assert engines.week_timeout_s() == 1200
    # a non-numeric or non-positive value falls back to the derivation
    monkeypatch.setenv("STUDIO_WEEK_TIMEOUT_S", "nope")
    assert engines.week_timeout_s() == \
        engines.week_post_budget() * engines.LLM_POST_TIMEOUT_S \
        + engines.WEEK_ENGINE_MARGIN_S
    monkeypatch.setenv("STUDIO_WEEK_TIMEOUT_S", "0")
    assert engines.week_timeout_s() > 0


def test_week_timeout_follows_the_engine_call_ceiling(monkeypatch):
    # the engine subprocess inherits this process's env, so a raised
    # LLM_MAX_CALLS raises the honest wall-time bound with it — the cap
    # and the budget must never disagree
    monkeypatch.delenv("STUDIO_WEEK_TIMEOUT_S", raising=False)
    monkeypatch.setenv("LLM_MAX_CALLS", "48")
    assert engines.week_post_budget() == 48
    assert engines.week_timeout_s() == \
        48 * engines.LLM_POST_TIMEOUT_S + engines.WEEK_ENGINE_MARGIN_S


def test_queue_deadline_stays_derived_from_the_watchdog():
    # the queued job's dead-man cap is 3x the watchdog-sized run: the old
    # 93-minute queue for a 30-minute kill was the original absurdity,
    # and the pair must keep deriving together
    assert engines.engine_lock_deadline_s() == \
        3 * (engines.WEEK_TIMEOUT + 60)
    assert engines.engine_lock_deadline_s() > engines.WEEK_TIMEOUT


def test_get_simulation_serves_the_run_contract(client, tmp_spine_root):
    # the UI sizes its poll from the SERVER's numbers (ticket 55), not a
    # third guess — the contract carries the watchdog and the queue cap
    r = client.get("/api/campaign/testvale/simulation")
    assert r.status_code == 200, r.text
    contract = r.json()["run_contract"]
    assert contract["week_timeout_s"] == engines.WEEK_TIMEOUT
    assert contract["week_timeout_s"] >= engines.llm_call_watchdog_s()
    assert contract["queue_deadline_s"] == engines.engine_lock_deadline_s()
