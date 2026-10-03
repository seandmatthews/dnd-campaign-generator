/* Tests for the next-session prep confirmation line (the missing-feedback
   bug found 2026-10-03): the persistent panel line's copy, with the file
   path positioned so the panel render can split around it and link just
   the path. Zero dependencies:
     node studio/tests/js/test_prep_feedback.cjs
   Run by run-tests.py's studio-js-prep suite (skipped when node is
   absent). */

'use strict';
const path = require('path');
const assert = require('assert');

// knowledge.js is a plain IIFE over window.Studio — nothing DOM at load
global.window = { Studio: { tabs: {} } };
require(path.join(__dirname, '..', '..', 'static', 'js', 'knowledge.js'));
const S = global.window.Studio;

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

const rec = {
  camp: 'brineharrow-coast',
  file: 'lore/prep/2026-10-03-next-session.md',
  committed: true,
  at: new Date('2026-10-03T14:03:00').getTime()
};

ok('the line leads with the verbatim file path so it can be linked', () => {
  const line = S.prepStoredLine(rec);
  assert.ok(line.startsWith('Prep written: ' + rec.file + ' '),
    'the path must sit verbatim right after the label: ' + line);
  assert.ok(line.indexOf(rec.file) === 'Prep written: '.length,
    'exactly one occurrence, at the split point');
});

ok('a committed snapshot says so; a failed one says NOT committed', () => {
  assert.ok(/— snapshot committed/.test(S.prepStoredLine(rec)));
  assert.ok(/— snapshot NOT committed/.test(
    S.prepStoredLine(Object.assign({}, rec, { committed: false }))));
});

ok('the time-of-day renders; a missing timestamp degrades quietly', () => {
  assert.ok(/snapshot committed at 14:03/.test(S.prepStoredLine(rec)),
    S.prepStoredLine(rec));
  const noTime = S.prepStoredLine(Object.assign({}, rec, { at: null }));
  assert.ok(!/committed at/.test(noTime), 'no clock, no timestamp: ' + noTime);
});

ok('the next-free-name promise stays on the line', () => {
  assert.ok(/next free -N name, never overwrites/.test(S.prepStoredLine(rec)));
});

ok('empty records produce an empty line (no bogus panel content)', () => {
  assert.strictEqual(S.prepStoredLine(null), '');
  assert.strictEqual(S.prepStoredLine({}), '');
  assert.strictEqual(S.prepStoredLine({ camp: 'x', file: '' }), '');
});

if (failed) {
  console.log(`\n${failed} test(s) FAILED`);
  process.exit(1);
}
console.log('\nall prep-feedback tests passed');
