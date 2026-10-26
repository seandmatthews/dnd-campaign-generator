/* Tests for the rumor → knowledge-form handoff render (ticket 69): the
   factPrefill payload is consumed by the Knowledge tab render itself,
   BEFORE anything that can throw. The delete used to live in
   applyFactPrefill, so a handoff render that failed before reaching it
   left the payload parked — and a much later plain visit to the tab
   self-filled the record form with a stale rumor. These load the real
   knowledge.js through the minidom shim and drive S.tabs.knowledge
   against a stubbed fetch. Zero dependencies:
     node studio/tests/js/test_fact_prefill.cjs
   Run by run-tests.py's studio-js-fact-prefill suite (skipped when node
   is absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['knowledge']);
S.state.campaign = 'ruravaq';

const PREFILL = {
  text: 'The bridge was insured twice over.',
  visibility: 'public',
  source_file: 'simulation/chronicle/0002-the-ash-crossing.md',
  week: 2
};

function resetFetch() {
  installFetch([
    { method: 'GET', match: (u) => u.includes('/simulation'),
      reply: () => ({ clock: { week: 3, season: 'early spring' },
        premise: 'the toll bridge burned',
        factions: [{ id: 'wardens', name: 'The Bridge Wardens' }] }) },
    { method: 'GET', match: (u) => u.includes('/knowledge'),
      reply: () => ({ facts: [] }) },
    { method: 'GET', match: (u) => u.includes('/disclosure'),
      reply: () => ({ revealed: [] }) },
    { method: 'GET', match: (u) => u.includes('/briefing'),
      reply: () => ({ total: 0, omitted: 0, shown: 0, items: [],
        decisions: [] }) },
  ]);
}

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

async function renderTab() {
  global.document = newPage();
  const sec = document.querySelector('.tab-section[data-tab="knowledge"]');
  await S.tabs.knowledge(sec);
  await flush();
  return sec;
}

async function main() {
  await ok('a parked prefill fills the record form and is consumed', async () => {
    resetFetch();
    S.state.factPrefill = Object.assign({}, PREFILL);
    const sec = await renderTab();
    const form = sec.querySelector('.fact-form');
    assert.ok(form, 'the record form renders');
    assert.strictEqual(form.querySelector('.fact-text').value, PREFILL.text);
    assert.strictEqual(form.querySelector('.fact-source').value,
      PREFILL.source_file);
    assert.strictEqual(form.querySelector('.fact-week').value, '2');
    assert.strictEqual(sec.querySelector('.vis-mode').value, 'public');
    assert.ok(sec.querySelector('.fact-prefill-note'),
      'the panel says what was prefilled');
    assert.strictEqual(S.state.factPrefill, undefined,
      'the payload is consumed, not parked');
    return null;
  });

  await ok('a later plain visit finds no stale prefill', async () => {
    const sec = await renderTab();
    const form = sec.querySelector('.fact-form');
    assert.strictEqual(form.querySelector('.fact-text').value, '',
      'the form stays empty on a plain visit');
    assert.strictEqual(sec.querySelector('.fact-prefill-note'), null,
      'no prefill note without a prefill');
    assert.strictEqual(S.state.factPrefill, undefined);
    return null;
  });

  await ok('a failed handoff render still consumes the prefill', async () => {
    resetFetch();
    S.state.factPrefill = Object.assign({}, PREFILL);
    // the handoff render dies building the tab root — before any panel,
    // the exact "render fails" shape from the ticket. The payload must
    // already be consumed: the OLD code deleted it only inside
    // applyFactPrefill, which a render like this never reaches.
    const realEl = S.el;
    S.el = (html) => {
      if (String(html).indexOf('tab-root') !== -1) {
        throw new Error('simulated render crash');
      }
      return realEl(html);
    };
    try {
      await assert.rejects(renderTab(), /simulated render crash/);
    } finally {
      S.el = realEl;
    }
    assert.strictEqual(S.state.factPrefill, undefined,
      'a failed render must not leave the prefill parked for later');
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} fact-prefill test(s) failed`);
    process.exit(1);
  }
  console.log('all fact-prefill tests passed');
});
