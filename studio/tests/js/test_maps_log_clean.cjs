/* Test for the ANSI leak in the worldgen log box (ticket 39b): the headless
   FMG runner colors its console output, the job log streamed the raw lines,
   and the <pre> rendered "[32m[39m"-style junk inline. The strip lives in
   Studio.makeLogBox.push — the one choke point every log box renders
   through — so this test pushes streamed-looking lines through the REAL
   makeLogBox and asserts the box never shows an escape sequence, while
   ordinary log text survives untouched. Ticket 69 adds the DOM-tail cap:
   the box keeps the same rotating window the server's job log does
   (jobs.py's LOG_CAP — read from the source here so the two cannot drift),
   where a chatty 30-minute week run used to append every line for the life
   of the watch. Zero dependencies:
     node studio/tests/js/test_maps_log_clean.cjs
   Run by run-tests.py's studio-js-log-clean suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const path = require('path');
const { boot, newPage } = require('./minidom.cjs');

// the cap is the server's log ring (jobs.py's LOG_CAP) — derived from the
// source so a bump there fails this test until the box moves with it
const LOG_CAP = Number(require('fs').readFileSync(
  path.join(__dirname, '..', '..', 'jobs.py'), 'utf8')
  .match(/^LOG_CAP\s*=\s*(\d+)/m)[1]);

const { S } = boot([]);
global.document = newPage();

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

function boxWith(lines) {
  const box = document.createElement('pre');
  const log = S.makeLogBox(box);
  log.open();
  lines.forEach((l) => log.push(l));
  return box.textContent;
}

async function main() {
  await ok('real CSI sequences (ESC[NNm) never reach the box', () => {
    const text = boxWith([
      'genesis \u001b[32mOK\u001b[39m in 4.2s',
      '\u001b[1m\u001b[34mregions:\u001b[0m 7 mapped'
    ]);
    assert.ok(!/\u001b/.test(text), 'no ESC byte may survive');
    assert.ok(!/\[[0-9;]*m/.test(text), 'no SGR tail may survive');
    assert.ok(text.includes('genesis OK in 4.2s'), 'the words survive');
    assert.ok(text.includes('regions: 7 mapped'), 'the words survive');
    return null;
  });

  await ok('stripped-ESC leftovers ("[32m") never reach the box either', () => {
    // the observed 2026-10-04 shape: the ESC byte invisible/lost, the
    // "[32m[39m" tails plain in the text
    const text = boxWith(['render [32mdone[39m, lakes [94m3[0m']);
    assert.ok(!/\[[0-9;]*m/.test(text), `got: ${JSON.stringify(text)}`);
    assert.ok(text.includes('render done, lakes 3'), 'the words survive');
    return null;
  });

  await ok('colored SGR with parameters (38;5;N) strip too', () => {
    const text = boxWith(['burg \u001b[38;5;196mProvis\u001b[0m placed']);
    assert.ok(!/\u001b|\[[0-9;]*m/.test(text), `got: ${JSON.stringify(text)}`);
    assert.ok(text.includes('burg Provis placed'));
    return null;
  });

  await ok('ordinary log text is untouched', () => {
    const text = boxWith([
      'import_fmg: 1 nation, 2 settlements',
      'summary written to world/geography.yaml',
      'array index [12] and a time 10:45 stay readable'
    ]);
    assert.strictEqual(text,
      'import_fmg: 1 nation, 2 settlements\n' +
      'summary written to world/geography.yaml\n' +
      'array index [12] and a time 10:45 stay readable\n');
    return null;
  });

  await ok('the maps tab log boxes are makeLogBox boxes (same sanitizer)', () => {
    // the pin is about the maps log specifically: its "streaming below"
    // boxes must route through the shared choke point, not a raw <pre>
    const src = require('fs').readFileSync(
      require('path').join(__dirname, '..', '..', 'static', 'js', 'maps.js'),
      'utf8');
    assert.ok(/S\.makeLogBox\(/.test(src),
      'maps.js creates its log boxes through Studio.makeLogBox');
    assert.ok(!/log-tail[^]*?textContent \+=/.test(src),
      'maps.js never writes log lines into a <pre> directly');
    return null;
  });

  await ok(`the DOM tail caps at the server log ring (${LOG_CAP} lines)`, () => {
    // ticket 69: a chatty run used to grow the box for the life of the
    // watch; the box now keeps the newest LOG_CAP lines, like jobs.py
    const box = document.createElement('pre');
    const log = S.makeLogBox(box);
    log.open();
    const total = LOG_CAP + 60;
    for (let i = 1; i <= total; i++) log.push('line ' + i);
    const shown = box.textContent.split('\n').filter((l) => l !== '');
    assert.strictEqual(shown.length, LOG_CAP,
      `${total} pushed lines must leave ${LOG_CAP} in the DOM, ` +
      `got ${shown.length}`);
    assert.strictEqual(shown[0], 'line ' + (total - LOG_CAP + 1),
      'the oldest lines are dropped — the newest window is what stays');
    assert.strictEqual(shown[shown.length - 1], 'line ' + total,
      'the newest line stays last');
    return null;
  });

  await ok('open() resets the cap window', () => {
    const box = document.createElement('pre');
    const log = S.makeLogBox(box);
    log.open();
    for (let i = 1; i <= LOG_CAP + 5; i++) log.push('line ' + i);
    log.open();
    assert.strictEqual(box.textContent, '', 'a new run starts empty');
    log.push('fresh');
    assert.strictEqual(box.textContent, 'fresh\n', 'and keeps appending');
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} log-clean test(s) failed`);
    process.exit(1);
  }
  console.log('all log-clean tests passed');
});
