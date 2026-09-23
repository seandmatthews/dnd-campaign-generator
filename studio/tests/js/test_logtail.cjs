/* Node test for logtail.js — the rotating job-log merge (no browser).
   Run:  node studio/tests/js/test_logtail.cjs   (also run by run-tests.py) */

const assert = require('assert');
const { mergeLogTail } = require('../../static/js/logtail.js');

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ${name}: OK`); }
  catch (e) { failures += 1; console.log(`  ${name}: FAIL — ${e.message}`); }
}

check('first poll emits everything', () => {
  const r = mergeLogTail(['a', 'b', 'c'], { seen: 0, last: null });
  assert.deepStrictEqual(r.lines, ['a', 'b', 'c']);
  assert.deepStrictEqual(r.cursor, { seen: 3, last: 'c' });
});

check('quiet poll emits nothing', () => {
  const r = mergeLogTail(['a', 'b', 'c'], { seen: 3, last: 'c' });
  assert.deepStrictEqual(r.lines, []);
  assert.deepStrictEqual(r.cursor, { seen: 3, last: 'c' });
});

check('full window, no rotation, emits nothing (the old freeze point)', () => {
  const CAP = 40;
  const full = Array.from({ length: CAP }, (_, i) => `line-${i}`);
  const r1 = mergeLogTail(full, { seen: 0, last: null });
  assert.strictEqual(r1.lines.length, CAP);
  const r2 = mergeLogTail(full, r1.cursor);
  assert.deepStrictEqual(r2.lines, [], 'pinned length must not mean new lines');
});

check('rotation by one emits exactly the newcomer', () => {
  const r = mergeLogTail(['b', 'c', 'd'], { seen: 3, last: 'c' });
  assert.deepStrictEqual(r.lines, ['d']);
  assert.deepStrictEqual(r.cursor, { seen: 3, last: 'd' });
});

check('sustained rotation streams every line (30-min week scenario)', () => {
  let cursor = { seen: 0, last: null };
  const CAP = 40;
  const emitted = [];
  let next = 0;
  // 200 lines arriving one per poll through a 40-line window (201st poll
  // sees the last line land)
  for (let step = 0; step < 201; step++) {
    const window = [];
    // window = the last min(CAP, produced) of lines 0..next
    for (let i = Math.max(0, next - CAP); i < next; i++) window.push(`L${i}`);
    const r = mergeLogTail(window, cursor);
    r.lines.forEach((l) => emitted.push(l));
    cursor = r.cursor;
    next += 1;
  }
  assert.strictEqual(emitted.length, 200, `every line must arrive: ${emitted.length}`);
  assert.strictEqual(emitted[0], 'L0');
  assert.strictEqual(emitted[199], 'L199');
});

check('anchor slid out of the window replays it (all-new window)', () => {
  const r = mergeLogTail(['n1', 'n2', 'n3'], { seen: 3, last: 'gone' });
  assert.deepStrictEqual(r.lines, ['n1', 'n2', 'n3']);
});

check('shrinking window (job log reset) replays safely', () => {
  const r = mergeLogTail(['x'], { seen: 10, last: 'old' });
  assert.deepStrictEqual(r.lines, ['x']);
});

if (failures) {
  console.log(`${failures} logtail test(s) failed`);
  process.exit(1);
}
console.log('all logtail tests passed');
