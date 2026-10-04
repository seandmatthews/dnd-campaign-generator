/* DOM tests for the copy-claims fixes (ticket 39c/39d):
     39c — the Generate-backstory panel claimed "One metered LLM call";
           the run meter logged ~3 calls (generation + audit, a retry can
           add one). The panel must state the honest shape.
     39d — accept/discard/stage confirmations reused the spend header
           "This costs real credits" although accepting spends nothing.
           confirmRun(message, title) now lets the caller name the act;
           the default stays the spend header for the LLM runners.
   Both render the REAL panels through minidom. Zero dependencies:
     node studio/tests/js/test_copy_claims.cjs
   Run by run-tests.py's studio-js-copy-claims suite (skipped when node is
   absent). */

'use strict';
const assert = require('assert');
const { boot, newPage, installFetch, flush } = require('./minidom.cjs');

const { S } = boot(['world']);
S.state.campaign = 'testvale';

let failed = 0;
function ok(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => console.log(`  ${name}: OK`))
    .catch((e) => { failed += 1; console.log(`  ${name}: FAIL ${e.message}`); });
}

async function renderWorld() {
  global.document = newPage();
  installFetch([
    { method: 'GET', match: (u) => /\/geography$/.test(u),
      reply: () => ({ meta: { seed: '12345' }, nations: [], burgs: [] }) },
    { method: 'GET', match: (u) => /\/lore$/.test(u),
      reply: () => ({ timeline: [], entities: [], style: {} }) },
    { method: 'GET', match: (u) => /\/quests$/.test(u),
      reply: () => ({ quests: [], findings: [] }) },
    { method: 'GET', match: (u) => /\/lore\/pages\/missing$/.test(u),
      reply: () => ({ targets: [] }) },
    { method: 'GET', match: (u) => /\/lore\/path$/.test(u),
      reply: () => ({ path: 'C:/nowhere/lore', obsidian_uri: 'obsidian://x' }) }
  ]);
  const sec = document.querySelector('.tab-section[data-tab="world"]');
  await S.tabs.world(sec);
  await flush();
  return sec;
}

function backstoryPanel(sec) {
  return sec.querySelectorAll('.panel').find((p) => {
    const t = p.querySelector('.panel-title');
    return t && t.textContent === 'Generate backstory';
  });
}

async function main() {
  await ok('39c: the backstory panel no longer claims one metered call', async () => {
    const sec = await renderWorld();
    const note = backstoryPanel(sec).querySelector('.panel-note').textContent;
    assert.ok(/couple of metered LLM passes/.test(note),
      `note reads: ${note}`);
    assert.ok(!/One metered LLM call/.test(note),
      'the false claim must be gone');
    assert.ok(/a few minutes/.test(note), 'the duration estimate stays');
    return null;
  });

  await ok('39d: an accept confirm states it becomes canon, not that it costs', async () => {
    global.document = newPage();
    let modalTitle = null;
    const p = S.confirmRun('Accepting publishes the proposed week — exactly once.',
      'This becomes canon — no credits are spent');
    modalTitle = document.querySelector('.modal-title').textContent;
    document.querySelector('[data-act="cancel"]').click();
    await p;
    assert.strictEqual(modalTitle, 'This becomes canon — no credits are spent');
    assert.ok(!/costs real credits/.test(modalTitle));
    return null;
  });

  await ok('39d: the default header is still the spend header (LLM runners)', async () => {
    global.document = newPage();
    const p = S.confirmRun('Generate pending backstory entries with the LLM?');
    const modalTitle = document.querySelector('.modal-title').textContent;
    document.querySelector('[data-act="cancel"]').click();
    await p;
    assert.strictEqual(modalTitle, 'This costs real credits');
    return null;
  });

  await ok('39d: the non-spending call sites pass their own headers (source pin)', () => {
    const sim = require('fs').readFileSync(
      require('path').join(__dirname, '..', '..', 'static', 'js', 'sim.js'),
      'utf8');
    assert.ok(sim.includes("'This becomes canon — no credits are spent'"),
      'proposal accept names the act');
    assert.ok(sim.includes("'Nothing was ever written'"),
      'discards do not claim a spend');
    assert.ok(sim.includes("'Staging is free — accept later'"),
      'project staging does not claim a spend');
    // every remaining bare confirmRun( in sim.js keeps the spend default,
    // which is correct — they are the forecast/week/goal-review runners
    return null;
  });
}

main().then(() => {
  if (failed) {
    console.error(`${failed} copy-claims test(s) failed`);
    process.exit(1);
  }
  console.log('all copy-claims tests passed');
});
