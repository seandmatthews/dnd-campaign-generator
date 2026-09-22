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
