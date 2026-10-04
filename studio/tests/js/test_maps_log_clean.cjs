/* Test for the ANSI leak in the worldgen log box (ticket 39b): the headless
   FMG runner colors its console output, the job log streamed the raw lines,
   and the <pre> rendered "[32m[39m"-style junk inline. The strip lives in
   Studio.makeLogBox.push — the one choke point every log box renders
   through — so this test pushes streamed-looking lines through the REAL
   makeLogBox and asserts the box never shows an escape sequence, while
   ordinary log text survives untouched. Zero dependencies:
     node studio/tests/js/test_maps_log_clean.cjs
   Run by run-tests.py's studio-js-log-clean suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const { boot, newPage } = require('./minidom.cjs');

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
}

main().then(() => {
  if (failed) {
    console.error(`${failed} log-clean test(s) failed`);
    process.exit(1);
  }
  console.log('all log-clean tests passed');
});
