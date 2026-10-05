/* Tests for the design-result restore (ticket 42): the pass is a queued
   job now, and its JOB RECORD is the durable copy — a reload mid-run (or
   a pass finished while the DM was on another tab) restores the prefill
   from the campaign-jobs snapshot's last_finished instead of the
   sessionStorage cache the 2026-10-03 fix used. These tests drive the
   real S.tabs.sim through the minidom shim: what is asserted is what the
   shipped IIFE actually renders. Zero dependencies:
     node studio/tests/js/test_design_restore.cjs
   Run by run-tests.py's studio-js-design-restore suite (skipped when node
   is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['sim']);
S.state.campaign = 'testvale';
// pollJob (the job watch the design now runs through) reads the bare
// global StudioLogTail; the UMD wrapper only sets that in a browser
// (module.exports wins under node), so seed it here
global.StudioLogTail = require('../../static/js/logtail.js');
// the maps tab is not loaded here, but sim.js builds artifact nodes via
// new Image() on some paths — node has none
global.Image = class Image { constructor() { this.alt = ''; } };

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

const RESULT = { design: DESIGN, notes: [], usage: { calls: 1 },
                 places: ['Provis', 'Dunmore'] };

function doneRecord(over) {
  return Object.assign({
    id: 'd1', kind: 'sim-design', campaign: 'testvale',
    status: 'done', log_tail: [],
    started: new Date().toISOString(),
    result: RESULT, error: null,
  }, over || {});
}

function jobsSnap(over) {
  return Object.assign({
    campaign: 'testvale', running: null, queued: [],
    running_estimate: null, last_finished: {},
  }, over || {});
}

async function renderSim(snapshot) {
  global.document = newPage();
  installFetch([
    { method: 'GET', match: (u) => /\/jobs$/.test(u),
      reply: () => snapshot || jobsSnap() },
    // GET /simulation 404s: no sandbox yet — that is the create panel's state
  ]);
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
  await ok('a finished pass\'s record refills the fresh form: premise, factions, places, extras', async () => {
    const sec = await renderSim(jobsSnap({
      last_finished: { 'sim-design': doneRecord() } }));
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

  await ok('no finished record: the form starts empty, no note, Regenerate hidden', async () => {
    const sec = await renderSim();
    const panel = createPanel(sec);
    assert.ok(!panel.querySelector('.sand-premise').value,
      'no premise without a finished design');
    assert.strictEqual(panel.querySelectorAll('.f-name').length, 2,
      'exactly the two default faction rows');
    assert.strictEqual(panel.querySelector('.design-restored'), null);
    assert.ok(panel.querySelector('.design-regenerate').classList.contains('hidden'));
    return null;
  });

  await ok('a stale record and a shapeless one are both ignored', async () => {
    let sec = await renderSim(jobsSnap({
      last_finished: { 'sim-design': doneRecord({
        started: new Date(Date.now() - S.DESIGN_STALE_MS - 1000)
          .toISOString() }) } }));
    assert.strictEqual(createPanel(sec).querySelector('.design-restored'), null,
      'past the believable window the record must not self-apply');

    sec = await renderSim(jobsSnap({
      last_finished: { 'sim-design': doneRecord({ result: { design: {} } }) } }));
    assert.strictEqual(createPanel(sec).querySelector('.design-restored'), null,
      'a record without factions is not restorable');

    sec = await renderSim(jobsSnap({
      last_finished: { 'sim-design': doneRecord({ status: 'error' }) } }));
    assert.strictEqual(createPanel(sec).querySelector('.design-restored'), null,
      'a failed pass has nothing to restore');
    return null;
  });

  await ok('a still queued/running pass says so instead of pretending the form is live', async () => {
    const sec = await renderSim(jobsSnap({
      running: { id: 'j9', kind: 'sim-design', status: 'running' } }));
    const panel = createPanel(sec);
    const note = panel.querySelector('.design-inflight');
    assert.ok(note, 'the inflight note renders');
    assert.ok(/already running/.test(note.textContent) &&
              /Reloading is safe/.test(note.textContent),
      'it names the state and the reload safety: ' +
      JSON.stringify(note.textContent));
    assert.strictEqual(panel.querySelector('.design-restored'), null,
      'nothing is restored while the pass runs');
    return null;
  });

  await ok('a completed pass fills the form through the job; Create still writes the sandbox', async () => {
    global.document = newPage();
    const log = installFetch([
      { method: 'GET', match: (u) => /\/jobs$/.test(u),
        reply: () => jobsSnap() },
      { method: 'GET', match: (u) => /\/simulation$/.test(u),
        reply: () => ({ __status: 404, error: 'none' }) },
      { method: 'POST', match: (u) => /\/simulation\/design$/.test(u),
        reply: () => ({ job_id: 'dj1', queued: false, queue_position: 0 }) },
      { method: 'GET', match: (u) => /\/api\/jobs\/dj1$/.test(u),
        reply: () => doneRecord({ id: 'dj1' }) },
      { method: 'POST', match: (u) => /\/simulation\/state$/.test(u),
        reply: () => ({ ok: true }) },
    ]);
    const sec = document.querySelector('.tab-section[data-tab="sim"]');
    await S.tabs.sim(sec);
    await flush();
    const panel = createPanel(sec);
    panel.querySelector('.design-premise').value = 'a tension';
    panel.querySelector('.design-places').value = 'Provis, Dunmore';
    panel.querySelector('.design-run').click();
    await flush(10);
    assert.strictEqual(panel.querySelector('.sand-premise').value,
      DESIGN.premise, 'the finished job\'s result filled the live panel');
    assert.strictEqual(panel.querySelector('.sand-city').value, 'Provis',
      'the recorded places filled the city field');
    const posted = log.find((e) => e.method === 'POST' &&
      /\/simulation\/design$/.test(e.url));
    assert.deepStrictEqual(posted.body,
      { premise: 'a tension', places: ['Provis', 'Dunmore'] },
      'the submission is the queued job\'s body');

    // Create writes the sandbox through the unchanged free path
    panel.querySelector('.sand-city').value = 'Provis';
    // minidom does not reflect the season input's value attribute (the
    // create handler reads it before the city check)
    panel.querySelector('.sand-season').value = 'late autumn';
    panel.querySelector('.sand-create').click();
    await flush();
    assert.ok(log.some((e) => e.method === 'POST' &&
      /\/simulation\/state$/.test(e.url)), 'the create POST went through');
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
