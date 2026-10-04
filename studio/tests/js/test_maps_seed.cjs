/* Test for the empty-seed silent no-op (found 2026-10-03): "Generate world"
   with a blank seed used to return without any message — the click read
   exactly like a dead button and cost several confused ones. It must now
   say what is missing, in the form's error box and as a toast, and a real
   seed must still submit the job exactly as before. Zero dependencies:
     node studio/tests/js/test_maps_seed.cjs
   Run by run-tests.py's studio-js-maps-seed suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['maps']);
S.state.campaign = 'testvale';

// the maps tab builds hero/artifact images through new Image() — node has
// none, and the tests never need one to actually load
global.Image = class Image { constructor() { this.alt = ''; } };
// pollJob reads the bare global StudioLogTail; the UMD wrapper only sets
// that in a browser (module.exports wins under node), so seed it here
global.StudioLogTail = require('../../static/js/logtail.js');

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

async function renderMaps() {
  global.document = newPage();
  const sec = document.querySelector('.tab-section[data-tab="maps"]');
  await S.tabs.maps(sec);
  await flush();
  return sec;
}

function toastText() {
  const host = document.querySelector('#toasts');
  return host ? host.textContent : '';
}

async function main() {
  await ok('a blank seed refuses loudly: the form names it and a toast fires', async () => {
    const sec = await renderMaps();
    const seedIn = sec.querySelector('.world-seed');
    assert.ok(seedIn, 'the generate-world form rendered');
    seedIn.value = '   ';
    const form = seedIn.closest('form');
    form.fire('submit', { preventDefault: () => {} });
    await flush();
    const errBox = seedIn.closest('.form-block').querySelector('.form-error');
    assert.ok(/seed/i.test(errBox.textContent),
      'the error box names the missing seed: ' + JSON.stringify(errBox.textContent));
    assert.ok(/seed/i.test(toastText()), 'a toast fired too');
    return null;
  });

  await ok('the seed that made the current world stays visible (ticket 39e)', async () => {
    // geography.yaml records the seed, but after a reload the Seed input
    // is empty and no surface named what made the world — the Maps panel
    // now shows it next to the generate form
    installFetch([
      { method: 'GET', match: (u) => /\/geography$/.test(u),
        reply: () => ({ meta: { seed: '12345' }, nations: [],
                        major_settlements: [] }) }
    ]);
    const sec = await renderMaps();
    const note = sec.querySelector('.current-seed-note');
    assert.ok(note, 'the seed note rendered');
    assert.ok(note.textContent.includes('12345'),
      'the note names the seed: ' + JSON.stringify(note.textContent));
    return null;
  });

  await ok('no seed note when there is no world yet', async () => {
    installFetch([
      { method: 'GET', match: (u) => /\/geography$/.test(u),
        reply: () => ({ __status: 404, error: 'none' }) }
    ]);
    const sec = await renderMaps();
    assert.strictEqual(sec.querySelector('.current-seed-note'), null,
      'a campaign with no geography has no seed to show');
    return null;
  });

  await ok('a real seed still submits the job exactly as before', async () => {
    installFetch([
      { method: 'GET', match: (u) => /\/geography$/.test(u),
        reply: () => ({ __status: 404, error: 'none' }) },
      { method: 'POST', match: (u) => /\/world$/.test(u),
        reply: (req) => {
          assert.strictEqual(req.body.seed, '1863');
          return { job_id: 'j1' };
        } },
      { method: 'GET', match: (u) => /\/api\/jobs\/j1$/.test(u),
        reply: () => ({ id: 'j1', status: 'done', log_tail: [],
          result: { nations: 12, settlements: 362 } }) },
    ]);
    const sec = await renderMaps();
    const seedIn = sec.querySelector('.world-seed');
    seedIn.value = '1863';
    seedIn.closest('form').fire('submit', { preventDefault: () => {} });
    await flush(10);
    assert.ok(/World generated/.test(toastText()),
      'the done toast fired: ' + JSON.stringify(toastText()));
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} maps-seed test(s) failed`);
    process.exit(1);
  }
  console.log('all maps-seed tests passed');
});
