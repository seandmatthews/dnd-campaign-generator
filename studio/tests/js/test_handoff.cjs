/* Tests for js/handoff.js — the rumor → knowledge-form prefill payload
   ("Tell the party", ticket 08). Zero dependencies:
     node studio/tests/js/test_handoff.cjs
   Run by run-tests.py's studio-js-handoff suite (skipped when node is
   absent). The payload shape IS the contract: {text, visibility,
   source_file, week} — the Knowledge tab's record form consumes exactly
   this, and submission rides the unchanged record → reveal flow. */

'use strict';
const assert = require('assert');
const handoff = require('../../static/js/handoff.js');

const CHRONICLES = [
  { week: 1, title: 'Bare Is Not Broken', file: 'simulation/chronicle/0001-bare-is-not-broken.md' },
  { week: 3, title: 'The Ash Crossing', file: 'simulation/chronicle/0003-the-ash-crossing.md' },
];

let failed = 0;
function ok(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); }
}

ok('payload shape is exactly text/visibility/source_file/week', () => {
  const p = handoff.rumorHandoff(
    { claim: 'The thorn has grown a man\u2019s height.', born_week: 1 },
    CHRONICLES);
  assert.deepStrictEqual(Object.keys(p).sort(),
    ['source_file', 'text', 'visibility', 'week']);
});

ok('visibility is public; week and chronicle ride along', () => {
  const p = handoff.rumorHandoff(
    { claim: 'The birds have gone quiet where the road runs.', born_week: 3 },
    CHRONICLES);
  assert.strictEqual(p.visibility, 'public');
  assert.strictEqual(p.week, 3);
  assert.strictEqual(p.source_file,
    'simulation/chronicle/0003-the-ash-crossing.md');
});

ok('text is the rumor verbatim (no rewriting, no truncation)', () => {
  const claim = 'Travellers swear the thorn along Greywatch Pass has grown ' +
    'a man\u2019s height since the thaw, and the birds have gone quiet where the road runs.';
  const p = handoff.rumorHandoff({ claim: claim, born_week: 1 }, CHRONICLES);
  assert.strictEqual(p.text, claim);
});

ok('the ledger outcome suffix is bookkeeping, not the rumor', () => {
  const p = handoff.rumorHandoff(
    { claim: 'The ferry poles doubled in price — succeeded (contested vs mint), margin +2',
      born_week: 2 },
    CHRONICLES);
  assert.strictEqual(p.text, 'The ferry poles doubled in price');
});

ok('no canon chronicle for the week means no fabricated source', () => {
  const p = handoff.rumorHandoff({ claim: 'A light burns in Deephold.', born_week: 2 },
    CHRONICLES);
  assert.strictEqual(p.source_file, '');
  assert.strictEqual(p.week, 2);
});

ok('week-less rumors still hand off text/visibility/source-less', () => {
  const p = handoff.rumorHandoff({ claim: 'The tally at the tower never stops.' }, []);
  assert.strictEqual(p.week, null);
  assert.strictEqual(p.source_file, '');
  assert.strictEqual(p.visibility, 'public');
});

ok('born/born_week/week all resolve as the week', () => {
  assert.strictEqual(handoff.rumorHandoff({ claim: 'x', born: 3 }, []).week, 3);
  assert.strictEqual(handoff.rumorHandoff({ claim: 'x', week: 4 }, []).week, 4);
  // born_week wins when several are present (the engine's name)
  assert.strictEqual(
    handoff.rumorHandoff({ claim: 'x', born_week: 1, week: 9 }, []).week, 1);
});

ok('a row with no claim hands off nothing (null, not a stub payload)', () => {
  assert.strictEqual(handoff.rumorHandoff({ born_week: 1 }, CHRONICLES), null);
  assert.strictEqual(handoff.rumorHandoff({ claim: '   ' }, CHRONICLES), null);
  assert.strictEqual(handoff.rumorHandoff(null, CHRONICLES), null);
});

ok('displayClaim mirrors the rumors board\u2019s strip', () => {
  assert.strictEqual(
    handoff.displayClaim('The gate was stormed — failed, margin -3'),
    'The gate was stormed');
  assert.strictEqual(handoff.displayClaim(null), '');
});

if (failed) { console.log(`${failed} test(s) failed`); process.exit(1); }
console.log('all handoff tests passed');
