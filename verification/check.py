"""The verification ladder for generated campaign prose.

Layers, cheapest first — each sees only what survives the last:
  structural  — the rhythm slop beats out: uniform sentence lengths, adverb
                fog, paragraph-final epigrams, near-miss canon names
  vale        — the taste lexicon + density caps (skipped with a note if the
                vale binary is not installed)
  prompts     — the four adversarial passes (canon-audit, discriminator,
                compression, specificity); manual by default, --llm to run
                them through the coding endpoint

Usage:
  python check.py <file-or-dir>... [--state <world state yaml>] [--llm] [--out findings]

Finds land in <out>/findings.json + <out>/queue.md — a review queue, not a
verdict. Nothing here blocks anything; the DM decides.
"""

import argparse
import json
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
CANON_FILE = ROOT / "lexicons" / "canon-terms.txt"
PROMPTS_DIR = ROOT / "prompts"
API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
MODEL = "glm-5.3-flash"

# -ly words that are not adverbs (or are load-bearing)
_ADVERB_STOPLIST = {
    "only", "family", "likely", "early", "holy", "ugly", "silly", "friendly",
    "july", "reply", "apply", "supply", "comply", "rely", "multiply", "ally",
    "lily", "belly", "fully", "wholly", "melancholy", "anomaly", "assembly",
}


def load_canon():
    terms = []
    if CANON_FILE.exists():
        for line in CANON_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms


def sentences(text):
    body = re.sub(r"^#.*$", "", text, flags=re.MULTILINE)      # headings out
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
    return findings


def canon_variants(text, terms):
    """Capitalized tokens one edit from a canon term (possessives stripped)."""
    out = []
    canon = {t.lower(): t for t in terms}
    for m in re.finditer(r"\b[A-Z][a-zA-Z]{4,}(?:'s)?\b", text):
        token = m.group(0).removesuffix("'s")
        low = token.lower()
        if low in canon:
            continue
        for cl, term in canon.items():
            if abs(len(cl) - len(low)) <= 1 and _edit_distance_le1(low, cl):
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
                                 "rule": f"vale/{a.get('Rule', '?')}",
                                 "level": a.get("Level", "suggestion"),
                                 "quote": a.get("Match", "")[:80],
                                 "note": a.get("Message", "")})
    return findings, True


def _find_key():
    import os
    if os.environ.get("ZAI_API_KEY"):
        return os.environ["ZAI_API_KEY"]
    cfg = pathlib.Path.home() / ".zcode" / "v2" / "provider_config.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    return data["config"]["providerConfigRules"]["providerRules"][1]["config"]["access"]["apiKey"]


def _complete(prompt, max_tokens=8192, max_budget=24576):
    """Same self-extending budget as the simulator's llm.py."""
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": max_tokens}
    best = ""
    while True:
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {_find_key()}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if len(content.strip()) > len(best.strip()):
            best = content
        if choice.get("finish_reason") != "length" or body["max_tokens"] >= max_budget:
            return best
        body["max_tokens"] = min(max_budget, body["max_tokens"] * 2)


def llm_findings(prompt_file, state_text, text):
    """Fill {{STATE}}/{{TEXT}} placeholders, call the model, harvest JSON."""
    prompt = prompt_file.read_text(encoding="utf-8")
    prompt = prompt.replace("{{STATE}}", state_text or "(none provided)").replace("{{TEXT}}", text)
    raw = _complete(prompt + "\n\nReturn the JSON ONLY.")
    m = re.search(r"\[.*\]|\{.*\}", raw, re.DOTALL)
    if not m:
        return [{"file": prompt_file.name, "line": 1, "rule": f"prompts/{prompt_file.stem}",
                 "level": "suggestion", "quote": "", "note": "no JSON in response"}]
    out = json.loads(m.group(0))
    return out if isinstance(out, list) else [out]


def validate(finding):
    """Schema check against schemas/findings.schema.json — required keys only."""
    required = {"file", "line", "rule", "level", "quote"}
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--state", help="world state yaml for the LLM passes")
    ap.add_argument("--out", default="findings")
    ap.add_argument("--llm", action="store_true", help="run the four adversarial prompts")
    args = ap.parse_args(argv)

    files = []
    for p in map(pathlib.Path, args.paths):
        files.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    files = [f for f in files if f.name not in ("findings.json", "queue.md")]

    findings = []
    for f in files:
        findings.extend(structural_findings(f, f.read_text(encoding="utf-8")))
    vale, ran = vale_findings(files)
    findings.extend(vale)

    if args.llm:
        state_text = pathlib.Path(args.state).read_text(encoding="utf-8") if args.state else None
        for f in files:
            text = f.read_text(encoding="utf-8")
            for pf in sorted(PROMPTS_DIR.glob("*.md")):
                if pf.stem == "discriminator":
                    continue  # needs decoy passages; run it deliberately
                if pf.stem == "canon-audit" and not state_text:
                    continue
                print(f"  llm pass {pf.stem} on {f.name} ...", flush=True)
                try:
                    findings.extend(llm_findings(pf, state_text, text))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
