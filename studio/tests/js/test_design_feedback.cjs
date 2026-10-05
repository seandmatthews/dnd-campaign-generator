/* Tests for the design-form feedback (ticket 42: the pass is a QUEUED JOB
   on the no-lock lane now; the 2026-10-03 silent-wedge fixes for the old
   synchronous endpoint became the job record + the queue strip): the job
   watch's ceiling vs the server's own worst case, the elapsed label, the
   jobs-snapshot slice the panel renders from, and the restore note. The
   DOM wiring itself stays untested here — the pure decision logic is what
   regresses. Zero dependencies:
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

ok('the job-watch ceiling sits above the server worst case but not absurdly', () => {
  // server worst: two complete() calls (pass + one re-ask), each 3
  // attempts x 300 s + small backoffs — just over 30 min
  const serverWorstMs = 2 * 3 * 300 * 1000;
  assert.ok(S.DESIGN_TIMEOUT_MS > serverWorstMs,
    'a legal max-length pass must never be killed client-side');
  assert.ok(S.DESIGN_STALE_MS > S.DESIGN_TIMEOUT_MS,
    'the auto-restore window outlives the watch ceiling');
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

// the campaign-jobs snapshot slice (ticket 42)
const JOB = (over) => Object.assign(
  { id: 'j1', kind: 'sim-design', status: 'running' }, over);

ok('designJobState surfaces the running or queued pass and the finished one', () => {
  const st = S.designJobState({
    running: JOB({}),
    queued: [JOB({ id: 'j2', status: 'queued', position: 1 })],
    last_finished: { 'sim-design': JOB({ id: 'j0', status: 'done' }) },
  });
  assert.ok(st.running && st.running.id === 'j1', 'the running pass surfaces');
  assert.ok(st.queued && st.queued.id === 'j2', 'a queued second pass surfaces');
  assert.ok(st.finished && st.finished.id === 'j0',
    'the newest finished record is the restore source');
});

ok('designJobState ignores other kinds and empty snapshots', () => {
  const st = S.designJobState({
    running: { id: 'w', kind: 'sim-week', status: 'running' },
    queued: [{ id: 'w2', kind: 'forecast', status: 'queued' }],
    last_finished: { 'sim-week': { id: 'w0', kind: 'sim-week', status: 'done' } },
  });
  assert.strictEqual(st.running, null, 'a week job is not a design pass');
  assert.ok(!st.queued, 'no design job in the queue');
  assert.strictEqual(st.finished, null,
    'a finished week is not a design to restore');
  const empty = S.designJobState(null);
  assert.ok(empty && empty.running === null && empty.finished === null,
    'a missing snapshot degrades to "nothing known"');
});

ok('designJobState refuses a finished record without a restorable design', () => {
  // the slice only CARRIES the record; the render checks shape — but a
  // failed/errored record must never read as restorable either
  const st = S.designJobState({
    last_finished: { 'sim-design': JOB({ status: 'error', error: 'x' }) },
  });
  assert.ok(st.finished && st.finished.status === 'error',
    'the failed record surfaces (for any future honesty surface)');
});

ok('designRestoreNote describes the restored draft inside the window', () => {
  const w = S.designRestoreNote(90 * 1000);
  assert.ok(w, 'a 90 s-old answer restores');
  assert.ok(/restored/.test(w), 'it says what happened: ' + w);
  assert.ok(/form resets/.test(w), 'it explains WHY the form refilled');
  assert.ok(/1m 30s ago/.test(w), 'it carries the age');
  assert.strictEqual(S.designRestoreNote(S.DESIGN_STALE_MS + 1000), null,
    'past the window a self-filling form surprises more than it helps');
  assert.strictEqual(S.designRestoreNote(-5), null,
    'a negative age (clock skew) is not a valid age');
  assert.strictEqual(S.designRestoreNote('x'), null, 'garbage is refused');
});

if (failed) {
  console.log(`\n${failed} test(s) FAILED`);
  process.exit(1);
}
console.log('\nall design-feedback tests passed');
