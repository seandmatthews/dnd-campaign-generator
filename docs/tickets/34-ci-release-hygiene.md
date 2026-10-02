# 34 — CI, release & repo hygiene

**Priority:** P3
**Area:** `.github/workflows/ci.yml`, `studio/tests/test_integration.py`,
`.gitignore`, `setup-tools.py`, `release/build_release.py`,
`release/templates/install.cmd`, `studio/tests/test_auto_accept.py`
**Found by:** 2026-10-02 full review P3-46, P3-47, P3-48, P3-49, P3-50,
P3-51

## Problem

1. **The integration tier doesn't self-skip on its documented direct
   invocation (P3-46).** `pytest.ini` documents `pytest -m integration`,
   but `test_integration.py` has no missing-campaign guard — a fresh clone
   gets 9 fixture errors instead of a clean skip; only run-tests.py's
   orchestrator guard prevents it.
2. **CI installs unpinned deps (P3-47).** `ci.yml:37-39` pip-installs
   floating versions while `pyproject.toml` says `studio/requirements.txt`
   is "the source of truth for the pinned versions the maintainer tests
   against" (fastapi==0.141.1, uvicorn==0.53.0, …). CI can go red on
   upstream drift — or green on combinations never tested locally.
   `uv.lock` is unused by CI.
3. **`dnd-mapping/worlds/candidates/` is engine scratch but not
   gitignored (P3-48).** Every world-gen run dirties git status (the two
   untracked `world-*.yaml` files sitting in status right now). `.gitignore`
   covers `worlds/sites/` only.
4. **`setup-tools.py:175` dead ternary (P3-49).** `return 1 if
   args.check else 1` — both arms return 1; the `--check` flag does
   nothing there and the surrounding "the studio still runs" message
   pairs confusingly with a failure exit.
5. **Release VERSION stamp is minute-granular vs the byte-compare fast
   path (P3-50).** `build_release.py:214` stamps `%Y%m%d%H%M`; two
   rebuilds within one minute produce identical VERSION files and the
   second exe takes install.cmd's fast path (`fc /b`) without mirroring
   the changed payload — a 60-second hole in "every rebuild is an
   update". Also `install.cmd:28` `/XD campaigns` excludes by NAME at any
   depth: a future nested `campaigns` dir would silently stop mirroring.
6. **Redundant double assertion in `test_auto_accept.py:99-101` (P3-51).**
   `assert X in texts[0] or any(X in t for t in texts)` — the `any` arm
   subsumes the `[0]` arm and the file is parsed twice; cosmetic but
   confusing.

## Proposal

Guard the integration module (skip when `campaigns/ruravaqan` is absent,
mirroring the orchestrator's check); install from
`studio/requirements.txt` (or `uv sync`) in CI; gitignore
`dnd-mapping/worlds/candidates/` (and commit the two stray files' fate:
delete them — they're QA scratch); fix the ternary so `--check` exits 0 on
success; add seconds to the VERSION stamp; scope the robocopy exclusion to
the top level (`/XD` with the full relative path, or a pre-move check);
collapse the double assertion.

## Acceptance criteria

- [ ] `pytest studio/tests -m integration` on a fresh clone: clean skip,
      no errors.
- [ ] CI logs show pinned installs matching requirements.txt (or uv.lock
      usage).
- [ ] `git status` clean after a world-gen run in a scratch campaign.
- [ ] `setup-tools.py --check` exits 0 when tools are present.
- [ ] Two builds in the same minute produce different VERSION bytes
      (and the fast path still catches a no-change rebuild).
- [ ] `campaigns/` under a nested dir would still mirror (or the exclusion
      is provably top-level-only); test or reasoned note.
- [ ] Double assertion collapsed; suite green.
