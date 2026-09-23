"""LLM provider for faction proposals — the M1 wiring.

One job per call: hand an agent its fog-of-war view rendered through the
faction prompt template, get back one structured proposal. The model never
sees the world state, only its view, and never resolves anything.

Key discovery order: $ZAI_API_KEY, then the key stored in
~/.zcode/v2/provider_config.json (ZCode's Z.ai coding-plan key).
"""

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

import yaml

API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
MODEL = "glm-5.3-flash"

_ROOT = pathlib.Path(__file__).resolve().parent.parent
VALID_RESOURCES = ("coin", "muscle", "legitimacy")
VALID_LEVELS = ("none", "low", "medium", "high")
_LEVEL_INDEX = {lvl: i for i, lvl in enumerate(VALID_LEVELS)}
VALID_RISK = ("low", "medium", "high")

# Transient HTTP statuses worth a retry: rate limit and gateway churn.
_RETRYABLE = (429, 500, 502, 503, 504)


def _find_key():
    if os.environ.get("ZAI_API_KEY"):
        return os.environ["ZAI_API_KEY"]
    cfg = pathlib.Path.home() / ".zcode" / "v2" / "provider_config.json"
    if cfg.exists():
        data = json.loads(cfg.read_text(encoding="utf-8"))
        for rule in data.get("config", {}).get("providerConfigRules", {}).get("providerRules", []):
            key = rule.get("config", {}).get("access", {}).get("apiKey")
            if key and not key.startswith("enc:"):
                return key
    raise RuntimeError(
        "no Z.ai API key found; set $ZAI_API_KEY or install one in "
        "~/.zcode/v2/provider_config.json"
    )


def _post_json(body, timeout, attempts=3):
    """POST and parse one completion response, retrying transient failures
    with a short backoff. The key is looked up once per call, not per
    attempt. Hard HTTP errors raise RuntimeError carrying a body snippet —
    never a bare HTTPError."""
    key = _find_key()
    last = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(min(2 ** (attempt - 1), 4))
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300].strip()
            except Exception:  # noqa: BLE001 — some HTTPErrors carry no body
                detail = ""
            if e.code in _RETRYABLE and attempt < attempts - 1:
                last = RuntimeError(f"HTTP {e.code}, retrying (attempt {attempt + 1}): {detail}")
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            last = RuntimeError(f"network error (attempt {attempt + 1}): {e.reason}")
            if attempt == attempts - 1:
                raise last from e
    raise last or RuntimeError("unreachable: retry loop exhausted")


def complete(prompt, model=MODEL, temperature=0.8, timeout=300, max_tokens=6144,
             max_budget=24576):
    """Ask the model, letting it think as long as it needs: glm-5.3-flash
    reasons inside the token budget (the API has no unbounded setting), so a
    budget that runs out mid-thought returns an empty answer — or a
    mid-sentence one — with finish_reason "length". When that happens,
    double the budget and retry, keeping the longest answer seen. Token
    usage is printed to stderr per call so cost is never invisible."""
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
            print(f"  [llm] tokens: prompt={usage.get('prompt_tokens', '?')} "
                  f"completion={usage.get('completion_tokens', '?')}", file=sys.stderr)
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if len(content.strip()) > len(best.strip()):
            best = content
        if choice.get("finish_reason") != "length" or body["max_tokens"] >= max_budget:
            return best
        body["max_tokens"] = min(max_budget, body["max_tokens"] * 2)


def render_prompt(template_text, faction):
    """Fill the faction.md template placeholders from a fog-of-war view."""
    name = faction["you"]["name"]
    return (
        template_text
        .replace("{{FACTION_NAME}}", name)
        .replace("{{LEADER}}", f"the leadership of {name}")
        .replace("{{FACTION_VIEW}}", yaml.safe_dump(faction, sort_keys=False, allow_unicode=True))
    )


def _extract_yaml(text):
    match = re.search(r"```(?:ya?ml|json)?\s*(.*?)```", text, re.DOTALL)
    block = match.group(1) if match else text
    return yaml.safe_load(block)


def _validate(p, actor_id, actual_resources=None, known_ids=(), districts=()):
    if not isinstance(p, dict) or not isinstance(p.get("action"), str) or not p["action"].strip():
        raise ValueError("missing or empty 'action'")
    # target: accept id/name/district in any case; canonicalize exact
    # case-insensitive matches so the resolver sees real ids
    target = str(p.get("target") or "self").strip()
    low = target.lower()
    for fid in known_ids:
        if low == str(fid).lower():
            target = str(fid)
            break
    else:
        for dname in districts:
            if low == str(dname).lower():
                target = str(dname)
                break
    p["target"] = target
    rc = p.get("resources_committed") or {}
    if not isinstance(rc, dict):
        rc = {}
    # you cannot commit what you do not have: clamp to the faction's actual stock
    clamped = {}
    for k, v in rc.items():
        if k not in VALID_RESOURCES:
            continue
        level = v if v in VALID_LEVELS else "low"
        if actual_resources:
            have = _LEVEL_INDEX.get(actual_resources.get(k, "none"), 0)
            want = _LEVEL_INDEX.get(level, 0)
            level = VALID_LEVELS[min(want, have)]
        if level != "none":
            clamped[k] = level
    p["resources_committed"] = clamped
    if p.get("risk_acceptance") not in VALID_RISK:
        p["risk_acceptance"] = "medium"
    p["actor"] = actor_id
    return p


def propose(view, template_text, actor_id):
    """Ask the model for this faction's weekly proposal; validate; one retry.
    A target matching no faction id or district warns (it will resolve
    uncontested) instead of silently degrading."""
    prompt = render_prompt(template_text, view)
    actual = view["you"]["resources"]
    known_ids = [o.get("id") for o in view.get("others", [])] + [actor_id, "world"]
    districts = [str(d) for d in (view.get("city", {}) or {}).get("districts", [])]
    last_error = None
    for attempt in range(2):
        try:
            raw = complete(prompt + ("\n\nReply with the YAML block ONLY." if attempt else ""))
            p = _validate(_extract_yaml(raw), actor_id, actual, known_ids, districts)
            if p["target"] not in known_ids and p["target"] not in districts \
                    and p["target"] != "self":
                print(f"  [llm] {actor_id}: target {p['target']!r} matches no "
                      f"faction id or district — it will resolve uncontested",
                      file=sys.stderr)
            return p
        except Exception as e:  # noqa: BLE001 — network and parse failures both retry once
            last_error = e
            print(f"  [llm] {actor_id} attempt {attempt + 1} failed: {e}", file=sys.stderr)
    raise RuntimeError(f"proposal for {actor_id} failed twice: {last_error}")


def narrate_week(city, week, outcomes, state_summary):
    """The chronicle's prose section — constrained to what the state backs."""
    ledger = "\n".join(
        f"- week {o.get('week', week)} / {o['actor']}: "
        f"{'SUCCEEDED' if o['success'] else 'FAILED'}"
        f"{' (contested vs ' + o['vs'] + ')' if o.get('contested') else ''}"
        f", margin {o['margin']:+d} — \"{o['action']}\""
        for o in outcomes
    )
    prompt = f"""You are the chronicler of {city}. Write 250-350 words of chronicle
for week {week}, past tense, concrete and dry-witted, for a game master to review.

You may ONLY state things that follow from this ledger of resolved actions and
this summary of the city's factions. Invent no new facts, no new characters,
no dialogue. Do not resolve anything further. Slop is regression to the mean:
no prophecies, no ancient evils, no tavern meetings, no "little did they know".

LEDGER OF THE WEEK:
{ledger}

CITY STATE SUMMARY:
{state_summary}

End with a single line "TITLE: <3-6 word title>" for the chronicle file."""
    return complete(prompt, temperature=0.9, max_tokens=6144)
