"""The verification ladder for generated campaign prose.

Layers, cheapest first — each sees only what survives the last:
  structural  — the rhythm slop beats out: uniform sentence lengths, adverb
                fog, paragraph-final epigrams, near-miss canon names
  vale        — the taste lexicon + density caps (skipped with a note if the
                vale binary is not installed)
  prompts     — the four adversarial passes (canon-audit, discriminator,
                compression, specificity); manual by default, --llm to run
                them through the llm.env provider (any OpenAI-compatible
                endpoint), metered like the studio and the engine

Usage:
  python check.py <file-or-dir>... [--state <world state yaml>] [--llm] [--out findings]

Finds land in <out>/findings.json + <out>/queue.md — a review queue, not a
verdict. Nothing here blocks anything; the DM decides.
"""

import argparse
import json
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
CANON_FILE = ROOT / "lexicons" / "canon-terms.txt"
PROMPTS_DIR = ROOT / "prompts"
SCHEMA_FILE = ROOT / "schemas" / "findings.schema.json"
# Provider config: the SAME llm.env precedence chain as the two LLM twins
# (agentic-simulator/src/llm.py, studio/llm_client.py — an import-free copy,
# D1: no imports across projects; one llm.env governs all three clients).
# Environment beats llm.env (repo root, see llm.env.example) beats the Z.ai
# coding-plan defaults below; the legacy key discovery still applies when
# LLM_API_KEY is unset.
ENV_FILE = ROOT.parent / "llm.env"
API_BASE_DEFAULT = "https://api.z.ai/api/coding/paas/v4"
API_URL = API_BASE_DEFAULT + "/chat/completions"
MODEL = "glm-5.3-flash"
_CONFIG = pathlib.Path.home() / ".zcode" / "v2" / "provider_config.json"

# Transient statuses worth a retry: rate limit and gateway churn.
_RETRYABLE = (429, 500, 502, 503, 504)

# -ly words that are not adverbs (or are load-bearing)
_ADVERB_STOPLIST = {
    "only", "family", "likely", "early", "holy", "ugly", "silly", "friendly",
    "july", "reply", "apply", "supply", "comply", "rely", "multiply", "ally",
    "lily", "belly", "fully", "wholly", "melancholy", "anomaly", "assembly",
    "imply", "sly", "fly", "gully", "holly", "jelly", "wily", "surly",
}


def load_canon():
    terms = []
    if CANON_FILE.exists():
        for line in CANON_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms


def read_text(path):
    """UTF-8 with a degradation contract: one cp1252-resaved file (Windows
    prose, a single curly quote) must cost a finding, never the run."""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def encoding_finding(path):
    return {"file": str(path), "line": 1, "rule": "prose/encoding",
            "level": "warning", "quote": "",
            "note": "file is not valid UTF-8 (a cp1252 resave? — resave it "
                    "as UTF-8); structural checks skipped for this file"}


def sentences(text):
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)  # frontmatter out
    body = re.sub(r"^#.*$", "", body, flags=re.MULTILINE)      # headings out
    body = re.sub(r"^\s*[-*+]\s.*$", "", body, flags=re.MULTILINE)   # bullets are data
    body = re.sub(r"^\s*\d+[.)]\s.*$", "", body, flags=re.MULTILINE)  # numbered lists too
    body = re.sub(r"^\|.*$", "", body, flags=re.MULTILINE)     # table rows are data
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _words(s):
    return re.findall(r"[A-Za-z']+", s)


def _edit_distance_le1(a, b):
    """True if a and b differ by at most one edit (insert/delete/substitute)."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    i = j = diffs = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
        else:
            diffs += 1
            if diffs > 1:
                return False
            j += 1
            continue
        j += 1
    return True


_STATBLOCK_FENCE_RE = re.compile(
    r"(?ms)^[ \t]*```statblock[^\n]*\n(.*?)^[ \t]*```[ \t]*\r?$")
# A statblock value: a number, dice (`2d6`, `d6`), or a range (`2-4`).
_STATBLOCK_VALUE_RE = re.compile(
    r"^[+-]?\d+(\.\d+)?$|^[+-]?\d*\s*d\d+$|^\d+\s*[-–]\s*\d+$")


def _statblock_fence_findings(path, text):
    """Shape-only checks on ```statblock fences (ticket 49): every fence
    line parses as `key: value`, keys and values are non-empty, and values
    parse as a number or dice expression. Findings, never verdicts — the
    ladder cannot know a pack's field vocabulary, so a text-valued field
    draws a finding the DM is free to ignore (the note says so)."""
    out = []
    for m in _STATBLOCK_FENCE_RE.finditer(text):
        first = text[:m.start()].count("\n") + 1
        for offset, line in enumerate(m.group(1).splitlines()):
            n = first + 1 + offset
            s = line.strip()
            if not s:
                out.append({"file": str(path), "line": n,
                            "rule": "structural/statblock-fence",
                            "level": "warning", "quote": "",
                            "note": "empty line inside the fence"})
                continue
            key, sep, value = s.partition(":")
            key, value = key.strip(), value.strip()
            if not sep or not key:
                out.append({"file": str(path), "line": n,
                            "rule": "structural/statblock-fence",
                            "level": "warning", "quote": s[:80],
                            "note": "not a `key: value` line"})
            elif not value:
                out.append({"file": str(path), "line": n,
                            "rule": "structural/statblock-fence",
                            "level": "warning", "quote": s[:80],
                            "note": f"empty value for '{key}'"})
            elif not _STATBLOCK_VALUE_RE.match(value):
                out.append({"file": str(path), "line": n,
                            "rule": "structural/statblock-fence",
                            "level": "suggestion", "quote": s[:80],
                            "note": f"'{value}' does not parse as a number "
                                    "or dice (4, 2d6) — if this field "
                                    "carries text, ignore this finding"})
    return out


def structural_findings(path, text):
    """The rhythm checks Vale cannot express as regex."""
    findings, sents = [], sentences(text)

    long = [(text[:text.find(s)].count("\n") + 1, s) for s in sents
            if len(_words(s)) > 35]
    for line, s in long:
        findings.append({"file": str(path), "line": line, "rule": "structural/long-sentence",
                         "level": "warning", "quote": s[:80], "note": "over 35 words"})

    if len(sents) >= 10:
        lengths = [len(_words(s)) for s in sents]
        mean = statistics.mean(lengths)
        if mean and statistics.stdev(lengths) / mean < 0.35:
            findings.append({"file": str(path), "line": 1, "rule": "structural/uniform-rhythm",
                             "level": "warning", "quote": f"{len(sents)} sentences",
                             "note": "sentence lengths too uniform (slop rhythm)"})

    adverbs = [w for w in map(str.lower, _words(text))
               if w.endswith("ly") and w not in _ADVERB_STOPLIST]
    total = max(1, len(_words(text)))
    if len(adverbs) / total * 100 > 4:
        findings.append({"file": str(path), "line": 1, "rule": "structural/adverb-fog",
                         "level": "warning", "quote": f"{len(adverbs)} -ly adverbs",
                         "note": f"{len(adverbs) / total * 100:.1f}% of words — cap is 4%"})

    paras = paragraphs(text)
    epigram = [p for p in paras if sentences(p)
               and len(_words(sentences(p)[-1])) <= 5]
    if len(paras) >= 6 and len(epigram) / len(paras) > 0.3:
        findings.append({"file": str(path), "line": 1, "rule": "structural/epigram-cadence",
                         "level": "suggestion", "quote": f"{len(epigram)}/{len(paras)} paragraphs",
                         "note": "too many paragraphs end on a short punch line"})

    for line_no, term, variant in canon_variants(text, load_canon()):
        findings.append({"file": str(path), "line": line_no, "rule": "structural/canon-variant",
                         "level": "error", "quote": variant,
                         "note": f"one edit away from canon term '{term}'"})

    findings.extend(_statblock_fence_findings(path, text))
    return findings


def canon_variants(text, terms):
    """Capitalized tokens one edit from a canon word (possessives stripped).

    Multi-word canon terms ("the Reliquary") contribute their individual
    words — a single token can never be one edit from a whole phrase, so
    phrase-only matching left more than half the lexicon inert. Words
    shorter than the matcher's floor (5 chars: "Sera") are checked via the
    extend-a-token path that falls out of the length-difference rule."""
    out = []
    canon_words = {}  # single word (lower) -> originating canon term
    for t in terms:
        for w in t.lower().split():
            if w not in ("the", "of", "a", "an"):
                canon_words.setdefault(w, t)
    for m in re.finditer(r"\b[A-Z][a-zA-Z]{4,}(?:'s)?\b", text):
        token = m.group(0).removesuffix("'s")
        low = token.lower()
        if any(low == w or low == t.lower() for w, t in canon_words.items()):
            continue
        for cw, term in canon_words.items():
            if abs(len(cw) - len(low)) <= 1 and _edit_distance_le1(low, cw):
                line = text[: m.start()].count("\n") + 1
                out.append((line, term, token))
                break
    return out


def vale_findings(paths):
    """Run the taste layer if the binary exists; return (findings, ran_bool)."""
    binary = shutil.which("vale")
    if not binary:
        return ([{"file": str(p), "line": 1, "rule": "vale/skipped", "level": "suggestion",
                  "quote": "", "note": "vale not installed — taste layer not run"}
                 for p in paths][:1], False)
    cmd = [binary, "--config", str(ROOT / ".vale.ini"), "--output", "JSON",
           *[str(p.resolve()) for p in paths]]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    findings = []
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ([{"file": "?", "line": 1, "rule": "vale/error", "level": "suggestion",
                  "quote": "", "note": proc.stderr[:200]}], True)
    for fname, blocks in raw.items():
        for _block, alerts in blocks.items():
            for a in alerts:
                findings.append({"file": fname, "line": a.get("Line", 1),
                                 "rule": f"vale/{a.get('Check') or a.get('Rule', '?')}",
                                 "level": a.get("Level", "suggestion"),
                                 "quote": a.get("Match", "")[:80],
                                 "note": a.get("Message", "")})
    return findings, True


def read_env_file(path=None):
    """Same .env reader as studio/llm_client.read_env_file (kept identical
    on purpose): '#' comments, KEY=VALUE, quotes stripped, BOM tolerated,
    {} when absent."""
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


def _find_key():
    """$ZAI_API_KEY, then the key in ~/.zcode/v2/provider_config.json.
    Rule 1 is the coding-plan key: scan rules 1..n, rule 0 LAST — the same
    order as the twins, so all three clients pick the same key from one
    provider_config.json. '' when nothing fits (resolve_config/_complete
    turn that into the readable error at call time)."""
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
            key = ((rules[i].get("config") or {}).get("access")
                   or {}).get("apiKey")
            if key and not key.startswith("enc:"):
                return key
    return ""


def resolve_config(env_file=None):
    """The effective LLM config {key, url, model} — llm.env (see
    llm.env.example), environment first, Z.ai coding-plan defaults last.
    Raises only when a custom base URL has no model: guessing a model name
    for a provider we were never told about is a config error before any
    spend."""
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
        "key": pick("LLM_API_KEY") or _find_key(),
        "url": (base or API_BASE_DEFAULT) + "/chat/completions",
        "model": model or MODEL,
    }


class BudgetExceeded(RuntimeError):
    """The per-run call ceiling is used up — the ladder stops between calls
    with a clear note instead of spending without a bound. main() turns it
    into a per-pass finding like any other pass failure."""


def env_limit():
    """LLM_MAX_CALLS as a positive int, or None when unset or not a number —
    the caller decides the fallback (same semantics as the engine's
    llm.env_limit)."""
    raw = (os.environ.get("LLM_MAX_CALLS") or "").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return None


# Process-global; safe because check.py is a single-threaded CLI. Mirror
# studio/llm_client.py's thread-local meter before any in-process embedding.
_BUDGET = {"calls": 0, "tokens": 0, "limit": env_limit() or 24}


def begin_run(limit=None):
    """Reset the meter for one logical run (one check.py invocation)."""
    _BUDGET["calls"] = 0
    _BUDGET["tokens"] = 0
    if limit is not None:
        _BUDGET["limit"] = max(1, int(limit))


def run_usage():
    return {"calls": _BUDGET["calls"], "tokens": _BUDGET["tokens"],
            "limit": _BUDGET["limit"]}


def _post_json(cfg, body, timeout, attempts=3):
    """POST and parse one completion, retrying transient failures (429/5xx)
    with short backoff. Budget is checked and counted BEFORE every attempt,
    retries included: a failed POST burns provider time too, and an
    unmetered retry loop could spend past the ceiling. Hard HTTP errors
    raise RuntimeError carrying a body snippet."""
    last = None
    for attempt in range(attempts):
        if _BUDGET["calls"] >= _BUDGET["limit"]:
            raise BudgetExceeded(
                f"per-run LLM ceiling reached ({_BUDGET['limit']} calls, "
                f"~{_BUDGET['tokens']} tokens metered) — run stopped "
                "incomplete; raise LLM_MAX_CALLS to check more files with "
                "--llm")
        _BUDGET["calls"] += 1
        if attempt:
            time.sleep(min(2 ** (attempt - 1), 4))
        req = urllib.request.Request(
            cfg["url"], data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {cfg['key']}",
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
            hint = ""
            if "api.z.ai" in cfg["url"] and (e.code == 401 or "1113" in detail):
                hint = (" — the coding endpoint needs the coding-plan key, "
                        "and 1113 means the wrong endpoint or an empty "
                        "plan pool")
            raise RuntimeError(f"HTTP {e.code}: {detail}{hint}") from e
        except urllib.error.URLError as e:
            last = RuntimeError(f"network error (attempt {attempt + 1}): {e.reason}")
            if attempt == attempts - 1:
                raise last from e
    raise last or RuntimeError("unreachable: retry loop exhausted")


def _complete(prompt, max_tokens=8192, max_budget=24576):
    """The twins' self-extending budget, import-free (D1):
    finish_reason "length" doubles max_tokens up to max_budget; a whole
    reply wins over a longer truncated one; a reply still cut at the cap
    comes back visibly marked. Config resolved once per call; the per-run
    ceiling is enforced inside _post_json before every POST."""
    cfg = resolve_config()
    if not cfg["key"]:
        raise RuntimeError(
            "no LLM API key found — set LLM_API_KEY in llm.env (copy "
            "llm.env.example at the repo root) or in the environment; the "
            "legacy $ZAI_API_KEY and ~/.zcode/v2/provider_config.json work "
            "too")
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": max_tokens}
    best = ""
    best_complete = ""
    while True:
        data = _post_json(cfg, body, timeout=300)
        if not isinstance(data, dict):
            # a 200 whose body is not a JSON object (a proxy returning an
            # array) takes the readable provider-error path — the old
            # data["choices"][0] raised a bare IndexError there
            raise RuntimeError(
                f"provider returned no choices (content filter or proxy "
                f"anomaly?); response was {type(data).__name__}, not a "
                "JSON object")
        usage = data.get("usage") or {}
        if usage:
            _BUDGET["tokens"] += int(usage.get("total_tokens") or 0)
        choices = data.get("choices")
        if not choices:
            # a 200 with no choices (content filter, proxy anomaly) must
            # not surface as a bare IndexError
            shape = sorted(data)[:6]
            raise RuntimeError(
                f"provider returned no choices (content filter or proxy "
                f"anomaly?); response keys: {shape}")
        choice = choices[0]
        content = choice["message"].get("content") or ""
        if len(content.strip()) > len(best.strip()):
            best = content
        if choice.get("finish_reason") != "length":
            if content.strip():
                best_complete = content
            break
        if body["max_tokens"] >= max_budget:
            break
        body["max_tokens"] = min(max_budget, body["max_tokens"] * 2)
    if best_complete.strip():
        # a whole reply wins outright — even a shorter one: plain
        # longest-wins preferred the truncated attempt over the complete
        # answer
        return best_complete
    if best.strip():
        # the budget is spent and every attempt hit finish_reason "length":
        # the text ends mid-thought — return it visibly marked, never as if
        # it were whole
        return best + "\n\n[truncated]"
    return best


def llm_findings(prompt_file, state_text, text, source_file=None):
    """Fill {{STATE}}/{{TEXT}} placeholders, call the model, harvest JSON,
    and DECORATE each finding into the schema shape: the prompts ask the
    model only for {rule, quote, note, severity} — file/line/level are added
    here (line located from the quote's first occurrence in the audited
    text), so nothing the model says is discarded for missing bookkeeping."""
    prompt = prompt_file.read_text(encoding="utf-8")
    prompt = prompt.replace("{{STATE}}", state_text or "(none provided)").replace("{{TEXT}}", text)
    raw = _complete(prompt + "\n\nReturn the JSON ONLY.")
    m = re.search(r"\[.*\]|\{.*\}", raw, re.DOTALL)
    if not m:
        return [{"file": str(source_file or prompt_file.name), "line": 1,
                 "rule": f"prompts/{prompt_file.stem}",
                 "level": "suggestion", "quote": "", "note": "no JSON in response"}]
    out = json.loads(m.group(0))
    items = out if isinstance(out, list) else [out]
    return [_decorate(f, prompt_file, text, source_file) for f in items]


def _decorate(finding, prompt_file, text, source_file):
    """Model shape {rule, quote, note, severity?} -> schema shape
    {file, line, rule, level, quote, ...}. severity maps to level; unknown
    severities fold to suggestion (validate would reject them)."""
    out = dict(finding)
    severity = out.pop("severity", None)
    out["level"] = severity if severity in ("error", "warning", "suggestion") else "suggestion"
    out["rule"] = "prompts/" + str(out.get("rule") or prompt_file.stem)
    out["file"] = str(source_file or prompt_file.name)
    quote = out.get("quote") or ""
    idx = text.find(quote) if quote else -1
    out["line"] = text[:idx].count("\n") + 1 if idx >= 0 else 1
    return out


_SCHEMA_REQUIRED = None


def _schema_required():
    """The required-keys list lives in schemas/findings.schema.json — the
    schema file is the contract, not decoration. Falls back to the
    hardcoded set if the file is missing."""
    global _SCHEMA_REQUIRED
    if _SCHEMA_REQUIRED is None:
        required = {"file", "line", "rule", "level", "quote"}
        if SCHEMA_FILE.exists():
            try:
                schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
                required = set(schema.get("required") or required)
            except (json.JSONDecodeError, OSError):
                pass
        _SCHEMA_REQUIRED = required
    return _SCHEMA_REQUIRED


def validate(finding):
    """Schema check: required keys per schemas/findings.schema.json plus the
    level enum."""
    required = _schema_required()
    missing = required - set(finding)
    if missing:
        return False, f"missing keys: {sorted(missing)}"
    if finding["level"] not in ("error", "warning", "suggestion"):
        return False, f"bad level: {finding['level']}"
    return True, ""


def write_queue(out_dir, findings):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "findings.json").write_text(
        json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8")
    lines = ["# Review queue", ""]
    for f in findings:
        loc = f"{f['file']}:{f['line']}"
        lines.append(f"- **[{f['level']}] {loc}** `{f['rule']}` — {f.get('note','')}"
                     + (f"\n  > {f['quote']}" if f.get("quote") else ""))
    (out_dir / "queue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def discriminate(state_text, canon_path, decoy_paths):
    """The discriminator pass, run deliberately: TEXT_A is the canon-adjacent
    passage, B and C the generic decoys. Findings: the model picking a decoy
    is an error; low confidence is a warning (the canon passage may be slop)."""
    pf = PROMPTS_DIR / "discriminator.md"
    prompt = pf.read_text(encoding="utf-8")
    prompt = prompt.replace("{{STATE}}", state_text or "(none provided)")
    for slot, p in zip(("TEXT_A", "TEXT_B", "TEXT_C"), [canon_path] + list(decoy_paths)):
        text = read_text(p)
        if text is None:
            return [encoding_finding(p)]
        prompt = prompt.replace("{{" + slot + "}}", text)
    raw = _complete(prompt + "\n\nReturn the JSON ONLY.")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return [{"file": str(canon_path), "line": 1, "rule": "prompts/discriminator",
                 "level": "suggestion", "quote": "", "note": "no JSON in response"}]
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return [{"file": str(canon_path), "line": 1, "rule": "prompts/discriminator",
                 "level": "suggestion", "quote": "",
                 "note": f"response was not valid JSON: {e}"}]
    choice = str(out.get("choice", "?")).upper()
    try:
        confidence = float(out.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    cues_raw = out.get("cues", [])
    cues = "; ".join(cues_raw if isinstance(cues_raw, list) else [str(cues_raw)])[:200]
    findings = []
    if choice != "A":
        findings.append({"file": str(canon_path), "line": 1,
                         "rule": "prompts/discriminator", "level": "error",
                         "quote": "", "note": f"model picked passage {choice} over the "
                                              f"canon-adjacent one — cues: {cues}"})
    if confidence < 0.4:
        findings.append({"file": str(canon_path), "line": 1,
                         "rule": "prompts/discriminator", "level": "warning",
                         "quote": "", "note": f"confidence {confidence} — the canon passage "
                                              f"may itself be slop — cues: {cues}"})
    if not findings:
        findings.append({"file": str(canon_path), "line": 1,
                         "rule": "prompts/discriminator", "level": "suggestion",
                         "quote": "", "note": f"canon passage identified "
                                              f"(confidence {confidence}) — no finding"})
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--state", help="world state yaml for the LLM passes")
    ap.add_argument("--out", default="findings")
    ap.add_argument("--llm", action="store_true", help="run the adversarial prompts")
    ap.add_argument("--discriminate", nargs=3, metavar=("CANON", "DECOY1", "DECOY2"),
                    help="run the discriminator: the canon-adjacent passage plus "
                         "two generic decoys (paths relative to cwd)")
    args = ap.parse_args(argv)

    state_text = None
    if args.state:
        state_text = read_text(args.state)
        if state_text is None:
            print(f"warning: {args.state} is not valid UTF-8 — the LLM passes "
                  "run without it (canon-audit skipped)", file=sys.stderr)
    if args.state and not args.llm and not args.discriminate:
        print("note: --state only feeds the LLM passes; it is unused without --llm "
              "or --discriminate", file=sys.stderr)

    begin_run()  # one invocation is one metered run (LLM_MAX_CALLS, default 24)

    if args.discriminate:
        findings = discriminate(state_text, *args.discriminate)
        good = [f for f in findings if validate(f)[0]]
        write_queue(pathlib.Path(args.out), good)
        print(f"discriminate: {len(good)} findings -> {args.out}/queue.md")
        for f in good:
            print(f"  [{f['level']:<10}] {f['file']} {f['rule']}: {f.get('note', '')}")
        # advisory tool: findings never gate the exit code, by design
        return 0

    files = []
    for p in map(pathlib.Path, args.paths):
        if not p.exists():
            ap.error(f"path does not exist: {p}")
        files.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    files = [f for f in files if f.name not in ("findings.json", "queue.md")]

    findings = []
    unreadable = set()
    for f in files:
        text = read_text(f)
        if text is None:
            findings.append(encoding_finding(f))
            unreadable.add(f)
            continue
        findings.extend(structural_findings(f, text))
    vale, ran = vale_findings(files)
    findings.extend(vale)

    if args.llm:
        for f in files:
            if f in unreadable:
                continue  # the encoding finding already fired above
            text = read_text(f)
            for pf in sorted(PROMPTS_DIR.glob("*.md")):
                if pf.stem == "discriminator":
                    continue  # needs decoy passages: run via --discriminate
                if pf.stem == "canon-audit" and not state_text:
                    continue
                print(f"  llm pass {pf.stem} on {f.name} ...", flush=True)
                try:
                    findings.extend(llm_findings(pf, state_text, text, source_file=f))
                except Exception as e:  # noqa: BLE001 — one pass failing must not kill the run
                    findings.append({"file": str(f), "line": 1, "rule": f"prompts/{pf.stem}",
                                     "level": "suggestion", "quote": "", "note": f"pass failed: {e}"})

    good, bad = [], []
    for f in findings:
        (good if validate(f)[0] else bad).append(f)
    write_queue(pathlib.Path(args.out), good)
    print(f"{len(files)} file(s): {len(good)} findings -> {args.out}/queue.md")
    for f in bad:
        print(f"  REJECTED (bad shape): {json.dumps(f)[:120]}")
    for f in good:
        print(f"  [{f['level']:<10}] {f['file']}:{f['line']} {f['rule']}")
    # advisory tool: findings never gate the exit code, by design
    return 0


if __name__ == "__main__":
    sys.exit(main())
