/* Tests for sessions.js's Studio.prepareSelection — the prepare handler's
   checked-card → id resolution (ticket 11). Zero dependencies:
     node studio/tests/js/test_sessions_prepare.cjs
   Run by run-tests.py's studio-js-sessions suite (skipped when node is
   absent). The contract: saveDraft serializes the cards in DOM order and
   the server mints ids for the new ones, so POSITION correlates a card
   with its saved suggestion — a checked card the DM just added must
   resolve to the minted id, never be dropped. */

'use strict';
const path = require('path');
const assert = require('assert');

// sessions.js is a plain IIFE over window.Studio — stub enough of the
// namespace for it to load (it assigns S.tabs.sessions at the end), then
// read the pure helper back off it
global.window = { Studio: { tabs: {} } };
require(path.join(__dirname, '..', '..', 'static', 'js', 'sessions.js'));
const prepareSelection = global.window.Studio.prepareSelection;

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

ok('a newly-added checked card resolves to its minted id', () => {
  const cards = [{ checked: true, id: null }];
  const saved = [{ id: 's-001' }];
  assert.deepStrictEqual(prepareSelection(cards, saved), ['s-001']);
});

ok('a mix of existing and new cards stages all of the checked ones', () => {
  const cards = [
    { checked: true, id: 's-101' },
    { checked: false, id: null },   // new but unchecked — stays out
    { checked: true, id: null },    // new AND checked — must ride along
    { checked: false, id: 's-102' },
  ];
  const saved = [
    { id: 's-101' },
    { id: 's-109' },
    { id: 's-110' },
    { id: 's-102' },
  ];
  assert.deepStrictEqual(prepareSelection(cards, saved),
                         ['s-101', 's-110']);
});

ok('the old bug shape fails loud, not silent', () => {
  // pre-fix behavior would have dropped the id-less card entirely; the
  // fixed contract is that the ONLY way a checked card drops out is the
  // server returning no id for its position
  const cards = [{ checked: true, id: null }];
  assert.deepStrictEqual(prepareSelection(cards, [{}]), []);
  assert.deepStrictEqual(prepareSelection(cards, []), []);
});

ok('accepted (disabled) cards and empty panels behave', () => {
  assert.deepStrictEqual(prepareSelection([], []), []);
  assert.deepStrictEqual(
    prepareSelection([{ checked: false, id: 's-1' }], [{ id: 's-1' }]),
    []);
  assert.deepStrictEqual(prepareSelection(null, null), []);
});

if (failed) {
  console.error(`${failed} sessions-prepare test(s) failed`);
  process.exit(1);
}
console.log('all sessions-prepare tests passed');
