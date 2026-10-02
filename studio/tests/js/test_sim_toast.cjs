/* Tests for sim.js's Studio.proposalAcceptMsg — the accept toast must
   name what actually published (ticket 30 / P3-32): a session_changes
   accept used to fall through to "Week N is canon", which is not what
   publishing a session's consequences is. Zero dependencies:
     node studio/tests/js/test_sim_toast.cjs
   Run by run-tests.py's studio-js-toast suite (skipped when node is
   absent). */

'use strict';
const path = require('path');
const assert = require('assert');

// sim.js is a plain IIFE over window.Studio — stub enough of the
// namespace for it to load (it registers a document-level delegated
// listener at load time), then read the pure helper back
global.window = { Studio: { tabs: {} }, StudioHandoff: {} };
global.document = { addEventListener: function () {} };
require(path.join(__dirname, '..', '..', 'static', 'js', 'sim.js'));
const acceptMsg = global.window.Studio.proposalAcceptMsg;

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

ok('a session_changes accept names the session changes, never a week', () => {
  const msg = acceptMsg({ kind: 'session_changes',
    run_record: { session_id: 's-1' } }, {});
  assert.ok(/Session changes are canon/.test(msg), msg);
  assert.ok(/state, knowledge, and disclosures published/.test(msg),
    'the toast names what actually published');
  assert.ok(!/Week \d+ is canon/.test(msg),
    'the old wrong toast shape must be gone');
  assert.ok(/exactly once/.test(msg), 'the idempotency promise stays');
});

ok('every other bundle kind keeps its own line', () => {
  assert.strictEqual(acceptMsg({ kind: 'goal_updates' }, {}),
    'Goal changes are canon — applied exactly once.');
  assert.strictEqual(acceptMsg({ kind: 'session_effects' }, {}),
    'Session changes are canon — applied exactly once.');
  assert.strictEqual(acceptMsg({ kind: 'project_changes' }, {}),
    'Project changes are canon — applied exactly once.');
});

ok('a plain week accept still says which week became canon', () => {
  assert.strictEqual(acceptMsg({ kind: undefined, proposed_week: 7 }, {}),
    'Week 7 is canon — accepted exactly once.');
  assert.strictEqual(acceptMsg({ proposed_week: 7 }, { week: 9 }),
    'Week 9 is canon — accepted exactly once.',
    'the response week wins when the server reports one');
  assert.strictEqual(acceptMsg(null, null),
    'Week undefined is canon — accepted exactly once.',
    'degenerate input degrades, never throws');
});

if (failed) {
  console.error(`${failed} sim-toast test(s) failed`);
  process.exit(1);
}
console.log('all sim-toast tests passed');
