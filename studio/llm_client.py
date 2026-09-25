"""LLM client for studio generation — the M2 wiring.

Copied PATTERN from agentic-simulator/src/llm.py, per D1: no imports across
projects, the seam is subprocess/HTTP only. Both clients speak the same
OpenAI-compatible /chat/completions shape and read the same llm.env.

Provider config (llm.env at the repo root — see llm.env.example):

    LLM_BASE_URL   the provider's API base (…/v1, …/v4), no /chat/completions
    LLM_API_KEY    the provider key
    LLM_MODEL      the model name (required when the base is not the default)

Precedence: process environment beats llm.env beats the built-in Z.ai
defaults. The legacy key discovery ($ZAI_API_KEY, then the ZCode CLI's
~/.zcode/v2/provider_config.json) still applies when LLM_API_KEY is unset —
the coding endpoint (…/coding/paas/v4) is the default base because the
pay-as-you-go endpoint returns 1113 even when the plan pool is fine (D6).
A custom LLM_BASE_URL turns this into a plain OpenAI-compatible client
(OpenAI, OpenRouter, Ollama, LM Studio — any /chat/completions server).

Self-extending budget: reasoning models burn the token budget on thought
(the API has no unbounded setting), so a budget that runs out mid-thought
returns an empty or mid-sentence answer with finish_reason "length". When
that happens, double the budget and retry, keeping the longest content seen.
Transient HTTP failures (429/5xx) get a short backoff-and-retry; hard HTTP
errors raise RuntimeError carrying the body snippet — plus the Z.ai
endpoint hint only when actually talking to Z.ai. Token usage is logged per
call so cost is never invisible.
"""

import json
import logging
import os
import pathlib
import threading
import time
import urllib.error
import urllib.request

_log = logging.getLogger("studio.llm")

STUDIO_DIR = pathlib.Path(__file__).resolve().parent
ENV_FILE = STUDIO_DIR.parent / "llm.env"

API_BASE_DEFAULT = "https://api.z.ai/api/coding/paas/v4"
MODEL_DEFAULT = "glm-5.3-flash"

_CONFIG = pathlib.Path.home() / ".zcode" / "v2" / "provider_config.json"

# Transient statuses worth a retry: rate limit and gateway churn.
_RETRYABLE = (429, 500, 502, 503, 504)


def read_env_file(path=None):
    """An .env-style file as a dict: KEY=VALUE lines, '#' comments, blank
    lines skipped, surrounding quotes stripped, UTF-8 with or without BOM
    (Notepad). Returns {} when the file is absent — llm.env is optional by
    design; the studio runs read-only without any key."""
    path = pathlib.Path(path or ENV_FILE)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def _find_legacy_key():
    """The pre-llm.env key discovery: $ZAI_API_KEY, else the key in
    ~/.zcode/v2/provider_config.json — rule 1 is the coding-plan key, then
    the rest scanned in order, enc: blobs skipped. '' when nothing fits."""
    if os.environ.get("ZAI_API_KEY"):
        return os.environ["ZAI_API_KEY"]
    if _CONFIG.exists():
        try:
            rules = json.loads(_CONFIG.read_text(encoding="utf-8"))[
                "config"]["providerConfigRules"]["providerRules"]
        except (OSError, ValueError, KeyError, TypeError):
            return ""
        order = list(range(1, len(rules))) + [0]
        for i in order:
            key = ((rules[i].get("config") or {}).get("access") or {}).get("apiKey")
            if key and not key.startswith("enc:"):
                return key
    return ""


def resolve_config(env_file=None):
    """The effective LLM config: {"key", "url", "model"}. Never raises for
    a missing key (complete() turns that into its readable error); raises
    only when a custom base URL has no model — guessing a model name for a
    provider we were never told about would be slop of the config kind."""
    f = read_env_file(env_file)

    def pick(name):
        return (os.environ.get(name) or f.get(name) or "").strip()

    base = pick("LLM_BASE_URL").rstrip("/")
    model = pick("LLM_MODEL")
    if base and base != API_BASE_DEFAULT and not model:
        raise RuntimeError(
            f"LLM_BASE_URL is set to {base}, but LLM_MODEL is not — set "
            "LLM_MODEL in the environment or llm.env (see llm.env.example); "
            "the model name is provider-specific")
    return {
        "key": pick("LLM_API_KEY") or _find_legacy_key(),
        "url": (base or API_BASE_DEFAULT) + "/chat/completions",
        "model": model or MODEL_DEFAULT,
    }


def _post_json(cfg, body, timeout, attempts=3):
    """One POST-and-parse with backoff on transient failures; the config is
    resolved once per call, not per attempt. Raises RuntimeError with a body
    snippet (and, for Z.ai 401/1113, the doctor's hint) — never a bare
    HTTPError."""
    last = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(min(2 ** (attempt - 1), 4))
        req = urllib.request.Request(
            cfg["url"], data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {cfg['key']}",
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
            if "api.z.ai" in cfg["url"] and (e.code == 401 or "1113" in detail):
                hint = (" — run the studio's doctor (GET /api/doctor): the "
                        "coding endpoint needs the coding-plan key, and 1113 "
                        "means the wrong endpoint or an empty plan pool")
            raise RuntimeError(f"HTTP {e.code}: {detail}{hint}") from e
        except urllib.error.URLError as e:
            last = RuntimeError(f"network error (attempt {attempt + 1}): {e.reason}")
            if attempt == attempts - 1:
                raise last from e
    raise last or RuntimeError("unreachable: retry loop exhausted")


class BudgetExceeded(RuntimeError):
    """The per-run call ceiling is used up — callers stop between calls with
    a clear incomplete status instead of burning an unbounded run."""


# Per-run metering: an explicit ceiling on provider CALLS (and an
# approximate token tally for the log). This is a budget guard, not an
# invoice — the numbers are estimates, never the provider's exact charges.
# The ceiling is explicit and configurable; a run that hits it stops
# between calls with an incomplete status.
#
# Thread-local on purpose: generation jobs run one per campaign (per-campaign
# locks do not serialize each other), and a single shared meter let campaign
# B's begin_run() reset campaign A's ceiling mid-run — A's spend went
# unchecked while A's remaining calls flipped B into a spurious
# BudgetExceeded. Each thread meters its own run.
def _env_limit():
    """LLM_MAX_CALLS as a positive int; None when unset OR invalid, so the
    caller applies its own fallback — same semantics as the engine's
    llm.env_limit() (the twins drifted here once: an invalid value gave the
    engine a scaled ceiling and the studio a flat 24)."""
    raw = (os.environ.get("LLM_MAX_CALLS") or "").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return None
_local = threading.local()


def _budget():
    b = getattr(_local, "budget", None)
    if b is None:
        b = {"calls": 0, "tokens": 0, "limit": _env_limit() or 24}
        _local.budget = b
    return b


def begin_run(limit=None):
    """Reset the meter for one logical run (a week, a generation job) — on
    the calling thread."""
    b = _budget()
    b["calls"] = 0
    b["tokens"] = 0
    if limit is not None:
        b["limit"] = max(1, int(limit))


def run_usage():
    """Approximate metering for the run so far: {"calls", "tokens"}."""
    b = _budget()
    return {"calls": b["calls"], "tokens": b["tokens"], "limit": b["limit"]}


def complete(prompt, model=None, temperature=0.8, timeout=300, max_tokens=6144,
             max_budget=24576):
    """Ask the model; on ANY finish_reason == "length", double max_tokens up
    to max_budget (24576) and retry, keeping the longest content seen."""
    cfg = resolve_config()
    if not cfg["key"]:
        raise RuntimeError(
            "no LLM API key found — set LLM_API_KEY in llm.env (copy "
            "llm.env.example) or in the environment; the legacy $ZAI_API_KEY "
            "and ~/.zcode/v2/provider_config.json still work too")
    body = {
        "model": model or cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    best = ""
    while True:
        b = _budget()
        if b["calls"] >= b["limit"]:
            raise BudgetExceeded(
                f"per-run LLM ceiling reached ({b['limit']} calls, "
                f"~{b['tokens']} tokens metered) — run stopped "
                "incomplete; raise LLM_MAX_CALLS or split the work")
        data = _post_json(cfg, body, timeout)
        b["calls"] += 1
        usage = data.get("usage") or {}
        if usage:
            b["tokens"] += int(usage.get("total_tokens") or 0)
            _log.info("llm tokens (run meter ~%d calls / ~%d tokens — "
                      "estimates, not the provider's bill): prompt=%s "
                      "completion=%s", b["calls"], b["tokens"],
                      usage.get("prompt_tokens", "?"),
                      usage.get("completion_tokens", "?"))
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            # a 200 with no choices (content filter, proxy anomaly) must not
            # surface as a bare IndexError job failure
            shape = sorted(data)[:6] if isinstance(data, dict) else type(data).__name__
            raise RuntimeError(
                f"provider returned no choices (content filter or proxy "
                f"anomaly?); response keys: {shape}")
        choice = choices[0]
        content = choice["message"].get("content") or ""
        if len(content.strip()) > len(best.strip()):
            best = content
        if choice.get("finish_reason") != "length" or body["max_tokens"] >= max_budget:
            return best
        body["max_tokens"] = min(max_budget, body["max_tokens"] * 2)
