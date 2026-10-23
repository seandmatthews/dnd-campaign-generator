/* Source-contract test for the single-sourced duration copy (ticket 16
   wrote it; ticket 55 re-measured the numbers): the numbers the UI shows
   about how long things take live in ONE place (core.js's
   Studio.DURATIONS) and sim.js derives from it — the fragile
   `n + '0 minutes'` string math must not come back, and the hardcoded
   literals must not either. core.js initializes the DOM at load, so the
   contract is checked against the source text, not a loaded module.
   TUTORIAL.md mirrors the table in prose (the ticket 55 claim) — the
   mirror is pinned here too, so a re-measure that moves one file but
   not the other fails loudly.
     node studio/tests/js/test_durations.cjs
   Run by run-tests.py's studio-js-durations suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const core = fs.readFileSync(
  path.join(__dirname, '..', '..', 'static', 'js', 'core.js'), 'utf8');
const sim = fs.readFileSync(
  path.join(__dirname, '..', '..', 'static', 'js', 'sim.js'), 'utf8');
const tutorial = fs.readFileSync(
  path.join(__dirname, '..', '..', '..', 'docs', 'for-new-gms',
            'TUTORIAL.md'), 'utf8');

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

ok('core.js defines the single source with band/approx/weekRun', () => {
  assert.match(core, /Studio\.DURATIONS\s*=/, 'Studio.DURATIONS must exist');
  assert.match(core, /band:\s*function/, 'band(weeks) must exist');
  assert.match(core, /approx:\s*function/, 'approx(weeks) must exist');
  assert.match(core, /weekRun:/, 'weekRun must exist');
});

ok("band(2) reads '120–180 minutes' (re-measured 2026-10-23, ticket 55)", () => {
  // re-derive exactly what the function computes, from its own constants
  // (full-roster weeks run 60–90 minutes — ticket 01's 10–20 was the
  // 2026-10-01 number, before the consistency/rewrite passes landed)
  const low = parseInt(core.match(/minutesPerWeekLow:\s*(\d+)/)[1], 10);
  const high = parseInt(core.match(/minutesPerWeekHigh:\s*(\d+)/)[1], 10);
  assert.strictEqual(low, 60);
  assert.strictEqual(high, 90);
  const band2 = Math.round(2 * low) + '\u2013' + (2 * high) + ' minutes';
  assert.strictEqual(band2, '120\u2013180 minutes');
  assert.match(core, /weekRun:\s*'60\\u201390 minutes'/,
    'weekRun carries the same re-measured band');
});

ok("TUTORIAL.md mirrors the table's week-run band in prose", () => {
  // the two claim to mirror each other — one source of truth for the
  // number, so the doc and the UI cannot drift apart again
  assert.ok(/expect 60\u201390 minutes/.test(tutorial),
    'the tutorial states the re-measured week-run band');
  assert.ok(!/expect 10\u201320 minutes/.test(tutorial),
    'the stale 10–20 claim must be gone from the tutorial');
  assert.ok(!/10\u201320 minutes/.test(core),
    'the stale 10–20 claim must be gone from the UI table');
});

ok('sim.js derives from S.DURATIONS — no string math, no literals', () => {
  assert.ok(!/'\s*\+\s*n\s*\+\s*'0\s*minutes/.test(sim),
            "the n + '0 minutes' formula is back");
  assert.ok(!/10&ndash;20\s*minutes/.test(sim),
            'a hardcoded forecast literal is back');
  assert.ok(!/10\u201320\s*minutes/.test(sim),
            'a hardcoded week-run literal is back');
  assert.ok(!/60&ndash;90\s*minutes/.test(sim),
            'a hardcoded forecast literal is back (derives, never spells)');
  const uses = sim.match(/S\.DURATIONS\.(band|approx|weekRun)/g) || [];
  assert.strictEqual(uses.length, 4,
    'expected band(2), approx(n), weekRun x2 — got: ' + uses.join(', '));
});

if (failed) {
  console.error(`${failed} durations test(s) failed`);
  process.exit(1);
}
console.log('all durations tests passed');
