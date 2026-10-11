# 48 — `system:` field in campaign.yaml

**Status:** completed (2026-10-10)
**Priority:** P2 — small, and unblocks the companion repo's pack work
**Area:** `studio/spine.py`, `studio/assemble.py`, `studio/doctor.py`,
`docs/design/campaign-studio-design.md`,
`Documents\dcg-avtt-integration\README.md` (one line)
**Found by:** Atlas seam residency ruling, 2026-10-10
(`docs/2026-10-10-next-steps.md` W1; `docs/design/atlas-seam.md` P0/M1).
Adversarial review 2026-10-10 corrected the writer story and the
"unblocks" claim; both reworded below.

## Problem

`campaign.yaml` has no notion of game system. The seam ruling keeps the
`system:` field in-core — it is canon metadata about the campaign (the
Tier-1 ICRPG prep packet wanted it before Atlas existed;
`Documents\icrpg-companion\DESIGN.md` §12 — third repo, path recorded so
the citation resolves), and the companion repo's pack installer is meant
to gate on it.

Ground truth today:

- `load_campaign_yaml` (`studio/spine.py:608`) is a bare yaml load;
  `require_campaign` (`spine.py:489`) checks existence only — no field
  validation anywhere. `doctor.check_campaigns` (`doctor.py:280-303`)
  checks the file exists.
- Write sides: `create_campaign` (`spine.py:533-539`) and
  `build_campaign_yaml` (`assemble.py:253-266`).

Two corrections from review:

1. **The field's only writer is the DM's hand — full stop.** The first
   draft said "or later the companion installer"; the companion's
   constitution (its README contract rule 1) forbids writing spine files,
   `campaign.yaml` named explicitly. If automated writes are ever wanted,
   that is a licensed-exception amendment on the companion side — a
   separate ruling, not an assumption this ticket makes.
2. **"Unblocks the installer" needs the contract written down, because
   there is no shared code to lean on.** The companion's contract rule 4
   (no imports either direction) means it re-implements the read, and its
   README §5.2 today gates presets on validation + Atlas built-ins,
   never mentioning `system:`. Nothing stops a naive
   `doc.get("system") == "icrpg"` — which rejects this ticket's own
   `system: ICRPG` acceptance case. Two repos, one meaning: both sides
   must pin identical semantics in their own constitutions.
3. **The round-trip trap is real but narrow.** An exhaustive writer sweep
   found exactly one lossy rewrite path: re-running
   `python studio/assemble.py` over its hardcoded campaign (sole caller
   `assemble.py:733`; the docstring invites idempotent re-runs).
   Studio-created campaigns are never rewritten; export restore writes
   byte-exact from a sha256-verified zip.

## Proposal

- One optional top-level field, `system: <freeform string>`. **No enum,
  no catalog** — the core does not know systems and must not grow one
  (seam non-goal; standing ruling: systems are data at the edge).
- A single read helper in `spine.py` (`campaign_system(ydoc) -> str |
  None`), the only sanctioned accessor in-core: string input →
  `value.strip().lower()`; non-string, empty, or missing → `None`. Not
  dead code — the doctor check consumes it.
- **The same semantics pinned in the companion README §5.2, in the same
  change** (the README is that repo's constitution): the installer gates
  on `str(value).strip().lower()` matching the pack's system; missing →
  refuse with "set `system: <system>` in campaign.yaml".
- Round-trip: `build_campaign_yaml` passes a pre-existing `system:`
  through. The asymmetry is deliberate and owned here: an assemble re-run
  reverts every hand edit to assembled outputs *except* this one —
  `system:` survives because it is edge-authored; everything else in the
  file is generated.
- Doctor: **no new check shape** — informational is the existing
  ok=True-plus-detail idiom (`doctor.py:266`, `:312`). Exactly **one
  aggregated advisory check** across campaigns (the `check_vault`
  pattern), never one check per campaign — duplicate names silently
  collapse in `by_name` (`doctor.py:756`). Unset everywhere → ok, "no
  campaign declares a system (normal)"; set → ok, detail naming each
  `campaign: value`; non-string anywhere → ok=False with the fix. A
  typo (`system: icrpggg`) always passes — findings, never verdicts.
- Doc line where the campaign.yaml field list lives
  (`docs/design/campaign-studio-design.md:43`).

The studio builds no UI for setting it. Gating the prep packet on the
field is *not* this ticket — the field's job is to exist, round-trip, and
be surfaced honestly.

## Acceptance criteria

- [x] `system: ICRPG` (mixed case) loads and the helper returns `icrpg`;
      `system: 5` (a YAML int) and an empty value return `None` —
      `studio/tests/test_spine.py`
- [x] `system: icrpggg` loads, everything passes, doctor may advise but
      never fails; no `system:` key → no finding —
      `studio/tests/test_doctor.py`
- [x] exactly one aggregated check in `report()` for it (assert
      `by_name` carries a single entry) — `test_doctor.py`
- [x] `build_campaign_yaml` over a campaign.yaml carrying `system:`
      preserves the value — test
- [x] the companion README §5.2 carries the pinned read semantics
      (string-match this ticket's helper behavior)

## Implementation notes (2026-10-10)

Landed with the seam batch. `spine.campaign_system` is the sanctioned
accessor (string → `strip().lower()`; missing/empty/non-string → None —
pinned in `test_system_field.py`, including the YAML-int case);
`build_campaign_yaml` passes a hand-set value through verbatim and drops
only an unparseable prior file. `doctor.check_campaign_system` is the one
aggregated advisory check (wired into `run_all_checks`); deviations: a
corrupt campaign.yaml is not silently skipped — the unreadable names ride
in the detail (the S112 lint rule was right to complain); the check name
is "campaign system". The companion README §5.2 carries the pinned read
contract verbatim (`str(value).strip().lower()`, missing → refuse and
instruct, writing campaign.yaml stays forbidden). Full fast tier green.
