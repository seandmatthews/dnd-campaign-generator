/* Tests for the design-form feedback fixes (the silent-wedge bug found
   2026-10-03): the elapsed label, the reload-marker warning, and the
   client ceiling vs the server's own worst case. The DOM wiring itself
   stays untested here — the pure decision logic is what regresses. Zero
   dependencies:
     node studio/tests/js/test_design_feedback.cjs
   Run by run-tests.py's studio-js-design suite (skipped when node is
   absent). */

'use strict';
const path = require('path');
const assert = require('assert');

// sim.js is a plain IIFE over window.Studio — stub enough of the
// namespace for it to load (it registers a document-level delegated
// listener at load time), then read the pure helpers back
global.window = { Studio: { tabs: {} }, StudioHandoff: {} };
global.document = { addEventListener: function () {} };
global.sessionStorage = {
  getItem: function () { return null; },
  setItem: function () {},
  removeItem: function () {}
};
require(path.join(__dirname, '..', '..', 'static', 'js', 'sim.js'));
const S = global.window.Studio;

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

ok('the client ceiling sits above the server worst case but not absurdly', () => {
  // server worst: two complete() calls (pass + one re-ask), each 3
  // attempts x 300 s + small backoffs — just over 30 min
  const serverWorstMs = 2 * 3 * 300 * 1000;
  assert.ok(S.DESIGN_TIMEOUT_MS > serverWorstMs,
    'a legal max-length pass must never be killed client-side');
  assert.ok(S.DESIGN_STALE_MS > S.DESIGN_TIMEOUT_MS,
    'the marker outlives the wait ceiling');
  assert.ok(S.DESIGN_TIMEOUT_MS <= 40 * 60 * 1000,
    'and stays within a sitting distance of it');
});

ok('designElapsed formats seconds and padded minutes', () => {
  assert.strictEqual(S.designElapsed(0), '0s');
  assert.strictEqual(S.designElapsed(-5), '0s');
  assert.strictEqual(S.designElapsed(42000), '42s');
  assert.strictEqual(S.designElapsed(65000), '1m 05s');
  assert.strictEqual(S.designElapsed(35 * 60 * 1000), '35m 00s');
  assert.strictEqual(S.designElapsed(35 * 60 * 1000 + 999), '35m 00s');
});

const NOW = 1_800_000_000_000;
const marker = (ageMs, camp) =>
  ({ camp: camp === undefined ? 'brineharrow-coast' : camp, at: NOW - ageMs });

ok('a fresh same-campaign marker warns about the duplicate-spend re-click', () => {
  const w = S.designInflightWarning(marker(90 * 1000), 'brineharrow-coast', NOW);
  assert.ok(w, 'a 90 s-old marker must warn');
  assert.ok(/1m 30s ago/.test(w), 'the warning carries the elapsed age: ' + w);
  assert.ok(/SECOND pass/.test(w) && /twice/.test(w),
    'the warning names the duplicate spend');
});

ok('the warning stays silent for other campaigns, garbage, and stale marks', () => {
  assert.strictEqual(S.designInflightWarning(null, 'brineharrow-coast', NOW), null);
  assert.strictEqual(S.designInflightWarning(marker(0, 'other-camp'),
    'brineharrow-coast', NOW), null);
  assert.strictEqual(
    S.designInflightWarning({ camp: 'brineharrow-coast' }, 'brineharrow-coast', NOW),
    null);
  assert.strictEqual(S.designInflightWarning('garbage', 'brineharrow-coast', NOW),
    null);
  assert.strictEqual(S.designInflightWarning(marker(S.DESIGN_STALE_MS + 1000),
    'brineharrow-coast', NOW), null,
    'past the believable window the marker must not scare anyone');
  assert.strictEqual(S.designInflightWarning(marker(-60 * 1000),
    'brineharrow-coast', NOW), null,
    'a future timestamp (clock skew) is not a valid age');
});

if (failed) {
  console.log(`\n${failed} test(s) FAILED`);
  process.exit(1);
}
console.log('\nall design-feedback tests passed');
