"""LLM client for studio generation — the M2 wiring (dormant until then).

Copied PATTERN from agentic-simulator/src/llm.py, per D1: no imports across
projects, the seam is subprocess/HTTP only. Same key discovery and the same
coding endpoint (the pay-as-you-go endpoint returns 1113 even when the plan
pool is fine — D6).

Self-extending budget: glm-5.3-flash reasons inside the token budget (the
API has no unbounded setting), so a budget that runs out mid-thought returns
an empty or mid-sentence answer with finish_reason "length". When that
happens, double the budget and retry, keeping the longest content seen.
"""

import json
import os
import pathlib
import urllib.request

API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
MODEL = "glm-5.3-flash"

_CONFIG = pathlib.Path.home() / ".zcode" / "v2" / "provider_config.json"


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
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {_find_key()}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if len(content.strip()) > len(best.strip()):
            best = content
        if choice.get("finish_reason") != "length" or body["max_tokens"] >= max_budget:
            return best
        body["max_tokens"] = min(max_budget, body["max_tokens"] * 2)
