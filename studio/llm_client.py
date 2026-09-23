"""LLM client for studio generation — the M2 wiring.

Copied PATTERN from agentic-simulator/src/llm.py, per D1: no imports across
projects, the seam is subprocess/HTTP only. Same key discovery and the same
coding endpoint (the pay-as-you-go endpoint returns 1113 even when the plan
pool is fine — D6).

Self-extending budget: glm-5.3-flash reasons inside the token budget (the
API has no unbounded setting), so a budget that runs out mid-thought returns
an empty or mid-sentence answer with finish_reason "length". When that
happens, double the budget and retry, keeping the longest content seen.
Transient HTTP failures (429/5xx) get a short backoff-and-retry; hard HTTP
errors raise RuntimeError carrying the body snippet plus the doctor's
pointer — a bare HTTPError in a job log tells nobody anything. Token usage
is printed per call so cost is never invisible.
"""

import json
import logging
import os
import pathlib
import time
import urllib.error
import urllib.request

_log = logging.getLogger("studio.llm")

API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
MODEL = "glm-5.3-flash"

_CONFIG = pathlib.Path.home() / ".zcode" / "v2" / "provider_config.json"

# Transient statuses worth a retry: rate limit and gateway churn.
_RETRYABLE = (429, 500, 502, 503, 504)


def _find_key():
    """$ZAI_API_KEY, else the key in ~/.zcode/v2/provider_config.json —
    rule 1 is the coding-plan key, then scan the rest, skipping enc: blobs."""
    if os.environ.get("ZAI_API_KEY"):
        return os.environ["ZAI_API_KEY"]
    if _CONFIG.exists():
        rules = json.loads(_CONFIG.read_text(encoding="utf-8"))[
            "config"]["providerConfigRules"]["providerRules"]
        order = list(range(1, len(rules))) + [0]
        for i in order:
            key = ((rules[i].get("config") or {}).get("access") or {}).get("apiKey")
            if key and not key.startswith("enc:"):
                return key
    raise RuntimeError(
        "no Z.ai API key found; set $ZAI_API_KEY or install one in "
        "~/.zcode/v2/provider_config.json"
    )


def _post_json(body, timeout, attempts=3):
    """One POST-and-parse with backoff on transient failures; the key is
    looked up once per call, not per attempt. Raises RuntimeError with a
    body snippet (and, for 401/1113, the doctor's hint) — never a bare
    HTTPError."""
    key = _find_key()
    last = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(min(2 ** (attempt - 1), 4))
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300].strip()
            except Exception:  # noqa: BLE001 — some HTTPErrors carry no body
                detail = ""
            if e.code in _RETRYABLE and attempt < attempts - 1:
                last = RuntimeError(f"HTTP {e.code}, retrying "
                                    f"(attempt {attempt + 1}): {detail}")
                continue
            hint = ""
            if e.code == 401 or "1113" in detail:
                hint = (" — run the studio's doctor (GET /api/doctor): the "
                        "coding endpoint needs the coding-plan key, and 1113 "
                        "means the wrong endpoint or an empty plan pool")
            raise RuntimeError(f"HTTP {e.code}: {detail}{hint}") from e
        except urllib.error.URLError as e:
            last = RuntimeError(f"network error (attempt {attempt + 1}): {e.reason}")
            if attempt == attempts - 1:
                raise last from e
    raise last or RuntimeError("unreachable: retry loop exhausted")


def complete(prompt, model=MODEL, temperature=0.8, timeout=300, max_tokens=6144,
             max_budget=24576):
    """Ask the model; on ANY finish_reason == "length", double max_tokens up
    to max_budget (24576) and retry, keeping the longest content seen."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    best = ""
    while True:
        data = _post_json(body, timeout)
        usage = data.get("usage") or {}
        if usage:
            _log.info("llm tokens: prompt=%s completion=%s",
                      usage.get("prompt_tokens", "?"),
                      usage.get("completion_tokens", "?"))
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if len(content.strip()) > len(best.strip()):
            best = content
        if choice.get("finish_reason") != "length" or body["max_tokens"] >= max_budget:
            return best
        body["max_tokens"] = min(max_budget, body["max_tokens"] * 2)
