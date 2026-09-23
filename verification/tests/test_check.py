"""Zero-dependency tests for the verification ladder's structural layer.

Run directly:  python tests/test_check.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import check

FAILED = []


def ok(name, cond, detail=""):
    print(f"  {name}: {'OK' if cond else 'FAIL ' + detail}")
    if not cond:
        FAILED.append(name)


# --- long sentences -------------------------------------------------------
text = "This is fine. " + ("word " * 40).strip() + ". Short one."
f = check.structural_findings(pathlib.Path("x.md"), text)
ok("long sentence flagged", any(x["rule"] == "structural/long-sentence" for x in f))
ok("clean text has no long-sentence finding",
   not any(x["rule"] == "structural/long-sentence"
           for x in check.structural_findings(pathlib.Path("x.md"), "Short. Also short. Fine.")))

# --- uniform rhythm -------------------------------------------------------
uniform = " ".join(f"Word{ i } verb object today." for i in range(12))
varied = ("The Mint opened its coffers and found, famously, the bottom of them. "
          "Sera walked. The harbor, picketed shut since the strike began, stayed shut; "
          "the customs men went home. A long paragraph of a sentence followed, piling clause "
          "upon clause, dragging through the Tanneries and out the other side, past the "
          "rendering pits where the gang hauled hides for coppers, until it stopped. "
          "Silence. Then the bells rang.")
ok("uniform rhythm flagged", any(x["rule"] == "structural/uniform-rhythm"
                                 for x in check.structural_findings(pathlib.Path("x.md"), uniform)))
ok("varied rhythm passes", not any(x["rule"] == "structural/uniform-rhythm"
                                   for x in check.structural_findings(pathlib.Path("x.md"), varied)))

# --- adverb fog -----------------------------------------------------------
foggy = "Suddenly, quietly, deliberately, carefully, slowly, surely, the gang moved."
ok("adverb fog flagged", any(x["rule"] == "structural/adverb-fog"
                             for x in check.structural_findings(pathlib.Path("x.md"), foggy)))
ok("'family' and 'only' not counted as adverbs",
   not any(x["rule"] == "structural/adverb-fog"
           for x in check.structural_findings(pathlib.Path("x.md"),
                                              "The family only watched. A holy day, an early bell, an ally.")))

# --- epigram cadence ------------------------------------------------------
punchy = "\n\n".join(
    f"Paragraph {i} wanders through some ordinary business of the week and arrives "
    "at its business's natural end, resolving nothing much in particular at length. "
    "So it went." for i in range(7))
flat = "\n\n".join(
    f"Paragraph {i} reports the week's ordinary business in moderate clauses that end "
    "without any performance whatsoever, closing on a plain participial phrase, "
    "settle accounts being the work of the dockside office." for i in range(7))
ok("epigram cadence flagged", any(x["rule"] == "structural/epigram-cadence"
                                  for x in check.structural_findings(pathlib.Path("x.md"), punchy)))
ok("flat cadence passes", not any(x["rule"] == "structural/epigram-cadence"
                                  for x in check.structural_findings(pathlib.Path("x.md"), flat)))

# --- canon variants -------------------------------------------------------
canon_text = ("Ruravaqan held. Ruravaquan did not exist. The Ruravaqan's harbor stayed shut, "
              "and Sera of Ruravaqan waited.")
hits = check.canon_variants(canon_text, ["Ruravaqan", "Sera"])
flagged = {v for _, _, v in hits}
ok("typo variant flagged", "Ruravaquan" in flagged, str(flagged))
ok("possessive of exact term not flagged", "Ruravaqan's" not in flagged)
ok("exact term not flagged", "Ruravaqan" not in flagged and "Sera" not in flagged)

# --- multi-word canon terms (once inert) -----------------------------------
multi_hits = check.canon_variants("The Reliquarry fell. The Dockes stayed shut.",
                                  ["the Reliquary", "the Docks"])
multi_flagged = {v for _, _, v in multi_hits}
multi_terms = {t for _, t, _ in multi_hits}
ok("multi-word term typo flagged (Reliquarry)", "the Reliquary" in multi_terms,
   str(multi_hits))
ok("multi-word term typo flagged (Dockes)", "the Docks" in multi_terms,
   str(multi_hits))
ok("the variants themselves are the tokens", {"Reliquarry", "Dockes"} == multi_flagged)
ok("exact multi-word member word not flagged",
   not check.canon_variants("The Reliquary itself stood.", ["the Reliquary"]))

# --- sentences(): frontmatter and lists are not prose ----------------------
doc = ("---\ntitle: week one\npunctuation: none\n---\n\n"
       "# Week one\n\n- fog rolls in\n- the strike holds\n- no coin moves\n\n"
       "The Mint opened its coffers and found the bottom of them. Silence fell.")
sf = check.structural_findings(pathlib.Path("x.md"), doc)
ok("frontmatter/bullets produce no long-sentence false positive",
   not any(x["rule"] == "structural/long-sentence" for x in sf), str(sf))
ok("the real prose sentence still counts",
   len(check.sentences(doc)) == 2, str(check.sentences(doc)))

# --- LLM finding decoration: model shape -> schema shape -------------------
model_finding = {"rule": "canon-audit", "quote": "The Mint opened its coffers",
                 "note": "STATE does not back it", "severity": "error"}
decorated = check._decorate(model_finding, pathlib.Path("canon-audit.md"),
                            "intro line\nThe Mint opened its coffers and found the bottom.",
                            pathlib.Path("chronicle/0001.md"))
ok("decorated finding passes validate", check.validate(decorated)[0], str(decorated))
ok("decoration locates the quote's line", decorated["line"] == 2, str(decorated["line"]))
ok("decoration attributes the audited file", decorated["file"] == str(pathlib.Path("chronicle/0001.md")))
ok("severity maps to level", decorated["level"] == "error")
bad_sev = check._decorate(dict(model_finding, severity="catastrophic"),
                          pathlib.Path("canon-audit.md"), "text", None)
ok("unknown severity folds to suggestion", bad_sev["level"] == "suggestion"
   and check.validate(bad_sev)[0])

# --- the schema file is the contract ---------------------------------------
ok("required keys loaded from findings.schema.json",
   check._schema_required() == {"file", "line", "rule", "level", "quote"},
   str(check._schema_required()))

# --- vale alert key: Check (2.x/3.x) with Rule fallback --------------------
import subprocess as _sp
vale_json = '{"file.md": {"path": [{"Line": 4, "Check": "Tabletop.Regression", "Level": "warning", "Match": "ancient evil", "Message": "no"}]}}'
saved_which, saved_run = check.shutil.which, check.subprocess.run
check.shutil.which = lambda name: "/fake/vale"
check.subprocess.run = lambda *a, **k: _sp.CompletedProcess(a[0], 0, vale_json, "")
try:
    vfindings, ran = check.vale_findings([pathlib.Path("file.md")])
finally:
    check.shutil.which, check.subprocess.run = saved_which, saved_run
ok("vale ran in fake mode", ran)
ok("vale rule id read from Check key",
   any(x["rule"] == "vale/Tabletop.Regression" for x in vfindings), str(vfindings))
old_json = vale_json.replace('"Check": "Tabletop.Regression"', '"Rule": "Tabletop.Regression"')
check.shutil.which = lambda name: "/fake/vale"
check.subprocess.run = lambda *a, **k: _sp.CompletedProcess(a[0], 0, old_json, "")
try:
    vfindings_old, _ = check.vale_findings([pathlib.Path("file.md")])
finally:
    check.shutil.which, check.subprocess.run = saved_which, saved_run
ok("older vale Rule key still works",
   any(x["rule"] == "vale/Tabletop.Regression" for x in vfindings_old), str(vfindings_old))

# --- findings schema ------------------------------------------------------
good = {"file": "x.md", "line": 3, "rule": "vale/Regression", "level": "error", "quote": "ancient evil"}
ok("valid finding accepted", check.validate(good)[0])
ok("finding missing keys rejected", not check.validate({"rule": "x", "quote": ""})[0])
ok("bad level rejected", not check.validate(dict(good, level="catastrophic"))[0])

print()
if FAILED:
    print(f"{len(FAILED)} test(s) failed: {FAILED}")
    sys.exit(1)
print("all check.py tests passed")
