/* Source-contract test for the single-sourced duration copy (ticket 16):
   the numbers the UI shows about how long things take live in ONE place
   (core.js's Studio.DURATIONS) and sim.js derives from it — the fragile
   `n + '0 minutes'` string math must not come back, and the hardcoded
   literals must not either. core.js initializes the DOM at load, so the
   contract is checked against the source text, not a loaded module.
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

ok("band(2) still reads '10–20 minutes' (today's measured copy)", () => {
  // re-derive exactly what the function computes, from its own constant
  const perWeek = parseInt(
    core.match(/minutesPerWeek:\s*(\d+)/)[1], 10);
  const band2 = Math.round(2 * perWeek / 2) + '\u2013' + (2 * perWeek) +
    ' minutes';
  assert.strictEqual(band2, '10\u201320 minutes');
});

ok('sim.js derives from S.DURATIONS — no string math, no literals', () => {
  assert.ok(!/'\s*\+\s*n\s*\+\s*'0\s*minutes/.test(sim),
            "the n + '0 minutes' formula is back");
  assert.ok(!/10&ndash;20\s*minutes/.test(sim),
            'a hardcoded forecast literal is back');
  assert.ok(!/10\u201320\s*minutes/.test(sim),
            'a hardcoded week-run literal is back');
  const uses = sim.match(/S\.DURATIONS\.(band|approx|weekRun)/g) || [];
  assert.strictEqual(uses.length, 4,
    'expected band(2), approx(n), weekRun x2 — got: ' + uses.join(', '));
});

if (failed) {
  console.error(`${failed} durations test(s) failed`);
  process.exit(1);
}
console.log('all durations tests passed');
