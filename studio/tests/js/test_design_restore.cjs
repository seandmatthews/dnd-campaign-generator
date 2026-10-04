/* Tests for the design-result restore (found 2026-10-03): a completed
   design pass that outlived its panel — the DM checked another tab during
   the 10+ minute await — used to fill DETACHED nodes while the form reset
   to empty. The answer is now cached in sessionStorage when it arrives and
   restored onto the fresh Create form. These tests drive the real
   S.tabs.sim through the minidom shim: what is asserted is what the
   shipped IIFE actually renders. Zero dependencies:
     node studio/tests/js/test_design_restore.cjs
   Run by run-tests.py's studio-js-design-restore suite (skipped when node
   is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['sim']);
S.state.campaign = 'testvale';

const RESULT_KEY = 'studio-design-result';
const DESIGN = {
  premise: 'The toll bridge burned the night the wardens raised the toll.',
  factions: [
    { name: 'The Bridge Wardens', kind: 'order', goal: 'Rebuild the bridge',
      resources: { coin: 'low', muscle: 'high', legitimacy: 'medium' } },
    { name: 'The Ferry League', kind: 'guild', goal: 'Kill the toll',
      resources: { coin: 'high', muscle: 'low', legitimacy: 'low' } },
  ],
  projects: [
    { title: 'Ferry subsidies', faction: 'The Ferry League',
      objective: 'Undercut the toll', target: 3 },
  ],
};

function seedCache(over) {
  const doc = Object.assign({
    camp: 'testvale', at: Date.now(),
    design: DESIGN, places: ['Provis', 'Dunmore'],
  }, over || {});
  global.sessionStorage.setItem(RESULT_KEY, JSON.stringify(doc));
  return doc;
}

function clearCache() {
  global.sessionStorage.removeItem(RESULT_KEY);
}

async function renderSim() {
  global.document = newPage();
  installFetch([]); // define fetch; unmatched requests 404 (GET /simulation)
  const sec = document.querySelector('.tab-section[data-tab="sim"]');
  await S.tabs.sim(sec);
  await flush();
  return sec;
}

function createPanel(sec) {
  return sec.querySelectorAll('.panel').find(
    (p) => p.querySelector('.design-run'));
}

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

async function main() {
  await ok('a cached design refills the fresh form: premise, factions, places, extras', async () => {
    clearCache();
    seedCache();
    const sec = await renderSim();
    const panel = createPanel(sec);
    assert.ok(panel, 'the create form rendered (no simulation state)');
    assert.strictEqual(panel.querySelector('.sand-premise').value, DESIGN.premise);
    const names = [...panel.querySelectorAll('.f-name')].map((i) => i.value);
    assert.deepStrictEqual(names, DESIGN.factions.map((f) => f.name),
      'both designed factions are in the rows');
    assert.strictEqual(panel.querySelector('.sand-city').value, 'Provis',
      'the first named place leads as the city');
    assert.strictEqual(panel.querySelector('.sand-places').value, 'Dunmore',
      'the rest stay other-places');
    assert.ok(panel.querySelector('.design-result .proposal-events'),
      'the suggested projects block is restored too');
    const note = panel.querySelector('.design-restored');
    assert.ok(note, 'the restore note is shown');
    assert.ok(/restored/.test(note.textContent));
    assert.ok(!panel.querySelector('.design-regenerate').classList.contains('hidden'),
      'Regenerate is available again');
    return null;
  });

  await ok('no cache: the form starts empty, no note, Regenerate hidden', async () => {
    clearCache();
    const sec = await renderSim();
    const panel = createPanel(sec);
    assert.ok(!panel.querySelector('.sand-premise').value,
      'no premise without a cached design');
    assert.strictEqual(panel.querySelectorAll('.f-name').length, 2,
      'exactly the two default faction rows');
    assert.strictEqual(panel.querySelector('.design-restored'), null);
    assert.ok(panel.querySelector('.design-regenerate').classList.contains('hidden'));
    return null;
  });

  await ok('a foreign campaign\'s cache and a stale one are both ignored', async () => {
    clearCache();
    seedCache({ camp: 'some-other-campaign' });
    let sec = await renderSim();
    assert.strictEqual(createPanel(sec).querySelector('.design-restored'), null,
      'another campaign\'s design must not fill this form');

    clearCache();
    seedCache({ at: Date.now() - S.DESIGN_STALE_MS - 1000 });
    sec = await renderSim();
    assert.strictEqual(createPanel(sec).querySelector('.design-restored'), null,
      'past the believable window the cache must not self-apply');
    return null;
  });

  await ok('garbage in the cache is refused, not thrown', async () => {
    clearCache();
    global.sessionStorage.setItem(RESULT_KEY, 'garbage{');
    let sec = await renderSim();
    assert.strictEqual(createPanel(sec).querySelector('.design-restored'), null);

    // right camp, wrong shape (no factions): not restorable either
    global.sessionStorage.setItem(RESULT_KEY,
      JSON.stringify({ camp: 'testvale', at: Date.now(), design: {} }));
    sec = await renderSim();
    assert.strictEqual(createPanel(sec).querySelector('.design-restored'), null);
    return null;
  });

  await ok('a completed pass saves its answer; Create consumes the cache', async () => {
    clearCache();
    global.document = newPage();
    const log = installFetch([
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ __status: 404, error: 'none' }) },
      { method: 'POST', match: (u) => /\/simulation\/design$/.test(u),
        reply: () => ({ design: DESIGN, notes: ['grounded on 2 places'] }) },
      { method: 'POST', match: (u) => /\/simulation\/state$/.test(u),
        reply: () => ({ ok: true }) },
    ]);
    const sec = document.querySelector('.tab-section[data-tab="sim"]');
    await S.tabs.sim(sec);
    await flush();
    let panel = createPanel(sec);
    panel.querySelector('.design-premise').value = 'a tension';
    panel.querySelector('.design-places').value = 'Provis, Dunmore';
    panel.querySelector('.design-run').click();
    await flush();
    const saved = JSON.parse(global.sessionStorage.getItem(RESULT_KEY));
    assert.ok(saved && saved.design && saved.design.factions.length === 2,
      'the answer was cached when it arrived');
    assert.strictEqual(saved.camp, 'testvale');
    assert.strictEqual(panel.querySelector('.sand-city').value, 'Provis',
      'the live panel is filled as before');

    // Create consumes it: the design is canon now, the cache must go
    panel = createPanel(sec);
    panel.querySelector('.sand-city').value = 'Provis';
    // minidom does not reflect the season input's value attribute (the
    // create handler reads it before the city check)
    panel.querySelector('.sand-season').value = 'late autumn';
    panel.querySelector('.sand-create').click();
    await flush();
    assert.strictEqual(global.sessionStorage.getItem(RESULT_KEY), null,
      'a created sandbox clears the design cache');
    assert.ok(log.some((e) => e.method === 'POST' &&
      /\/simulation\/state$/.test(e.url)));
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} design-restore test(s) failed`);
    process.exit(1);
  }
  console.log('all design-restore tests passed');
});
